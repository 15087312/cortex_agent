"""可视化链路（事件日志 / 投影 / 待办表）内存有界回归

类型: 缓存型泄漏防护——可视化引入三个易无界增长的结构：

- `SessionEventRepository._next`（每会话 seq 计数器）与事件表行数；
- `session_projections`（按 key 覆盖写，绝不能追加）；
- `PendingRequestRegistry`（审批/提问登记后必须随 resolve/断线清掉）。

长会话/持续刷新都不能让它们随时间线性增长。
"""
import gc
import threading

import pytest

import modules.thinking.api_stream as stream
from modules.thinking import pending_requests as pr

pytestmark = pytest.mark.leak


@pytest.fixture
def repos(tmp_path, monkeypatch):
    import modules.database.connection as conn
    import modules.database.session_events as se
    import modules.database.session_projections as sp

    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "viz_leak.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    dm = conn.get_db_manager()
    dm.initialize()
    monkeypatch.setattr(se, "get_db_manager", lambda: dm)
    monkeypatch.setattr(sp, "get_db_manager", lambda: dm)
    se.reset_session_event_repo()
    sp.reset_projection_repo()
    yield se.SessionEventRepository(), sp.ProjectionRepository(), sp
    se.reset_session_event_repo()
    sp.reset_projection_repo()


def test_event_repo_counters_stay_per_session(repos):
    """大量事件后：每会话只有一条计数器，clear 后计数器彻底释放"""
    events, _proj, _sp = repos
    for i in range(500):
        events.append("s1", type="tool", event="tool_call", data={"i": i})
    assert len(events._next) == 1
    assert events.last_seq("s1") == 500
    events.clear("s1")
    assert events._next == {}


def test_projection_upsert_never_appends(repos):
    """同一 key 反复覆盖写 → 库里始终只有一行（不随时间增长）"""
    _events, proj, sp = repos
    for i in range(500):
        proj.upsert("s1", "contextPressure", {"tokens": i, "window": 1000, "ratio": 0.1})
    assert len(proj.all("s1")) == 1
    with proj._session() as s:
        rows = s.query(sp.SessionProjection).filter(
            sp.SessionProjection.session_id == "s1"
        ).count()
    assert rows == 1


def test_event_buffer_is_ring_buffered(monkeypatch, repos):
    """api_stream 的环形补发缓冲有上限（200/会话），长会话不无限增长"""
    monkeypatch.setattr(stream, "_event_buffers", {})
    monkeypatch.setattr(stream, "_event_seq", {})
    for i in range(600):
        stream._record_event("s1", {"type": "progress", "event": "tick", "data": {"i": i}})
    buf = stream._event_buffers["s1"]
    assert buf.maxlen == 200
    assert len(buf) == 200  # 只保留最近 200 帧，长会话不线性增长


def test_pending_registry_releases_resolved_requests():
    """1000 次登记+应答后待办表为空（不残留会话/请求对象）"""
    pr.reset_registry()
    registry = pr.get_registry()
    try:
        for i in range(1000):
            registry.register("s1", f"r{i}", pr.KIND_APPROVAL, target="t")
            registry.resolve("s1", f"r{i}")
        assert registry.snapshot("s1") == []
        assert registry._by_session == {}
        for i in range(100):
            registry.register(f"s{i}", "r", pr.KIND_APPROVAL)
            registry.clear(f"s{i}")
        assert registry._by_session == {}
    finally:
        pr.reset_registry()


def test_pending_snapshot_returns_detached_copies():
    """快照是副本：前端持有快照不会把登记表里的对象钉住"""
    pr.reset_registry()
    registry = pr.get_registry()
    try:
        registry.register("s1", "r1", pr.KIND_APPROVAL, target="t")
        snap = registry.snapshot("s1")
        before = len(registry.snapshot("s1"))
        snap[0]["target"] = "mutated"
        assert registry.snapshot("s1")[0]["target"] == "t"
        assert before == 1
    finally:
        pr.reset_registry()


def test_pending_objects_are_garbage_collected():
    """登记表清空后请求记录可被回收（无隐藏强引用）"""
    pr.reset_registry()
    registry = pr.get_registry()
    try:
        for i in range(200):
            registry.register("s1", f"r{i}", pr.KIND_APPROVAL, detail="x" * 200)
        registry.clear("s1")
        gc.collect()
        before = len(gc.get_objects())
        for i in range(200):
            registry.register("s1", f"r{i}", pr.KIND_APPROVAL, detail="x" * 200)
            registry.resolve("s1", f"r{i}")
        gc.collect()
        growth = len(gc.get_objects()) - before
        assert growth < 2000, f"待办对象增长 {growth}"
    finally:
        pr.reset_registry()
