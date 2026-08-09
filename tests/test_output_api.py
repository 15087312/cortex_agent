"""output_system/api 测试（此前 40% 覆盖）：输出端点"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import modules.output_system.api as api_mod


def test_text_output_success(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(api_mod, "OutputSystem", lambda: fake)
    out = asyncio.run(api_mod.text_output(text="你好"))
    assert out["success"] is True
    fake.output_text.assert_called_once()


def test_text_output_failure(monkeypatch):
    monkeypatch.setattr(api_mod, "OutputSystem", lambda: (_ for _ in ()).throw(RuntimeError()))
    from api.errors import AppError
    try:
        asyncio.run(api_mod.text_output(text="x"))
        assert False
    except AppError:
        pass


def test_speech_output_success(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(api_mod, "OutputSystem", lambda: fake)
    import modules.output_system.tts as tts_mod
    engine = MagicMock()
    engine.synthesize = AsyncMock(return_value="/tmp/a.mp3")
    monkeypatch.setattr(tts_mod, "TTSEngine", lambda: engine)
    out = asyncio.run(api_mod.speech_output(text="说话"))
    assert out["success"] is True
    assert out["data"]["audio_url"] == "/tmp/a.mp3"


def test_speech_output_no_audio(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(api_mod, "OutputSystem", lambda: fake)
    import modules.output_system.tts as tts_mod
    engine = MagicMock()
    engine.synthesize = AsyncMock(return_value=None)
    monkeypatch.setattr(tts_mod, "TTSEngine", lambda: engine)
    out = asyncio.run(api_mod.speech_output(text="说话"))
    assert out["data"]["audio_url"] is None
