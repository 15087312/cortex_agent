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
