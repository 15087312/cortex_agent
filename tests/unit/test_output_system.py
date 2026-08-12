"""output_system 端点测试（此前 40% 覆盖）：文本/语音/鼠标/键盘"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import modules.output_system.api as os_api


def _run(coro):
    return asyncio.run(coro)


def test_text_output(monkeypatch):
    osys = MagicMock()
    monkeypatch.setattr(os_api, "OutputSystem", lambda: osys)
    r = _run(os_api.text_output(text="你好"))
    assert r["success"] is True
    assert r["data"]["output"] == "你好"
    osys.output_text.assert_called_with("你好", stream=False)


def test_speech_output_tts_ok(monkeypatch):
    osys = MagicMock()
    monkeypatch.setattr(os_api, "OutputSystem", lambda: osys)
    import modules.output_system.tts as tts_mod
    engine = MagicMock()
    engine.synthesize = AsyncMock(return_value="/tmp/a.mp3")
    monkeypatch.setattr(tts_mod, "TTSEngine", lambda: engine)
    r = _run(os_api.speech_output(text="测试"))
    assert r["success"] is True
    assert r["data"]["audio_url"] == "/tmp/a.mp3"


def test_speech_output_tts_disabled(monkeypatch):
    monkeypatch.setattr(os_api, "OutputSystem", lambda: MagicMock())
    import modules.output_system.tts as tts_mod
    engine = MagicMock()
    engine.synthesize = AsyncMock(return_value=None)
    monkeypatch.setattr(tts_mod, "TTSEngine", lambda: engine)
    r = _run(os_api.speech_output(text="x"))
    assert r["success"] is True
    assert r["data"]["audio_url"] is None


def test_mouse_move(monkeypatch):
    ctrl = MagicMock()
    ctrl.move_to.return_value = True
    monkeypatch.setattr(os_api, "input_controller", ctrl)
    r = _run(os_api.mouse_move(MagicMock(x=10, y=20, duration=0.3)))
    assert r["success"] is True


def test_mouse_click(monkeypatch):
    ctrl = MagicMock()
    ctrl.click.return_value = True
    monkeypatch.setattr(os_api, "input_controller", ctrl)
    r = _run(os_api.mouse_click(MagicMock(x=10, y=20, button="left", clicks=1)))
    assert r["success"] is True


def test_keyboard_type(monkeypatch):
    ctrl = MagicMock()
    ctrl.typewrite.return_value = True
    monkeypatch.setattr(os_api, "input_controller", ctrl)
    r = _run(os_api.keyboard_type(MagicMock(text="hello", interval=0.05)))
    assert r["success"] is True


def test_text_output_error(monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(os_api, "OutputSystem", boom)
    try:
        _run(os_api.text_output(text="x"))
        assert False
    except Exception:
        pass
