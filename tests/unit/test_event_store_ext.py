"""event_store 扩展测试：补齐 get_instance 竞态 / 迁移全分支 / 自动向量化分支 / worker / FAISS 全路径"""
import json
import os
import sqlite3
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from modules.memory.event_store import EventStore, MemoryEvent


@pytest.fixture
def store(tmp_path):
    s = EventStore(
        db_path=str(tmp_path / "es2.db"),
        faiss_index_path=str(tmp_path / "es2.faiss"),
        id_map_path=str(tmp_path / "es2.json"),
    )
    yield s
    # 释放假/真 faiss 索引后再 close：避免 __del__ 在循环 GC 期对
    # 非 faiss 对象调 write_index 抛 SWIG TypeError，触发 Python 3.13 无限 GC 循环
    s._faiss_index = None
    s._id_map = []
    try:
        s.close()
    except Exception:
        pass


def _event(**kw):
    base = dict(fact="测试事件", keywords=["k1"], importance=0.6)
    base.update(kw)
    return MemoryEvent(**base)


class SyncThread:
    """同步执行的假线程：start() 立即跑 target"""

    def __init__(self, target=None, daemon=False, **kw):
        self._target = target

    def start(self):
        self._target()


# ── 单例竞态 ────────────────────────────────────────────────────────────────

def test_get_instance_inner_race(monkeypatch):
    existing = EventStore(db_path="/tmp/x.db")
    monkeypatch.setattr(EventStore, "_instance", None)

    class FakeLock:
        def __enter__(self):
            EventStore._instance = existing
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(EventStore, "_lock", FakeLock())
    assert EventStore.get_instance() is existing


def test_get_instance_creates_when_none(monkeypatch, tmp_path):
    """外/内层都为 None → 走 118->119->120->121 创建"""
    monkeypatch.setattr(EventStore, "_instance", None)
    inst = EventStore.get_instance(
        db_path=str(tmp_path / "gi.db"),
        faiss_index_path=str(tmp_path / "gi.faiss"),
        id_map_path=str(tmp_path / "gi.json"),
    )
    assert inst is not None


def test_get_instance_already_set(monkeypatch):
    existing = EventStore(db_path="/tmp/x2.db")
    monkeypatch.setattr(EventStore, "_instance", existing)
    assert EventStore.get_instance() is existing  # 118->122 快速返回


# ── 连接重建：close 抛异常 ──────────────────────────────────────────────────

def test_get_conn_close_error_then_rebuild(store):
    store.save_event(_event())
    os.remove(store._db_path)

    class FakeConn:
        def close(self):
            raise RuntimeError("close boom")

    store._conn = FakeConn()
    store.save_event(_event(fact="新事件"))  # 138-140 except 捕获后重建
    assert os.path.exists(store._db_path)


# ── 迁移：全部 ALTER 分支 ───────────────────────────────────────────────────

def test_migrate_schema_all_columns_missing(tmp_path):
    """直接调用 _migrate_schema：type 等全部新列缺失 → 6 条 ALTER 全部执行（178-188）"""
    db = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE events (
        id TEXT PRIMARY KEY, fact TEXT NOT NULL, thought TEXT DEFAULT '',
        lesson TEXT DEFAULT '', keywords TEXT DEFAULT '[]',
        importance REAL DEFAULT 0.5, time TEXT NOT NULL, session_id TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.commit()
    conn.close()
    s = EventStore(
        db_path=db,
        faiss_index_path=str(tmp_path / "l.faiss"),
        id_map_path=str(tmp_path / "l.json"),
    )
    raw = sqlite3.connect(db)
    raw.row_factory = sqlite3.Row
    s._migrate_schema(raw)
    cols = {r["name"] for r in raw.execute("PRAGMA table_info(events)").fetchall()}
    for col in ("type", "last_accessed", "access_count", "mention_count",
                "causal_node_ids", "owner_id"):
        assert col in cols


def test_init_db_migrates_partial_legacy(tmp_path):
    """端到端：legacy 表含 type（供索引）但缺新列 → _get_conn 自动补全"""
    db = str(tmp_path / "legacy2.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE events (
        id TEXT PRIMARY KEY, fact TEXT NOT NULL, thought TEXT DEFAULT '',
        lesson TEXT DEFAULT '', keywords TEXT DEFAULT '[]',
        importance REAL DEFAULT 0.5, time TEXT NOT NULL, session_id TEXT DEFAULT '',
        type TEXT DEFAULT 'fact',
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.commit()
    conn.close()
    s = EventStore(
        db_path=db,
        faiss_index_path=str(tmp_path / "l2.faiss"),
        id_map_path=str(tmp_path / "l2.json"),
    )
    c = s._get_conn()
    cols = {r["name"] for r in c.execute("PRAGMA table_info(events)").fetchall()}
    assert "owner_id" in cols and "causal_node_ids" in cols


def test_migrate_schema_columns_exist_skip(tmp_path):
    """新列全部存在 → ALTER 全部跳过（185->187, 187->189 等 fall-through）"""
    db = str(tmp_path / "legacy3.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE events (
        id TEXT PRIMARY KEY, fact TEXT NOT NULL, thought TEXT DEFAULT '',
        lesson TEXT DEFAULT '', keywords TEXT DEFAULT '[]',
        importance REAL DEFAULT 0.5, time TEXT NOT NULL, session_id TEXT DEFAULT '',
        type TEXT DEFAULT 'fact', last_accessed TEXT DEFAULT '',
        access_count INTEGER DEFAULT 0, mention_count INTEGER DEFAULT 1,
        causal_node_ids TEXT DEFAULT '[]', owner_id TEXT DEFAULT 'shared',
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.commit()
    conn.close()
    s = EventStore(
        db_path=db,
        faiss_index_path=str(tmp_path / "l3.faiss"),
        id_map_path=str(tmp_path / "l3.json"),
    )
    c = s._get_conn()
    assert c is not None


def test_save_event_preserves_id_and_last_accessed(store, monkeypatch):
    """id 与 last_accessed 已存在 → 跳过生成分支（202->204, 207->210）"""
    from modules.memory import embedding as emb_mod
    fake = MagicMock()
    fake._loaded = False
    fake._attempted = True
    monkeypatch.setattr(emb_mod.EmbeddingEngine, "get_instance", staticmethod(lambda: fake))
    ev = _event(fact="预设", id="custom-id", time="2026-01-01T00:00:00",
                last_accessed="2026-01-02T00:00:00")
    eid = store.save_event(ev)
    assert eid == "custom-id"
    assert store.get_event("custom-id").last_accessed == "2026-01-02T00:00:00"


# ── 自动向量化分支 ──────────────────────────────────────────────────────────

def test_save_event_with_embedding_skips_vectorize(store):
    ev = _event(embedding=[1.0, 2.0])
    eid = store.save_event(ev)
    assert eid == ev.id


def test_save_event_embed_empty_text(store, monkeypatch):
    """eng 已加载但文本为空 → 跳过向量化直接保存（243->256）"""
    from modules.memory import embedding as emb_mod
    fake = MagicMock()
    fake._loaded = True
    fake.embed = MagicMock()
    monkeypatch.setattr(emb_mod.EmbeddingEngine, "get_instance", staticmethod(lambda: fake))
    ev = _event(fact="", thought="", lesson="", keywords=[])
    eid = store.save_event(ev)
    assert eid == ev.id
    fake.embed.assert_not_called()


def test_save_event_embed_returns_none(store, monkeypatch):
    """eng 已加载但 embed 返回 None → 不写 FAISS（245->256）"""
    from modules.memory import embedding as emb_mod
    fake = MagicMock()
    fake._loaded = True
    fake.embed = MagicMock(return_value=None)
    monkeypatch.setattr(emb_mod.EmbeddingEngine, "get_instance", staticmethod(lambda: fake))
    store._add_embedding_inner = MagicMock()
    ev = _event(fact="内容")
    eid = store.save_event(ev)
    assert eid == ev.id
    store._add_embedding_inner.assert_not_called()


def test_save_event_engine_attempted_not_loaded(store, monkeypatch):
    """eng 已尝试过但未加载成功 → 不入队也不向量化（249 分支不成立）"""
    from modules.memory import embedding as emb_mod
    fake = MagicMock()
    fake._loaded = False
    fake._attempted = True
    monkeypatch.setattr(emb_mod.EmbeddingEngine, "get_instance", staticmethod(lambda: fake))
    ev = _event(fact="内容")
    eid = store.save_event(ev)
    assert eid == ev.id
    assert not store._pending_embeddings


# ── 后台 worker ─────────────────────────────────────────────────────────────

def test_embedding_worker_engine_not_loaded(store, monkeypatch):
    from config.settings import settings
    from modules.memory import embedding as emb_mod
    import time
    monkeypatch.setattr(settings, "EMBEDDING_BACKGROUND_WORKER", True)
    fake = MagicMock()
    fake._loaded = False
    fake._attempted = False
    monkeypatch.setattr(emb_mod.EmbeddingEngine, "get_instance", staticmethod(lambda: fake))
    store._embedding_worker_started = False
    store._pending_embeddings = ["x"]
    monkeypatch.setattr(threading, "Thread", SyncThread)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    store._start_embedding_worker()
    assert store._embedding_worker_started is False


def test_embedding_worker_processes_pending(store, monkeypatch):
    from config.settings import settings
    from modules.memory import embedding as emb_mod
    import time
    monkeypatch.setattr(settings, "EMBEDDING_BACKGROUND_WORKER", True)
    fake = MagicMock()
    fake._loaded = True
    fake._attempted = True
    fake.embed = MagicMock(return_value=[0.1, 0.2])
    monkeypatch.setattr(emb_mod.EmbeddingEngine, "get_instance", staticmethod(lambda: fake))
    ev = _event(fact="内容")
    monkeypatch.setattr(store, "get_event", lambda eid: ev)
    store.add_embedding = MagicMock()
    store._embedding_worker_started = False
    store._pending_embeddings = ["e1"]
    monkeypatch.setattr(threading, "Thread", SyncThread)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    store._start_embedding_worker()
    store.add_embedding.assert_called_once_with("e1", [0.1, 0.2])
    assert store._embedding_worker_started is False


def test_embedding_worker_embed_exception(store, monkeypatch):
    from config.settings import settings
    from modules.memory import embedding as emb_mod
    import time
    monkeypatch.setattr(settings, "EMBEDDING_BACKGROUND_WORKER", True)
    fake = MagicMock()
    fake._loaded = True
    fake._attempted = True
    fake.embed = MagicMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(emb_mod.EmbeddingEngine, "get_instance", staticmethod(lambda: fake))
    ev = _event(fact="内容")
    monkeypatch.setattr(store, "get_event", lambda eid: ev)
    store.add_embedding = MagicMock()
    store._embedding_worker_started = False
    store._pending_embeddings = ["e1"]
    monkeypatch.setattr(threading, "Thread", SyncThread)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    store._start_embedding_worker()
    store.add_embedding.assert_not_called()
    assert store._embedding_worker_started is False


def test_embedding_worker_skip_branches(store, monkeypatch):
    """worker 循环内的跳过分支：无事件/有向量/空文本/向量为空（283/285/287 的 False 分支）"""
    from config.settings import settings
    from modules.memory import embedding as emb_mod
    import time
    monkeypatch.setattr(settings, "EMBEDDING_BACKGROUND_WORKER", True)
    fake = MagicMock()
    fake._loaded = True
    fake._attempted = True
    fake.embed = MagicMock(return_value=None)
    monkeypatch.setattr(emb_mod.EmbeddingEngine, "get_instance", staticmethod(lambda: fake))

    ev_with_vec = _event(fact="已有向量", embedding=[0.1, 0.2])
    ev_empty_text = _event(fact="", thought="", lesson="", keywords=[])
    ev_embed_none = _event(fact="内容")

    def fake_get(eid):
        return {"v": ev_with_vec, "t": ev_empty_text, "n": ev_embed_none}.get(eid)

    monkeypatch.setattr(store, "get_event", fake_get)
    store.add_embedding = MagicMock()
    store._embedding_worker_started = False
    store._pending_embeddings = ["v", "t", "n"]
    monkeypatch.setattr(threading, "Thread", SyncThread)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    store._start_embedding_worker()
    store.add_embedding.assert_not_called()


# ── FAISS 维度 & 加载 ───────────────────────────────────────────────────────

def test_get_embedding_dim_raises(store, monkeypatch):
    from modules.memory import embedding as emb_mod
    fake = MagicMock()
    fake._load_model = MagicMock(return_value=False)
    monkeypatch.setattr(emb_mod.EmbeddingEngine, "get_instance", staticmethod(lambda: fake))
    store._embedding_dim = None
    with pytest.raises(RuntimeError):
        store._get_embedding_dim()


def test_get_embedding_dim_success(store, monkeypatch):
    from modules.memory import embedding as emb_mod
    fake = MagicMock()
    fake._load_model = MagicMock(return_value=True)
    fake.dim = 8
    monkeypatch.setattr(emb_mod.EmbeddingEngine, "get_instance", staticmethod(lambda: fake))
    store._embedding_dim = None
    assert store._get_embedding_dim() == 8
    assert store._embedding_dim == 8


def test_load_faiss_existing_index_and_idmap(store, monkeypatch, tmp_path):
    with open(store._id_map_path, "w") as f:
        json.dump(["a", "b"], f)
    Path(store._faiss_index_path).write_bytes(b"fake-index")

    class FakeIdx:
        def __init__(self, ntotal, d):
            self.ntotal = ntotal
            self.d = d

    class FakeFaiss:
        @staticmethod
        def read_index(p):
            return FakeIdx(2, 8)

        @staticmethod
        def IndexFlatIP(dim):
            return FakeIdx(0, dim)

    monkeypatch.setitem(sys.modules, "faiss", FakeFaiss())
    store._faiss_index = None
    store._id_map = []
    store._get_embedding_dim = MagicMock(return_value=8)
    store._load_faiss()
    assert store._faiss_index is not None
    assert store._faiss_index.ntotal == 2
    assert store._id_map == ["a", "b"]


def test_load_faiss_already_loaded(store):
    store._faiss_index = object()  # 已加载 → 393 直接返回
    store._load_faiss()
    assert store._faiss_index is not None


def test_save_faiss_writes_real_index(store, monkeypatch):
    """真实 faiss 索引落盘：write_index + id_map dump（424-425）"""
    import faiss as real_faiss
    store._faiss_index = real_faiss.IndexFlatIP(8)
    store._id_map = ["e1"]
    store._save_faiss()
    assert os.path.exists(store._faiss_index_path)
    assert json.loads(Path(store._id_map_path).read_text()) == ["e1"]
    store._faiss_index = None


def test_add_embedding_public(store):
    store._add_embedding_inner = MagicMock()
    store.add_embedding("e1", [1.0, 2.0])
    store._add_embedding_inner.assert_called_once_with("e1", [1.0, 2.0])


def test_add_embedding_inner_no_index(store, monkeypatch):
    store._load_faiss = MagicMock()
    store._faiss_index = None
    store._save_faiss = MagicMock()
    store._add_embedding_inner("e1", [1.0, 0.0])  # 440 True → 441 return


# ── remove_embedding ─────────────────────────────────────────────────────────

def _fake_reconstruct_index(n, dim=8):
    class FakeIdx:
        def __init__(self):
            self.ntotal = n
            self.d = dim
            self._vecs = [np.full(dim, 0.5, dtype=np.float32) for _ in range(n)]

        def reconstruct(self, i, out):
            out[:] = self._vecs[i]

        def add(self, arr):
            pass

    return FakeIdx()


def test_remove_embedding_rebuilds(store, monkeypatch):
    store._faiss_index = _fake_reconstruct_index(2)
    store._id_map = ["e1", "e2"]
    store._embedding_dim = 8
    store._load_faiss = MagicMock()
    store._save_faiss = MagicMock()
    store.remove_embedding("e1")
    assert store._id_map == ["e2"]
    store._save_faiss.assert_called_once()


def test_remove_embedding_to_empty(store, monkeypatch):
    store._faiss_index = _fake_reconstruct_index(1)
    store._id_map = ["only"]
    store._embedding_dim = 8
    store._load_faiss = MagicMock()
    store._save_faiss = MagicMock()
    store.remove_embedding("only")
    assert store._id_map == []


def test_remove_embedding_not_in_map(store, monkeypatch):
    store._faiss_index = MagicMock()
    store._id_map = ["a"]
    store._load_faiss = MagicMock()
    store._save_faiss = MagicMock()
    store.remove_embedding("nope")  # 458 直接 return
    assert store._id_map == ["a"]


def test_remove_embedding_exception(store, monkeypatch):
    store._load_faiss = MagicMock(side_effect=RuntimeError("boom"))
    store.remove_embedding("x")  # 483-484 不抛异常


# ── search_by_vector ─────────────────────────────────────────────────────────

def test_search_by_vector_skips_negative_idx(store, monkeypatch):
    class FakeIdx:
        ntotal = 2
        d = 8

        def search(self, vec, k):
            scores = np.array([[1.0, 0.5]], dtype=np.float32)
            idx = np.array([[0, -1]], dtype=np.int64)
            return scores, idx

    store._load_faiss = MagicMock()
    store._faiss_index = FakeIdx()
    store._id_map = ["a", "b"]
    res = store.search_by_vector([1.0, 0.0], top_k=2)
    assert res == [("a", 1.0)]


def test_search_by_vector_exception(store, monkeypatch):
    import types
    fake = types.ModuleType("faiss")
    fake.normalize_L2 = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    monkeypatch.setitem(sys.modules, "faiss", fake)

    class FakeIdx:
        ntotal = 1
        d = 8

    store._load_faiss = MagicMock()
    store._faiss_index = FakeIdx()
    assert store.search_by_vector([1.0], top_k=1) == []


# ── get_embedding ────────────────────────────────────────────────────────────

def test_get_embedding_found(store, monkeypatch):
    class FakeIdx:
        d = 8
        ntotal = 1

        def __init__(self):
            self._v = np.full(8, 0.5, dtype=np.float32)

        def reconstruct(self, idx, out):
            out[:] = self._v

    store._load_faiss = MagicMock()
    store._faiss_index = FakeIdx()
    store._id_map = ["e1"]
    v = store.get_embedding("e1")
    assert v is not None and len(v) == 8


def test_get_embedding_missing(store, monkeypatch):
    store._load_faiss = MagicMock()
    store._faiss_index = MagicMock()
    store._id_map = ["e1"]
    assert store.get_embedding("nope") is None


def test_get_embedding_exception(store, monkeypatch):
    class BadIdx:
        d = 8

        def reconstruct(self, idx, out):
            raise RuntimeError("boom")

    store._load_faiss = MagicMock()
    store._faiss_index = BadIdx()
    store._id_map = ["e1"]
    assert store.get_embedding("e1") is None


# ── clear_all / __del__ ──────────────────────────────────────────────────────

def test_clear_all_removes_existing_files(store):
    store.save_event(_event())
    Path(store._faiss_index_path).write_bytes(b"fake")
    Path(store._id_map_path).write_text("[]")
    store.clear_all()
    assert not os.path.exists(store._faiss_index_path)
    assert not os.path.exists(store._id_map_path)


def test_del_calls_close(store, monkeypatch):
    monkeypatch.setattr(sys, "is_finalizing", lambda: False)
    store.close = MagicMock()
    store.__del__()
    store.close.assert_called_once()


def test_del_close_error_suppressed(store, monkeypatch):
    monkeypatch.setattr(sys, "is_finalizing", lambda: False)
    store.close = MagicMock(side_effect=RuntimeError("boom"))
    store.__del__()  # 583-584 except pass 吞掉异常
