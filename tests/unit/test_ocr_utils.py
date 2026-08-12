"""ocr_utils / infra.model.interface 测试（此前 0% 覆盖）"""
import sys

import pytest

import utils.ocr_utils as ocr
from infra.model import interface as model_interface


@pytest.fixture
def clean_ocr(monkeypatch):
    """重置 OCR 单例，隔离各分支"""
    ocr._ocr_engine = None
    ocr._ocr_type = None
    yield
    ocr._ocr_engine = None
    ocr._ocr_type = None
    # 还原 sys.modules
    for m in ("paddleocr", "rapidocr_onnxruntime"):
        monkeypatch.delenv if False else None


def test_paddle_branch(monkeypatch, clean_ocr):
    class FakePaddle:
        def __init__(self, lang="ch"):
            pass
    fake_mod = type(sys)("paddleocr")
    fake_mod.PaddleOCR = FakePaddle
    monkeypatch.setitem(sys.modules, "paddleocr", fake_mod)
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)
    eng, typ = ocr.get_ocr_engine()
    assert typ == "paddle"
    assert isinstance(eng, FakePaddle)


def test_rapid_fallback(monkeypatch, clean_ocr):
    monkeypatch.setitem(sys.modules, "paddleocr", None)
    class FakeRapid:
        def __init__(self):
            pass
    fake_mod = type(sys)("rapidocr_onnxruntime")
    fake_mod.RapidOCR = FakeRapid
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", fake_mod)
    eng, typ = ocr.get_ocr_engine()
    assert typ == "rapid"
    assert isinstance(eng, FakeRapid)


def test_none_available(monkeypatch, clean_ocr):
    monkeypatch.setitem(sys.modules, "paddleocr", None)
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)
    eng, typ = ocr.get_ocr_engine()
    assert eng is None
    assert typ is None


def test_singleton_cached(monkeypatch, clean_ocr):
    class FakeRapid:
        def __init__(self):
            pass
    fake_mod = type(sys)("rapidocr_onnxruntime")
    fake_mod.RapidOCR = FakeRapid
    monkeypatch.setitem(sys.modules, "paddleocr", None)
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", fake_mod)
    eng1, t1 = ocr.get_ocr_engine()
    eng2, t2 = ocr.get_ocr_engine()  # 第二次不重新初始化
    assert eng1 is eng2
    assert t1 == t2 == "rapid"


def test_model_interface_re_exports():
    from infra.model.base_model import BaseModelClient, ChatMessage, ChatResponse
    assert model_interface.BaseModelClient is BaseModelClient
    assert model_interface.ChatMessage is ChatMessage
    assert model_interface.ChatResponse is ChatResponse
    assert set(model_interface.__all__) == {"BaseModelClient", "ChatMessage", "ChatResponse"}
