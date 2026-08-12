"""speech_recognizer 测试（此前 20% 覆盖）：whisper/降级/置信度"""
import asyncio
from unittest.mock import MagicMock

from infra.data_process.core.speech_recognizer import SpeechRecognizer


def _sr(model=None, initialized=False, language="zh", model_name="base"):
    s = SpeechRecognizer.__new__(SpeechRecognizer)
    s.model = model
    s._initialized = initialized
    s.language = language
    s.model_name = model_name
    s.use_local = True
    s.logger = MagicMock()
    return s


def _run(coro):
    return asyncio.run(coro)


def test_recognize_mock_when_no_model():
    s = _sr(model=None, initialized=True)
    r = _run(s.recognize(b"audio"))
    assert "模拟识别结果" in r["text"]


def test_recognize_with_whisper():
    model = MagicMock()
    model.transcribe.return_value = {
        "text": " 你好世界 ",
        "language": "zh",
        "segments": [{"start": 0.0, "end": 1.0, "text": "你好"}, {"start": 1.0, "end": 2.0, "text": "世界", "avg_logprob": -0.2}],
    }
    s = _sr(model=model, initialized=True)
    r = _run(s.recognize(b"audio", task="transcribe"))
    assert r["text"] == "你好世界"
    assert len(r["segments"]) == 2
    assert r["duration"] == 2.0


def test_recognize_initializes_if_needed(monkeypatch):
    # 未初始化 + whisper 缺失 → 加载失败降级 mock
    import sys
    monkeypatch.setitem(sys.modules, "whisper", None)
    import pytest
    s = _sr(model=None, initialized=False)
    with pytest.raises(ImportError):
        _run(s.recognize(b"audio"))  # whisper 缺失时抛 ImportError


def test_calculate_confidence():
    s = _sr()
    assert s._calculate_confidence({}) == 0.0
    r = s._calculate_confidence({"segments": [{"avg_logprob": -0.5}]})
    assert 0.0 <= r <= 1.0
    r2 = s._calculate_confidence({"segments": [{"avg_logprob": 1.0}, {"avg_logprob": 1.0}]})
    assert r2 == pytest_approx(1.0)


def test_recognize_base64():
    import base64
    s = _sr(model=None, initialized=True)
    b64 = base64.b64encode(b"audio").decode()
    r = _run(s.recognize_base64(b64))
    assert "模拟识别结果" in r["text"]


def test_recognize_file(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(b"audio")
    s = _sr(model=None, initialized=True)
    r = _run(s.recognize_file(str(p)))
    assert "模拟识别结果" in r["text"]


def pytest_approx(x, abs=1e-6):
    import pytest
    return pytest.approx(x, abs=abs)
