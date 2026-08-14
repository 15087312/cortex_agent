"""memory/embedding 扩展测试：补齐 get_instance 竞态/双检锁/成功推理/FAISS 重建路径"""
import json
import os
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np

from modules.memory.embedding import EmbeddingEngine


def _ctx_mgr(obj):
    obj.no_grad = lambda: obj
    obj.__enter__ = lambda self: self
    obj.__exit__ = lambda self, *a: False
    return obj


def _bare(**kw):
    e = EmbeddingEngine.__new__(EmbeddingEngine)
    e._loaded = kw.get("loaded", False)
    e._model = kw.get("model", None)
    e._tokenizer = kw.get("tokenizer", None)
    e.dim = kw.get("dim", 768)
    e._load_lock = kw.get("load_lock", object())
    e._infer_lock = kw.get("infer_lock", threading.Lock())
    return e


# ── get_instance 竞态路径 ────────────────────────────────────────────────

def test_get_instance_inner_race_fallthrough(monkeypatch):
    """内层检查发现 _instance 已被并发线程设置 → 直接返回（38->40）"""
    existing = EmbeddingEngine()
    monkeypatch.setattr(EmbeddingEngine, "_instance", None)

    class FakeLock:
        def __enter__(self):
            EmbeddingEngine._instance = existing
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(EmbeddingEngine, "_lock", FakeLock())
    got = EmbeddingEngine.get_instance()
    assert got is existing


# ── _load_model 双检锁内层 ────────────────────────────────────────────────

def test_load_model_double_check_inner(monkeypatch):
    """进入锁后发现 _attempted 已被其他线程置位 → 直接返回 _loaded（47->48）"""
    e = EmbeddingEngine()
    e._attempted = False
    e._loaded = False

    class FakeLock:
        def __enter__(self):
            e._attempted = True
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(e, "_load_lock", FakeLock())
    assert e._load_model() is False


def test_load_model_local_only_false_and_hf_mirror(monkeypatch, tmp_path):
    """local_only=False（跳过离线 env）且 HF_MIRROR 设置 → 写入 HF_ENDPOINT"""
    from config.settings import settings

    monkeypatch.setattr(settings, "EMBEDDING_LOCAL_FILES_ONLY", False)
    monkeypatch.setattr(settings, "HF_MIRROR", "https://hf-mirror.com")
    monkeypatch.setattr(settings, "EMBEDDING_CACHE_FOLDER", str(tmp_path / "cache"))

    model = MagicMock()
    model.config.hidden_size = 384
    tok = MagicMock()

    class FakeTransformers:
        AutoModel = MagicMock()
        AutoModel.from_pretrained = MagicMock(return_value=model)
        AutoTokenizer = MagicMock()
        AutoTokenizer.from_pretrained = MagicMock(return_value=tok)

    monkeypatch.setitem(sys.modules, "transformers", FakeTransformers())
    e = EmbeddingEngine()
    e._rebuild_faiss_if_needed = MagicMock()
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    try:
        assert e._load_model() is True
        assert os.environ.get("HF_ENDPOINT") == "https://hf-mirror.com"
        assert "TRANSFORMERS_OFFLINE" not in os.environ
    finally:
        monkeypatch.delenv("HF_ENDPOINT", raising=False)


def test_load_model_local_only_sets_offline(monkeypatch, tmp_path):
    """local_only=True → 设置 HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE"""
    from config.settings import settings
    monkeypatch.setattr(settings, "EMBEDDING_LOCAL_FILES_ONLY", True)
    monkeypatch.setattr(settings, "HF_MIRROR", "")
    monkeypatch.setattr(settings, "EMBEDDING_CACHE_FOLDER", str(tmp_path / "cache"))

    model = MagicMock()
    model.config.hidden_size = 8
    tok = MagicMock()

    class FakeTransformers:
        AutoModel = MagicMock()
        AutoModel.from_pretrained = MagicMock(return_value=model)
        AutoTokenizer = MagicMock()
        AutoTokenizer.from_pretrained = MagicMock(return_value=tok)

    monkeypatch.setitem(sys.modules, "transformers", FakeTransformers())
    e = EmbeddingEngine()
    e._rebuild_faiss_if_needed = MagicMock()
    try:
        assert e._load_model() is True
        assert os.environ.get("HF_HUB_OFFLINE") == "1"
        assert os.environ.get("TRANSFORMERS_OFFLINE") == "1"
    finally:
        monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
        monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)


# ── _rebuild_faiss_if_needed ──────────────────────────────────────────────

def test_rebuild_faiss_index_absent(monkeypatch, tmp_path):
    """索引文件不存在 → 走'新记忆库'日志分支（110->118, 123），且无事件可重向量化（135->exit）"""
    from config.settings import settings
    import modules.memory.event_store as es_mod
    monkeypatch.setattr(settings, "MEMORY_FAISS_INDEX", str(tmp_path / "no.idx"))
    monkeypatch.setattr(settings, "MEMORY_ID_MAP", str(tmp_path / "no.json"))
    monkeypatch.setattr("os.path.exists", lambda p: False)
    removed = []
    monkeypatch.setattr("os.remove", lambda p: removed.append(p))

    class FakeStore:
        @staticmethod
        def list_events(limit=5000):
            return []

    monkeypatch.setattr(es_mod.EventStore, "get_instance", classmethod(lambda cls: FakeStore()))
    e = EmbeddingEngine()
    e.dim = 384
    e._rebuild_faiss_if_needed()
    assert removed == []


def test_rebuild_faiss_read_index_fails(monkeypatch, tmp_path):
    """索引存在但读取失败 → old_dim=None → 触发重建（115-116）"""
    from config.settings import settings
    monkeypatch.setattr(settings, "MEMORY_FAISS_INDEX", str(tmp_path / "bad.idx"))
    monkeypatch.setattr(settings, "MEMORY_ID_MAP", str(tmp_path / "bad.json"))
    monkeypatch.setattr("os.path.exists", lambda p: True)
    removed = []
    monkeypatch.setattr("os.remove", lambda p: removed.append(p))

    class BrokenFaiss:
        @staticmethod
        def read_index(p):
            raise RuntimeError("corrupt index")

    monkeypatch.setitem(sys.modules, "faiss", BrokenFaiss())
    e = EmbeddingEngine()
    e.dim = 384
    e._rebuild_faiss_if_needed()
    assert removed != []


def test_rebuild_faiss_with_events(monkeypatch, tmp_path):
    """重建时从 EventStore 取事件重新向量化并写索引（134-147）"""
    from config.settings import settings
    import modules.memory.event_store as es_mod

    monkeypatch.setattr(settings, "MEMORY_FAISS_INDEX", str(tmp_path / "r.idx"))
    monkeypatch.setattr(settings, "MEMORY_ID_MAP", str(tmp_path / "r.json"))
    monkeypatch.setattr("os.path.exists", lambda p: False)

    ev = SimpleNamespace(id="e1", fact="事实", thought="想法", lesson="经验")

    class FakeStore:
        @staticmethod
        def list_events(limit=5000):
            return [ev]

    monkeypatch.setattr(es_mod.EventStore, "get_instance", classmethod(lambda cls: FakeStore()))

    written = {}

    class FakeIdx:
        def __init__(self, dim):
            self.d = dim
            self.ntotal = 0

        def add(self, matrix):
            self.ntotal += matrix.shape[0]

    class FakeFaiss:
        @staticmethod
        def IndexFlatIP(dim):
            return FakeIdx(dim)

        @staticmethod
        def write_index(idx, path):
            written["index"] = (idx, path)

    monkeypatch.setitem(sys.modules, "faiss", FakeFaiss())

    e = EmbeddingEngine()
    e.dim = 8
    e.embed_batch = MagicMock(return_value=[[0.5] * 8])
    e._rebuild_faiss_if_needed()
    assert written["index"][0].ntotal == 1
    id_map = json.loads(open(str(tmp_path / "r.json")).read())
    assert id_map == ["e1"]


def test_rebuild_faiss_event_store_exception(monkeypatch, tmp_path):
    """重建过程 EventStore 异常 → 安全返回（148-149）"""
    from config.settings import settings
    import modules.memory.event_store as es_mod

    monkeypatch.setattr(settings, "MEMORY_FAISS_INDEX", str(tmp_path / "x.idx"))
    monkeypatch.setattr(settings, "MEMORY_ID_MAP", str(tmp_path / "x.json"))
    monkeypatch.setattr("os.path.exists", lambda p: False)
    removed = []
    monkeypatch.setattr("os.remove", lambda p: removed.append(p))

    monkeypatch.setattr(
        es_mod.EventStore, "get_instance",
        classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("store down"))),
    )
    e = EmbeddingEngine()
    e.dim = 8
    e._rebuild_faiss_if_needed()  # 不抛异常
    assert removed == []


# ── embed 成功路径 ────────────────────────────────────────────────────────

def test_embed_success(monkeypatch):
    e = _bare(loaded=True, model=MagicMock(), tokenizer=MagicMock())
    fake_torch = _ctx_mgr(MagicMock())
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    outputs = MagicMock()
    outputs.last_hidden_state.mean.return_value.squeeze.return_value.numpy.return_value = (
        np.array([0.5, 0.5, 0.5, 0.5], dtype=np.float32)
    )
    e._model.return_value = outputs
    result = e.embed("你好")
    assert result is not None
    assert abs(np.sqrt(np.sum(np.array(result) ** 2)) - 1.0) < 1e-5


def test_embed_zero_norm(monkeypatch):
    e = _bare(loaded=True, model=MagicMock(), tokenizer=MagicMock())
    fake_torch = _ctx_mgr(MagicMock())
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    outputs = MagicMock()
    outputs.last_hidden_state.mean.return_value.squeeze.return_value.numpy.return_value = (
        np.zeros(4, dtype=np.float32)
    )
    e._model.return_value = outputs
    result = e.embed("x")
    assert result == [0.0, 0.0, 0.0, 0.0]


def test_embed_encodes_error(monkeypatch):
    e = _bare(loaded=True, model=MagicMock(side_effect=RuntimeError("boom")), tokenizer=MagicMock())
    fake_torch = _ctx_mgr(MagicMock())
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    assert e.embed("x") is None


# ── embed_batch 成功路径 ──────────────────────────────────────────────────

def test_embed_batch_not_loaded_no_model(monkeypatch):
    e = _bare()
    monkeypatch.setattr(e, "_load_model", lambda: False)
    assert e.embed_batch(["a", "b"]) == [None, None]


def test_embed_batch_success(monkeypatch):
    e = _bare(loaded=True, model=MagicMock(), tokenizer=MagicMock())
    fake_torch = _ctx_mgr(MagicMock())
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    outputs = MagicMock()
    outputs.last_hidden_state.mean.return_value.numpy.return_value = (
        np.array([[3.0, 4.0], [1.0, 0.0]], dtype=np.float32)
    )
    e._model.return_value = outputs
    result = e.embed_batch(["a", "b"])
    assert len(result) == 2
    assert abs(np.sqrt(np.sum(np.array(result[0]) ** 2)) - 1.0) < 1e-5
    assert result[1] == [1.0, 0.0]


def test_embed_batch_error(monkeypatch):
    e = _bare(loaded=True, model=MagicMock(side_effect=RuntimeError("boom")), tokenizer=MagicMock())
    fake_torch = _ctx_mgr(MagicMock())
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    assert e.embed_batch(["a", "b"]) == [None, None]
