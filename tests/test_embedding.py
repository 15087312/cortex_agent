"""memory/embedding 测试（此前 21% 覆盖）：向量化引擎"""
from unittest.mock import MagicMock, patch

from modules.memory.embedding import EmbeddingEngine


def _engine(**kw):
    e = EmbeddingEngine.__new__(EmbeddingEngine)
    e._loaded = kw.get("loaded", False)
    e._model = kw.get("model", None)
    e._tokenizer = kw.get("tokenizer", None)
    return e


def test_embed_not_loaded_and_fails_to_load(monkeypatch):
    e = _engine()
    monkeypatch.setattr(e, "_load_model", lambda: False)
    assert e.embed("text") is None
    assert e._loaded is False


def test_embed_loaded_no_model():
    e = _engine(loaded=True, model=None)
    assert e.embed("text") is None


def test_embed_model_exception(monkeypatch):
    import sys
    e = _engine(loaded=True, model=MagicMock(), tokenizer=MagicMock())
    fake_torch = MagicMock()
    fake_torch.no_grad = lambda: fake_torch
    fake_torch.__enter__ = lambda self: self
    fake_torch.__exit__ = lambda self, *a: False
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    e._tokenizer.return_value = {"input_ids": object()}
    assert e.embed("text") is None


def test_embed_batch_empty():
    e = _engine()
    assert e.embed_batch([]) == []


def test_embed_batch_not_loaded(monkeypatch):
    e = _engine()
    monkeypatch.setattr(e, "_load_model", lambda: False)
    assert e.embed_batch(["a", "b"]) == [None, None]


def test_get_instance_singleton(monkeypatch):
    monkeypatch.setattr(EmbeddingEngine, "_instance", None)
    a = EmbeddingEngine.get_instance()
    b = EmbeddingEngine.get_instance()
    assert a is b


def test_load_model_failure(monkeypatch):
    import sys
    import modules.memory.embedding as emb_mod
    class FakeTransformers:
        AutoModel = None
        AutoTokenizer = None
    monkeypatch.setitem(sys.modules, "transformers", FakeTransformers())
    e = EmbeddingEngine()
    assert e._load_model() is False
    assert e._loaded is False
    assert e._attempted is True
    assert e.embed("x") is None


def test_load_model_success(monkeypatch):
    import sys
    import modules.memory.embedding as emb_mod
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
    assert e._load_model() is True
    assert e.dim == 384
    e._rebuild_faiss_if_needed.assert_called_once()


def test_rebuild_faiss_dim_same(monkeypatch):
    import modules.memory.embedding as emb_mod
    e = EmbeddingEngine()
    e.dim = 384
    monkeypatch.setattr("os.path.exists", lambda p: True)
    class FakeFaiss:
        @staticmethod
        def read_index(p):
            class I:
                d = 384
            return I()
    import sys
    monkeypatch.setitem(sys.modules, "faiss", FakeFaiss())
    e._rebuild_faiss_if_needed()  # 维度一致不重建


def test_rebuild_faiss_dim_mismatch(monkeypatch):
    import modules.memory.embedding as emb_mod
    e = EmbeddingEngine()
    e.dim = 384
    monkeypatch.setattr("os.path.exists", lambda p: True)
    monkeypatch.setattr("os.remove", lambda p: None)
    class FakeFaiss:
        @staticmethod
        def read_index(p):
            class I:
                d = 768
            return I()
    import sys
    monkeypatch.setitem(sys.modules, "faiss", FakeFaiss())
    import modules.memory.event_store as es_mod
    monkeypatch.setattr(es_mod.EventStore, "get_instance", classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("no store"))))
    e._rebuild_faiss_if_needed()  # 重建过程 EventStore 异常 → 安全返回
