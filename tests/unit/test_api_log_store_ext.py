"""modules/management/api_log_store 补充测试：读写/筛选/统计/清理/边界"""
import sqlite3
import threading
import time
from unittest.mock import MagicMock

import pytest

import modules.management.api_log_store as store_mod
from modules.management.api_log_store import ApiLogStore, _BATCH_SIZE, _db_path


@pytest.fixture
def store_factory(tmp_path):
    stores = []

    def _make(name="api_log.db"):
        s = ApiLogStore(path=str(tmp_path / name))
        stores.append(s)
        return s

    yield _make
    for s in stores:
        s.stop()


# ── 路径 / 建库迁移 ─────────────────────────────────────────────────────────

def test_db_path_default():
    assert _db_path().endswith("data/api_log.db")


def test_init_migrates_old_schema(tmp_path):
    """旧库缺 request_body/response_body 列 → ALTER 补齐"""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE api_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
        "time TEXT NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL, "
        "status INTEGER NOT NULL, ms REAL NOT NULL DEFAULT 0)"
    )
    conn.commit()
    conn.close()

    store = ApiLogStore(path=str(db))
    store.add("GET", "/x", 200, request_body="req", response_body="resp")
    store.flush()
    store.stop()

    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(api_requests)")}
    conn.close()
    assert "request_body" in cols
    assert "response_body" in cols
    assert store.query(limit=10)[0]["request_body"] == "req"


# ── stop() 各分支 ───────────────────────────────────────────────────────────

def test_stop_with_alive_thread_flushes_then_closes(tmp_path):
    store = ApiLogStore(path=str(tmp_path / "x.db"))
    store.add("GET", "/x", 200)
    store.stop()  # 线程存活 → join；关闭前 flush
    assert store._conn is None


def test_stop_flush_failure_tolerated(tmp_path):
    store = ApiLogStore(path=str(tmp_path / "x.db"))

    def boom():
        raise RuntimeError("flush fail")

    store.flush = boom
    store.stop()  # flush 抛错 → except 吞掉


def test_stop_close_failure_tolerated(tmp_path):
    store = ApiLogStore(path=str(tmp_path / "x.db"))
    store._conn = MagicMock()
    store._conn.close.side_effect = RuntimeError("close fail")
    store.stop()


def test_stop_thread_not_alive(tmp_path):
    store = ApiLogStore(path=str(tmp_path / "x.db"))
    store._thread = threading.Thread(target=lambda: None)  # 未启动 → not alive
    store.stop()


def test_stop_no_thread_attr(tmp_path):
    store = ApiLogStore(path=str(tmp_path / "x.db"))
    del store._thread
    store.stop()


def test_stop_conn_none(tmp_path):
    store = ApiLogStore(path=str(tmp_path / "x.db"))
    store._conn = None
    store.stop()


# ── get_instance 单例 ───────────────────────────────────────────────────────

def test_get_instance_singleton(monkeypatch, tmp_path):
    monkeypatch.setattr(store_mod, "_db_path", lambda: str(tmp_path / "inst.db"))
    monkeypatch.setattr(ApiLogStore, "_instance", None)
    a = ApiLogStore.get_instance()
    b = ApiLogStore.get_instance()
    assert a is b
    a.stop()


def test_get_instance_inner_recheck(monkeypatch, tmp_path):
    """并发：外层判 None 后、加锁期间已被其它线程创建 → 复用已有实例"""
    monkeypatch.setattr(store_mod, "_db_path", lambda: str(tmp_path / "race.db"))
    saved = ApiLogStore._instance
    saved_lock = ApiLogStore._lock
    try:
        ApiLogStore._instance = None
        entered = threading.Event()
        release = threading.Event()

        class BlockingLock:
            def __enter__(self):
                entered.set()
                assert release.wait(5), "release timeout"
                return self

            def __exit__(self, *exc):
                return False

        ApiLogStore._lock = BlockingLock()
        result = {}

        def worker():
            result["store"] = ApiLogStore.get_instance()

        t = threading.Thread(target=worker)
        t.start()
        assert entered.wait(5), "worker did not enter lock"
        ApiLogStore._instance = object()  # 模拟另一线程已先完成创建
        release.set()
        t.join(5)
        assert not t.is_alive()
        assert result["store"] is ApiLogStore._instance
    finally:
        ApiLogStore._instance = saved
        ApiLogStore._lock = saved_lock


# ── 写入：批量自动 flush / flush 失败回填 ────────────────────────────────────

def test_add_triggers_batch_flush(store_factory):
    store = store_factory()
    for i in range(_BATCH_SIZE):
        store.add("GET", f"/x{i}", 200)
    assert len(store.query(limit=1000)) == _BATCH_SIZE


def test_flush_locked_empty_noop(store_factory):
    store = store_factory()
    store._flush_locked()  # 队列空 → 直接返回


def test_flush_failure_requeue(tmp_path):
    store = ApiLogStore(path=str(tmp_path / "r.db"))
    store._conn = MagicMock()
    store._conn.executemany.side_effect = RuntimeError("insert fail")
    store.add("GET", "/x", 200)
    store.flush()
    assert len(store._queue) == 1  # 失败回填，避免丢日志
    store.stop()


def test_flush_loop_error_tolerance(tmp_path):
    """后台 flush 线程在写库异常时吞掉错误，不退出循环"""
    store = ApiLogStore(path=str(tmp_path / "loop.db"))
    store._conn = MagicMock()
    store._conn.executemany.side_effect = RuntimeError("db broken")
    store._queue.append(("t", "12:00:00", "GET", "/x", 200, 0.0, "", ""))
    time.sleep(1.8)  # flush_loop 每 1s 触发一次，等待至少一次失败 flush
    store.stop()


# ── 查询 / 筛选 / 统计 ──────────────────────────────────────────────────────

def test_query_and_filters(store_factory):
    store = store_factory()
    store.add("GET", "/health", 200, 5.0, "req-a", "resp-a")
    store.add("POST", "/api/run", 500, 9.0, "req-b", "resp-b")
    store.add("GET", "/other", 404, 1.0, "req-c", "resp-c")
    store.flush()

    assert len(store.query(limit=50)) == 3
    assert len(store.query(method="GET", limit=50)) == 2
    assert len(store.query(path="health", limit=50)) == 1
    assert len(store.query(status=500, limit=50)) == 1
    assert len(store.query(since_hours=1, limit=50)) == 3
    assert len(
        store.query(method="GET", path="health", status=200, limit=50, offset=0, since_hours=0.5)
    ) == 1
    # 分页
    assert len(store.query(limit=2, offset=0)) == 2
    assert len(store.query(limit=2, offset=2)) == 1
    # include_body=False → 不返回 body 字段
    rows = store.query(method="GET", limit=50, include_body=False)
    assert "request_body" not in rows[0]
    assert "response_body" not in rows[0]


def test_count_filters(store_factory):
    store = store_factory()
    store.add("GET", "/health", 200)
    store.add("GET", "/other", 404)
    store.add("POST", "/api", 500)
    store.flush()
    assert store.count() == 3
    assert store.count(method="GET") == 2
    assert store.count(path="api") == 1
    assert store.count(status=404) == 1
    assert store.count(since_hours=1) == 3
    assert store.count(method="GET", path="health", status=200, since_hours=0.5) == 1


def test_stats_since(store_factory):
    store = store_factory()
    store.add("GET", "/a", 200, 10.0)
    store.add("POST", "/b", 500, 30.0)
    store.add("GET", "/c", 404, 5.0)
    store.flush()
    s = store.stats(since_hours=1)
    assert s["total"] == 3
    assert s["avg_ms"] == 15.0
    assert s["by_method"]["GET"] == 2
    assert s["by_status"]["404"] == 1
    s0 = store.stats(since_hours=0)
    assert s0["total"] == 3
    assert s0["avg_ms"] == 15.0
