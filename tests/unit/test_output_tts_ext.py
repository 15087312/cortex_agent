"""output_system/tts 补充测试：gTTS 失败分支 / 云端 API 后端"""
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from config.settings import settings
from modules.output_system.tts import DEFAULT_TTS_OUTPUT_DIR, TTSEngine


def test_default_output_dir():
    assert isinstance(DEFAULT_TTS_OUTPUT_DIR, Path)


@pytest.fixture
def tts_enabled(monkeypatch):
    monkeypatch.setattr(settings, "OUTPUT_TTS_ENABLED", True)


def test_available_cached(monkeypatch):
    fake_mod = types.ModuleType("gtts")
    monkeypatch.setitem(sys.modules, "gtts", fake_mod)
    engine = TTSEngine()
    engine._available = None
    assert engine.available is True
    # 已缓存 True，即使后续 import 失败也保持
    monkeypatch.setitem(sys.modules, "gtts", None)
    assert engine.available is True


def test_synthesize_gtts_unavailable(tts_enabled, monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "gtts", None)
    engine = TTSEngine(output_dir=str(tmp_path))
    engine._available = False
    assert engine.synthesize_sync("你好") is None


def test_synthesize_gtts_failure(tts_enabled, monkeypatch, tmp_path):
    fake_mod = types.ModuleType("gtts")
    class BoomGtts:
        def __init__(self, **kw):
            pass
        def save(self, path):
            raise RuntimeError("network error")
    fake_mod.gTTS = BoomGtts
    monkeypatch.setitem(sys.modules, "gtts", fake_mod)
    engine = TTSEngine(output_dir=str(tmp_path))
    assert engine.synthesize_sync("你好") is None


def test_synthesize_api_backend_dispatches(tts_enabled, monkeypatch, tmp_path):
    engine = TTSEngine(output_dir=str(tmp_path))
    monkeypatch.setattr(engine, "_synthesize_api", lambda text, language=None: "/tmp/api.mp3")
    monkeypatch.setattr(settings, "OUTPUT_TTS_BACKEND", "api")
    assert engine.synthesize_sync("你好") == "/tmp/api.mp3"


def test_synthesize_api_no_key(tts_enabled, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OUTPUT_TTS_BACKEND", "api")
    monkeypatch.setattr(settings, "OUTPUT_TTS_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    engine = TTSEngine(output_dir=str(tmp_path))
    assert engine._synthesize_api("你好") is None


def test_synthesize_api_success(tts_enabled, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OUTPUT_TTS_BACKEND", "api")
    monkeypatch.setattr(settings, "OUTPUT_TTS_API_KEY", "sk-test")
    import requests as req_mod
    class Resp:
        content = b"MP3DATA"
        def raise_for_status(self):
            return None
    captured = {}
    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return Resp()
    monkeypatch.setattr(req_mod, "post", fake_post)
    engine = TTSEngine(output_dir=str(tmp_path))
    out = engine._synthesize_api("你好")
    assert out is not None
    assert Path(out).suffix == ".mp3"
    assert Path(out).read_bytes() == b"MP3DATA"
    assert captured["json"]["model"] == "tts-1"
    assert captured["json"]["voice"] == "alloy"
    assert captured["timeout"] == 30


def test_synthesize_api_http_error(tts_enabled, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OUTPUT_TTS_BACKEND", "api")
    monkeypatch.setattr(settings, "OUTPUT_TTS_API_KEY", "sk-test")
    import requests as req_mod
    class Err:
        def raise_for_status(self):
            raise req_mod.exceptions.HTTPError("401")
    monkeypatch.setattr(req_mod, "post", lambda *a, **k: Err())
    engine = TTSEngine(output_dir=str(tmp_path))
    assert engine._synthesize_api("你好") is None


def test_synthesize_api_language_voice_overrides(tts_enabled, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OUTPUT_TTS_BACKEND", "api")
    monkeypatch.setattr(settings, "OUTPUT_TTS_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "OUTPUT_TTS_API_MODEL", "tts-1-hd")
    monkeypatch.setattr(settings, "OUTPUT_TTS_API_VOICE", "nova")
    monkeypatch.setattr(settings, "OUTPUT_TTS_API_URL", "https://custom.example/speech")
    import requests as req_mod
    class Resp:
        content = b"x"
        def raise_for_status(self):
            return None
    captured = {}
    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs["json"]
        captured["headers"] = kwargs["headers"]
        return Resp()
    monkeypatch.setattr(req_mod, "post", fake_post)
    engine = TTSEngine(output_dir=str(tmp_path))
    assert engine._synthesize_api("你好") is not None
    assert captured["url"] == "https://custom.example/speech"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["json"]["voice"] == "nova"
