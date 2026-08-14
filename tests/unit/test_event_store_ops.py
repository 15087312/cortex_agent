"""event_store 扩展测试：CRUD / 检索 / 迁移 / FAISS 路径 / 后台 worker"""
import json
import os
import sqlite3
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

from modules.memory.event_store import EventStore, MemoryEvent


@pytest.fixture
def store(tmp_path):
    return EventStore(
        db_path=str(tmp_path / "es.db"),
        faiss_index_path=str(tmp_path / "es.faiss"),
        id_map_path=str(tmp_path / "es.json"),
    )


def _event(**kw):
    base = dict(fact="测试事件", keywords=["k1"], importance=0.6)
    base.update(kw)
    return MemoryEvent(**base)


# ── MemoryEvent 序列化 ─────────────────────────────────────────────────

def test_memory_event_roundtrip():
    ev = _event(causal_node_ids=["n1", "n2"], last_accessed="t", access_count=2)
    d = ev.to_dict()
    assert d["keywords"] == '["k1"]'
    assert d["causal_node_ids"] == '["n1", "n2"]'
    assert "embedding" not in d
    ev2 = MemoryEvent.from_dict(d)
    assert ev2.causal_node_ids == ["n1", "n2"]
    assert ev2.access_count == 2
    assert ev2.last_accessed == "t"


def test_memory_event_from_dict_defaults():
    ev = MemoryEvent.from_dict({"id": "x", "fact": "f", "time": "t"})
    assert ev.keywords == []
    assert ev.owner_id == "shared"
    assert ev.mention_count == 1


# ── CRUD ───────────────────────────────────────────────────────────────

def test_save_event_generates_ids(store):
    ev = _event()
    eid = store.save_event(ev)
    assert eid
    assert ev.id == eid
    assert ev.time  # 自动填充
    assert ev.last_accessed == ev.time


def test_get_delete_list_count(store):
    e1 = _event(fact="A")
    e2 = _event(fact="B")
    store.save_event(e1)
    store.save_event(e2)
    assert store.get_event(e1.id).fact == "A"
    assert store.get_event("不存在") is None
    assert store.count_events() == 2
    assert len(store.list_events(limit=1)) == 1
    assert store.delete_event(e1.id) is True
    assert store.delete_event("不存在") is False
    assert store.count_events() == 1


def test_save_event_auto_embedding_paths(store, monkeypatch):
    # 模型已加载 → 直接向量化
    from modules.memory import embedding as emb_mod
    fake_eng = MagicMock()
    fake_eng._loaded = True
    fake_eng.embed = MagicMock(return_value=[0.1, 0.2])
    monkeypatch.setattr(emb_mod.EmbeddingEngine, "get_instance", staticmethod(lambda: fake_eng))
    store._add_embedding_inner = MagicMock()
    ev = _event(fact="有内容")
    store.save_event(ev)
    store._add_embedding_inner.assert_called_once()
    assert ev.embedding == [0.1, 0.2]


def test_save_event_queues_embedding(store, monkeypatch):
    # 模型未加载未尝试 → 入待处理队列
    from modules.memory import embedding as emb_mod
    fake_eng = MagicMock()
    fake_eng._loaded = False
    fake_eng._attempted = False
    monkeypatch.setattr(emb_mod.EmbeddingEngine, "get_instance", staticmethod(lambda: fake_eng))
    store._pending_embeddings.clear()
    ev = _event(fact="有内容")
    store.save_event(ev)
    assert ev.id in store._pending_embeddings


def test_save_event_embedding_exception(store, monkeypatch):
    from modules.memory import embedding as emb_mod
    fake_eng = MagicMock()
    fake_eng._loaded = True
    fake_eng.embed = MagicMock(side_effect=RuntimeError("no"))
    monkeypatch.setattr(emb_mod.EmbeddingEngine, "get_instance", staticmethod(lambda: fake_eng))
    store._add_embedding_inner = MagicMock()
    ev = _event(fact="有内容")
    assert store.save_event(ev) == ev.id


def test_touch_event_and_increment_mention(store):
    ev = _event()
    store.save_event(ev)
    assert store.touch_event(ev.id) is True
    assert store.touch_event("不存在") is False
    updated = store.get_event(ev.id)
    assert updated.access_count == 1
    assert store.increment_mention(ev.id) is True
    assert store.get_event(ev.id).mention_count == 2


# ── 检索 ───────────────────────────────────────────────────────────────

def test_search_by_keywords(store):
    store.save_event(_event(fact="缓存优化", keywords=["缓存", "性能"]))
    store.save_event(_event(fact="其他", keywords=["无关"]))
    hits = store.search_by_keywords(["缓存"])
    assert len(hits) == 1
    assert hits[0].fact == "缓存优化"
    assert store.search_by_keywords([]) == []
    assert store.search_by_keywords(["没有这个关键词"]) == []


def test_search_by_importance(store):
    store.save_event(_event(fact="重要", importance=0.9))
    store.save_event(_event(fact="次要", importance=0.3))
    hits = store.search_by_importance(min_importance=0.7)
    assert [e.fact for e in hits] == ["重要"]


def test_search_by_time(store):
    store.save_event(_event(fact="早期", time="2026-01-01T00:00:00"))
    store.save_event(_event(fact="中期", time="2026-06-01T00:00:00"))
    store.save_event(_event(fact="晚期", time="2026-12-01T00:00:00"))
    assert [e.fact for e in store.search_by_time()] == ["晚期", "中期", "早期"]
    mid = store.search_by_time(start_time="2026-02-01", end_time="2026-07-01")
    assert [e.fact for e in mid] == ["中期"]
    only_start = store.search_by_time(start_time="2026-07-01")
    assert [e.fact for e in only_start] == ["晚期"]


# ── 连接重建 / 迁移 ────────────────────────────────────────────────────

def test_get_conn_rebuilds_after_file_deleted(store):
    store.save_event(_event())
    os.remove(store._db_path)
    store.save_event(_event(fact="新事件"))  # 触发 _get_conn 重建
    assert store.get_event(store.list_events()[0].id)


def test_migration_adds_columns(tmp_path):
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE events (
        id TEXT PRIMARY KEY, fact TEXT, thought TEXT, lesson TEXT,
        keywords TEXT, importance REAL, time TEXT, session_id TEXT,
        type TEXT, last_accessed TEXT, access_count INTEGER, mention_count INTEGER,
        created_at TEXT
    )""")
    conn.commit()
    conn.close()
    s = EventStore(
        db_path=db,
        faiss_index_path=str(tmp_path / "o.faiss"),
        id_map_path=str(tmp_path / "o.json"),
    )
    conn2 = s._get_conn()
    cols = {r["name"] for r in conn2.execute("PRAGMA table_info(events)").fetchall()}
    assert "type" in cols and "owner_id" in cols and "causal_node_ids" in cols


# ── 后台 worker ────────────────────────────────────────────────────────

def test_start_embedding_worker_disabled(store, monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "EMBEDDING_BACKGROUND_WORKER", False)
    store._pending_embeddings.append("x")
    store._start_embedding_worker()
    assert store._embedding_worker_started is False


def test_start_embedding_worker_enabled(store, monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "EMBEDDING_BACKGROUND_WORKER", True)
    store._embedding_worker_started = False
    store._pending_embeddings.append("x")
    store._start_embedding_worker()
    assert store._embedding_worker_started is True
    # worker 线程运行后重置标志
    import time
    deadline = time.time() + 5
    while store._embedding_worker_started and time.time() < deadline:
        time.sleep(0.05)
    assert store._embedding_worker_started is False


def test_start_embedding_worker_already_started(store, monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "EMBEDDING_BACKGROUND_WORKER", True)
    store._embedding_worker_started = True
    store._pending_embeddings.append("x")
    store._start_embedding_worker()
    assert store._embedding_worker_started is True


# ── FAISS 路径（模拟导入失败 / 假 faiss）───────────────────────────────

def test_search_by_vector_no_index(store):
    store._faiss_index = None
    store._load_faiss = MagicMock()
    assert store.search_by_vector([1.0, 0.0]) == []


def test_add_embedding_faiss_import_fails(store, monkeypatch):
    monkeypatch.setitem(sys.modules, "faiss", None)
    store._load_faiss = MagicMock()
    store._add_embedding_inner("e1", [1.0, 0.0])  # 不抛异常


def test_load_faiss_import_error(store, monkeypatch):
    monkeypatch.setitem(sys.modules, "faiss", None)
    store._faiss_index = None
    store._load_faiss()
    assert store._faiss_index is None


def test_load_faiss_success_new_index(store, monkeypatch):
    class FakeFaiss:
        @staticmethod
        def IndexFlatIP(dim):
            class Idx:
                def __init__(self):
                    self.ntotal = 0
                    self.d = dim
            return Idx()
        @staticmethod
        def read_index(path):
            return None
    monkeypatch.setitem(sys.modules, "faiss", FakeFaiss())
    store._get_embedding_dim = MagicMock(return_value=8)
    store._load_faiss()
    assert store._faiss_index is not None


def test_add_embedding_success_path(store, monkeypatch):
    calls = []
    class FakeIdx:
        def __init__(self, dim=8):
            self.ntotal = 0
            self.d = dim
        def add(self, arr):
            self.ntotal += arr.shape[0]
    idx = FakeIdx()
    store._faiss_index = idx
    store._id_map = []
    store._load_faiss = MagicMock()
    store._save_faiss = MagicMock(wraps=lambda: calls.append(1))
    store._add_embedding_inner("e1", [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5])
    assert store._id_map == ["e1"]
    assert idx.ntotal == 1


def test_get_embedding_various(store):
    store._load_faiss = MagicMock()
    store._faiss_index = None
    assert store.get_embedding("missing") is None


def test_clear_all_removes_files(store):
    store.save_event(_event())
    faiss_path = store._faiss_index_path
    idmap_path = store._id_map_path
    store.clear_all()
    assert store.count_events() == 0
    assert not os.path.exists(faiss_path)
    assert not os.path.exists(idmap_path)


def test_close_and_finalize(store):
    store.close()
    assert store._conn is None
    # __del__ 在 finalizing 阶段跳过
    with patch.object(sys, "is_finalizing", return_value=True):
        store.__del__()
