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


def test_mouse_move(monkeypatch):
    ctrl = MagicMock()
    ctrl.move_to.return_value = True
    monkeypatch.setattr(api_mod, "input_controller", ctrl)
    out = asyncio.run(api_mod.mouse_move(api_mod.MouseMoveRequest(x=1, y=2)))
    assert out["success"] is True
    ctrl.move_to.assert_called_once_with(1, 2, 0.3)


def test_mouse_click(monkeypatch):
    ctrl = MagicMock()
    ctrl.click.return_value = True
    monkeypatch.setattr(api_mod, "input_controller", ctrl)
    out = asyncio.run(api_mod.mouse_click(api_mod.MouseClickRequest(x=1, y=2, button="left", clicks=1)))
    assert out["success"] is True


def test_mouse_double_and_right_click(monkeypatch):
    ctrl = MagicMock()
    ctrl.double_click.return_value = True
    ctrl.right_click.return_value = True
    monkeypatch.setattr(api_mod, "input_controller", ctrl)
    assert asyncio.run(api_mod.mouse_double_click(x=1, y=2))["success"] is True
    assert asyncio.run(api_mod.mouse_right_click(x=1, y=2))["success"] is True


def test_mouse_scroll_and_drag(monkeypatch):
    ctrl = MagicMock()
    ctrl.scroll.return_value = True
    ctrl.drag.return_value = True
    monkeypatch.setattr(api_mod, "input_controller", ctrl)
    assert asyncio.run(api_mod.mouse_scroll(clicks=3, x=1, y=2))["success"] is True
    out = asyncio.run(api_mod.mouse_drag(api_mod.MouseDragRequest(start_x=0, start_y=0, end_x=5, end_y=5, duration=0.5)))
    assert out["success"] is True


def test_get_mouse_position(monkeypatch):
    ctrl = MagicMock()
    ctrl.get_current_position.return_value = (10, 20)
    monkeypatch.setattr(api_mod, "input_controller", ctrl)
    out = asyncio.run(api_mod.get_mouse_position())
    assert out["data"] == {"x": 10, "y": 20}


def test_keyboard_press(monkeypatch):
    ctrl = MagicMock()
    ctrl.press.return_value = True
    monkeypatch.setattr(api_mod, "input_controller", ctrl)
    out = asyncio.run(api_mod.keyboard_press(api_mod.KeyboardPressRequest(key="a", modifier=None)))
    assert out["success"] is True
