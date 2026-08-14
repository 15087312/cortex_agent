"""SpeechRecognizer 扩展测试：初始化 / 流式 / 云端 STT API

关键：whisper / requests 均 mock，不真实加载 openai-whisper（其会拉起 torch）。
"""
import asyncio
import sys
import types
from unittest.mock import MagicMock

import pytest

import infra.data_process.core.speech_recognizer as sr_mod
from config.settings import settings
from infra.data_process.core.speech_recognizer import SpeechRecognizer


def _run(coro):
    return asyncio.run(coro)


def _make(model=None, initialized=False, language="auto", use_local=True, model_name="base"):
    s = SpeechRecognizer.__new__(SpeechRecognizer)
    s.model = model
    s._initialized = initialized
    s.language = language
    s.use_local = use_local
    s.model_name = model_name
    return s


# ── __init__ / initialize ───────────────────────────────────────────────────

def test_init():
    s = SpeechRecognizer(model_name="tiny", language="zh", use_local=True)
    assert s.model_name == "tiny"
    assert s.language == "zh"
    assert s.use_local is True
    assert s.model is None
    assert s._initialized is False


def test_initialize_already_initialized_returns(monkeypatch):
    s = _make(initialized=True)
    called = []

    async def fake_load():
        called.append(1)

    s._load_whisper = fake_load
    _run(s.initialize())
    assert called == []


def test_initialize_not_local_raises():
    s = SpeechRecognizer(use_local=False)
    with pytest.raises(ValueError):
        _run(s.initialize())


def test_initialize_whisper_success(monkeypatch):
    fake = types.ModuleType("whisper")
    fake.load_model = lambda n: "whisper-model"
    monkeypatch.setitem(sys.modules, "whisper", fake)
    s = SpeechRecognizer(model_name="tiny")
    _run(s.initialize())
    assert s.model == "whisper-model"
    assert s._initialized is True


def test_initialize_whisper_missing_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "whisper", None)
    s = SpeechRecognizer(model_name="tiny")
    with pytest.raises(ImportError):
        _run(s.initialize())


# ── recognize 分支补充 ──────────────────────────────────────────────────────

def test_recognize_initializes_automatically(monkeypatch):
    fake = types.ModuleType("whisper")
    model = MagicMock()
    model.transcribe.return_value = {"text": "你好", "language": "zh", "segments": []}
    fake.load_model = lambda n: model
    monkeypatch.setitem(sys.modules, "whisper", fake)
    s = SpeechRecognizer(model_name="tiny")
    r = _run(s.recognize(b"audio"))
    assert r["text"] == "你好"
    assert s._initialized is True


def test_recognize_file(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(b"audio")
    s = _make(initialized=True)
    r = _run(s.recognize_file(str(p)))
    assert "模拟识别结果" in r["text"]


def test_recognize_base64():
    import base64
    s = _make(initialized=True)
    r = _run(s.recognize_base64(base64.b64encode(b"audio").decode()))
    assert "模拟识别结果" in r["text"]


def test_recognize_whisper_explicit_language_and_translate():
    model = MagicMock()
    model.transcribe.return_value = {"text": "hello", "language": "en", "segments": []}
    s = _make(model=model, initialized=True, language="auto")
    r = _run(s.recognize(b"audio", language="en", task="translate"))
    assert r["text"] == "hello"
    assert r["language"] == "en"
    assert r["confidence"] == 0.0
    assert r["duration"] == 0
    from unittest.mock import ANY
    model.transcribe.assert_called_once_with(
        ANY, language="en", task="translate", fp16=False
    )


def test_recognize_whisper_auto_language_passes_none():
    model = MagicMock()
    model.transcribe.return_value = {"text": "t", "language": "zh", "segments": []}
    s = _make(model=model, initialized=True, language="auto")
    _run(s.recognize(b"audio"))
    kwargs = model.transcribe.call_args[1]
    assert kwargs["language"] is None


def test_calculate_confidence_clamped():
    s = SpeechRecognizer()
    assert s._calculate_confidence({"segments": [{"avg_logprob": 10}, {"avg_logprob": -10}]}) == 1.0


# ── recognize_stream / close / 单例 ─────────────────────────────────────────

def test_recognize_stream_yields_per_chunk():
    s = _make(initialized=True)
    results = []

    async def gen():
        for c in (b"1", b"2", b"3"):
            yield c

    async def collect():
        async for r in s.recognize_stream(gen()):
            results.append(r)

    _run(collect())
    assert len(results) == 3
    assert all("模拟识别结果" in r["text"] for r in results)


def test_recognize_stream_skips_empty_chunk():
    s = _make(initialized=True)
    results = []

    async def gen():
        yield b""
        yield b"data"

    async def collect():
        async for r in s.recognize_stream(gen()):
            results.append(r)

    _run(collect())
    assert len(results) == 1


def test_close_releases_model():
    s = _make(model=object(), initialized=True)
    _run(s.close())
    assert s.model is None
    assert s._initialized is False


def test_close_without_model():
    s = _make(model=None, initialized=True)
    _run(s.close())
    assert s.model is None
    assert s._initialized is False


def test_get_default_recognizer_singleton(monkeypatch):
    monkeypatch.setattr(sr_mod, "_default_recognizer", None)

    async def fake_initialize(self):
        self._initialized = True

    monkeypatch.setattr(sr_mod.SpeechRecognizer, "initialize", fake_initialize)
    r1 = _run(sr_mod.get_default_recognizer())
    r2 = _run(sr_mod.get_default_recognizer())
    assert r1 is r2
    assert r1._initialized is True


# ── transcribe_with_api（云端 STT）──────────────────────────────────────────

def _patch_requests(monkeypatch, captured, resp=None, exc=None):
    import requests as requests_mod

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["files"] = files
        captured["data"] = data
        if exc is not None:
            raise exc
        return resp

    monkeypatch.setattr(requests_mod, "post", fake_post)


class _FakeResp:
    def __init__(self, text):
        self._text = text

    def raise_for_status(self):
        pass

    def json(self):
        return {"text": self._text}


def test_transcribe_with_api_no_key_returns_empty(monkeypatch):
    monkeypatch.setattr(settings, "PERCEPTION_VOICE_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    assert sr_mod.transcribe_with_api(b"audio") == ""


def test_transcribe_with_api_success(monkeypatch):
    monkeypatch.setattr(settings, "PERCEPTION_VOICE_API_KEY", "sk")
    monkeypatch.setattr(settings, "PERCEPTION_VOICE_API_URL", "https://api.example.com/v1")
    monkeypatch.setattr(settings, "PERCEPTION_VOICE_API_MODEL", "whisper-1")
    captured = {}
    _patch_requests(monkeypatch, captured, resp=_FakeResp("  你好世界  "))
    out = sr_mod.transcribe_with_api(b"\x00\x00", language="zh", sample_rate=16000)
    assert out == "你好世界"
    assert captured["url"] == "https://api.example.com/v1"
    assert captured["data"] == {"model": "whisper-1", "language": "zh"}
    assert captured["headers"]["Authorization"] == "Bearer sk"
    assert captured["files"]["file"][0] == "audio.wav"


def test_transcribe_with_api_default_url(monkeypatch):
    monkeypatch.setattr(settings, "PERCEPTION_VOICE_API_KEY", "sk")
    monkeypatch.setattr(settings, "PERCEPTION_VOICE_API_URL", "")
    monkeypatch.setattr(settings, "PERCEPTION_VOICE_API_MODEL", "")
    captured = {}
    _patch_requests(monkeypatch, captured, resp=_FakeResp(""))
    assert sr_mod.transcribe_with_api(b"audio") == ""
    assert captured["url"] == "https://api.openai.com/v1/audio/transcriptions"
    assert captured["data"] == {"model": "whisper-1", "language": "zh"}


def test_transcribe_with_api_fallback_openai_key(monkeypatch):
    monkeypatch.setattr(settings, "PERCEPTION_VOICE_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai")
    captured = {}
    _patch_requests(monkeypatch, captured, resp=_FakeResp("ok"))
    assert sr_mod.transcribe_with_api(b"audio", language="auto") == "ok"
    assert "language" not in captured["data"]


def test_transcribe_with_api_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(settings, "PERCEPTION_VOICE_API_KEY", "sk")
    captured = {}
    _patch_requests(monkeypatch, captured, exc=ConnectionError("net down"))
    assert sr_mod.transcribe_with_api(b"audio") == ""


def test_transcribe_with_api_http_error_returns_empty(monkeypatch):
    monkeypatch.setattr(settings, "PERCEPTION_VOICE_API_KEY", "sk")

    class _Err(_FakeResp):
        def raise_for_status(self):
            raise RuntimeError("http 500")

    captured = {}
    _patch_requests(monkeypatch, captured, resp=_Err(""))
    assert sr_mod.transcribe_with_api(b"audio") == ""
