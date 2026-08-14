"""output_system/api 补测：全端点（TestClient）+ 错误分支"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import modules.output_system.api as api_mod


API_KEY = "test-secret-key"


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    from config.settings import settings
    monkeypatch.setattr("api.main._SIMPLE_API_KEY", API_KEY)
    monkeypatch.setattr(settings, "SIMPLE_API_KEY", API_KEY)
    yield TestClient(app, raise_server_exceptions=False)


def _post(client, url, **kw):
    kw.setdefault("headers", {"X-API-Key": API_KEY})
    return client.post(url, **kw)


def _get(client, url, **kw):
    kw.setdefault("headers", {"X-API-Key": API_KEY})
    return client.get(url, **kw)


@pytest.fixture
def mock_io(monkeypatch):
    """mock api 模块级 input_controller / ui_interactor / OutputSystem"""
    ic = MagicMock()
    ui = MagicMock()
    osys = MagicMock()
    ic.move_to.return_value = True
    ic.click.return_value = True
    ic.double_click.return_value = True
    ic.right_click.return_value = True
    ic.scroll.return_value = True
    ic.drag.return_value = True
    ic.get_current_position.return_value = (100, 200)
    ic.press.return_value = True
    ic.typewrite.return_value = True
    ic.hotkey.return_value = True
    ic.key_down.return_value = True
    ic.key_up.return_value = True
    ic.pause = MagicMock()
    ic.resume = MagicMock()
    ic.get_status.return_value = {"paused": False, "mouse_position": {"x": 100, "y": 200}, "controller_available": True}
    ui.capture_screen.return_value = b"png-bytes"
    ui.detect_ui_elements = AsyncMock(return_value=[])
    osys.output_text = MagicMock()
    monkeypatch.setattr(api_mod, "input_controller", ic)
    monkeypatch.setattr(api_mod, "ui_interactor", ui)
    monkeypatch.setattr(api_mod, "OutputSystem", MagicMock(return_value=osys))
    return ic, ui, osys


def _click_result(**kw):
    from modules.output_system.ui_interactor import ClickResult
    defaults = dict(success=True, element="button", x=1, y=2, mode="position")
    defaults.update(kw)
    return ClickResult(**defaults)


# ── 文字/语音 ─────────────────────────────────────────────────────────

def test_text_output(client, mock_io):
    r = _post(client, "/output/text", json={"text": "你好"})
    assert r.status_code == 200
    assert r.json()["data"]["output"] == "你好"


def test_text_output_error(client, mock_io):
    mock_io[2].output_text.side_effect = RuntimeError("boom")
    r = _post(client, "/output/text", json={"text": "x"})
    assert r.status_code == 500


def test_speech_output_with_audio(client, mock_io):
    engine = MagicMock()
    engine.synthesize = AsyncMock(return_value="/tmp/a.mp3")
    with patch("modules.output_system.tts.TTSEngine", return_value=engine):
        r = _post(client, "/output/speech", json={"text": "语音"})
    assert r.status_code == 200
    assert r.json()["data"]["audio_url"] == "/tmp/a.mp3"


def test_speech_output_no_audio(client, mock_io):
    engine = MagicMock()
    engine.synthesize = AsyncMock(return_value=None)
    with patch("modules.output_system.tts.TTSEngine", return_value=engine):
        r = _post(client, "/output/speech", json={"text": "语音"})
    assert r.status_code == 200
    assert r.json()["data"]["audio_url"] is None


def test_speech_output_error(client, mock_io):
    engine = MagicMock()
    engine.synthesize = AsyncMock(side_effect=RuntimeError("no tts"))
    with patch("modules.output_system.tts.TTSEngine", return_value=engine):
        r = _post(client, "/output/speech", json={"text": "语音"})
    assert r.status_code == 500


# ── 鼠标 ──────────────────────────────────────────────────────────────

def test_mouse_move(client, mock_io):
    r = _post(client, "/output/mouse/move", json={"x": 10, "y": 20})
    assert r.status_code == 200
    assert r.json()["data"]["x"] == 10
    mock_io[0].move_to.assert_called_once_with(10, 20, 0.3)


def test_mouse_move_error(client, mock_io):
    mock_io[0].move_to.side_effect = RuntimeError("hw")
    r = _post(client, "/output/mouse/move", json={"x": 10, "y": 20})
    assert r.status_code == 500


def test_mouse_click_with_pos(client, mock_io):
    r = _post(client, "/output/mouse/click", json={"x": 5, "y": 6, "button": "left"})
    assert r.status_code == 200
    assert r.json()["data"]["action"] == "click_left_1x"
    mock_io[0].click.assert_called_once_with(5, 6, button="left", clicks=1)


def test_mouse_click_no_pos(client, mock_io):
    r = _post(client, "/output/mouse/click", json={})
    assert r.status_code == 200
    assert r.json()["data"]["x"] == 0
    assert r.json()["data"]["y"] == 0


def test_mouse_click_error(client, mock_io):
    mock_io[0].click.side_effect = RuntimeError("hw")
    r = _post(client, "/output/mouse/click", json={"x": 1, "y": 2})
    assert r.status_code == 500


def test_mouse_double_click(client, mock_io):
    r = _post(client, "/output/mouse/double-click", params={"x": 3, "y": 4})
    assert r.status_code == 200
    assert r.json()["data"]["action"] == "double_click"
    mock_io[0].double_click.assert_called_once_with(3, 4)


def test_mouse_double_click_no_pos(client, mock_io):
    r = _post(client, "/output/mouse/double-click")
    assert r.status_code == 200
    assert r.json()["data"]["x"] == 0


def test_mouse_double_click_error(client, mock_io):
    mock_io[0].double_click.side_effect = RuntimeError("hw")
    r = _post(client, "/output/mouse/double-click")
    assert r.status_code == 500


def test_mouse_right_click(client, mock_io):
    r = _post(client, "/output/mouse/right-click", params={"x": 3, "y": 4})
    assert r.status_code == 200
    assert r.json()["data"]["action"] == "right_click"
    mock_io[0].right_click.assert_called_once_with(3, 4)


def test_mouse_right_click_error(client, mock_io):
    mock_io[0].right_click.side_effect = RuntimeError("hw")
    r = _post(client, "/output/mouse/right-click")
    assert r.status_code == 500


def test_mouse_scroll(client, mock_io):
    r = _post(client, "/output/mouse/scroll", content="3", params={"x": 1, "y": 2})
    assert r.status_code == 200
    assert r.json()["data"]["clicks"] == 3
    mock_io[0].scroll.assert_called_once_with(3, 1, 2)


def test_mouse_scroll_error(client, mock_io):
    mock_io[0].scroll.side_effect = RuntimeError("hw")
    r = _post(client, "/output/mouse/scroll", content="1")
    assert r.status_code == 500


def test_mouse_drag(client, mock_io):
    r = _post(client, "/output/mouse/drag", json={"start_x": 1, "start_y": 2, "end_x": 3, "end_y": 4})
    assert r.status_code == 200
    assert r.json()["data"]["x"] == 3
    mock_io[0].drag.assert_called_once_with(1, 2, 3, 4, 0.5)


def test_mouse_drag_error(client, mock_io):
    mock_io[0].drag.side_effect = RuntimeError("hw")
    r = _post(client, "/output/mouse/drag", json={"start_x": 1, "start_y": 2, "end_x": 3, "end_y": 4})
    assert r.status_code == 500


def test_get_mouse_position(client, mock_io):
    r = _get(client, "/output/mouse/position")
    assert r.status_code == 200
    assert r.json()["data"] == {"x": 100, "y": 200}


def test_get_mouse_position_error(client, mock_io):
    mock_io[0].get_current_position.side_effect = RuntimeError("hw")
    r = _get(client, "/output/mouse/position")
    assert r.status_code == 500


# ── 键盘 ──────────────────────────────────────────────────────────────

def test_keyboard_press(client, mock_io):
    r = _post(client, "/output/keyboard/press", json={"key": "enter"})
    assert r.status_code == 200
    assert r.json()["data"]["action"] == "press"
    mock_io[0].press.assert_called_once_with("enter")


def test_keyboard_press_error(client, mock_io):
    mock_io[0].press.side_effect = RuntimeError("hw")
    r = _post(client, "/output/keyboard/press", json={"key": "enter"})
    assert r.status_code == 500


def test_keyboard_type(client, mock_io):
    r = _post(client, "/output/keyboard/type", json={"text": "abc", "interval": 0.1})
    assert r.status_code == 200
    assert r.json()["data"]["length"] == 3
    mock_io[0].typewrite.assert_called_once_with("abc", 0.1)


def test_keyboard_type_error(client, mock_io):
    mock_io[0].typewrite.side_effect = RuntimeError("hw")
    r = _post(client, "/output/keyboard/type", json={"text": "abc"})
    assert r.status_code == 500


def test_keyboard_hotkey(client, mock_io):
    r = _post(client, "/output/keyboard/hotkey", json={"keys": ["cmd", "c"]})
    assert r.status_code == 200
    assert r.json()["data"]["keys"] == ["cmd", "c"]
    mock_io[0].hotkey.assert_called_once_with("cmd", "c")


def test_keyboard_hotkey_error(client, mock_io):
    mock_io[0].hotkey.side_effect = RuntimeError("hw")
    r = _post(client, "/output/keyboard/hotkey", json={"keys": ["cmd"]})
    assert r.status_code == 500


def test_keyboard_key_down(client, mock_io):
    r = _post(client, "/output/keyboard/key-down", json={"key": "shift"})
    assert r.status_code == 200
    mock_io[0].key_down.assert_called_once_with("shift")


def test_keyboard_key_down_error(client, mock_io):
    mock_io[0].key_down.side_effect = RuntimeError("hw")
    r = _post(client, "/output/keyboard/key-down", json={"key": "shift"})
    assert r.status_code == 500


def test_keyboard_key_up(client, mock_io):
    r = _post(client, "/output/keyboard/key-up", json={"key": "shift"})
    assert r.status_code == 200
    mock_io[0].key_up.assert_called_once_with("shift")


def test_keyboard_key_up_error(client, mock_io):
    mock_io[0].key_up.side_effect = RuntimeError("hw")
    r = _post(client, "/output/keyboard/key-up", json={"key": "shift"})
    assert r.status_code == 500


# ── UI 交互 ───────────────────────────────────────────────────────────

def test_ui_screenshot_success(client, mock_io):
    r = _post(client, "/output/ui/screenshot", params={"x": 0, "y": 0, "width": 10, "height": 10})
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert r.json()["format"] == "base64_png"
    mock_io[1].capture_screen.assert_called_once_with((0, 0, 10, 10))


def test_ui_screenshot_no_region(client, mock_io):
    r = _post(client, "/output/ui/screenshot")
    assert r.status_code == 200
    mock_io[1].capture_screen.assert_called_once_with(None)


def test_ui_screenshot_failure(client, mock_io):
    mock_io[1].capture_screen.return_value = None
    r = _post(client, "/output/ui/screenshot")
    assert r.status_code == 200
    assert r.json()["success"] is False


def test_ui_screenshot_error(client, mock_io):
    mock_io[1].capture_screen.side_effect = RuntimeError("screen")
    r = _post(client, "/output/ui/screenshot")
    assert r.status_code == 500


def test_ui_detect_elements(client, mock_io):
    from modules.output_system.ui_interactor import UIElement
    el = UIElement(element_type="button", text="确定", x=1, y=2, width=3, height=4,
                   confidence=0.9, center_x=5, center_y=6)
    mock_io[1].detect_ui_elements = AsyncMock(return_value=[el])
    r = _post(client, "/output/ui/detect", params={"element_types": "button,input"})
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["count"] == 1
    assert body["data"]["elements"][0]["type"] == "button"


def test_ui_detect_elements_no_types(client, mock_io):
    mock_io[1].detect_ui_elements = AsyncMock(return_value=[])
    r = _post(client, "/output/ui/detect")
    assert r.status_code == 200
    assert r.json()["data"]["count"] == 0


def test_ui_detect_elements_error(client, mock_io):
    mock_io[1].detect_ui_elements = AsyncMock(side_effect=RuntimeError("detect"))
    r = _post(client, "/output/ui/detect")
    assert r.status_code == 500


def test_ui_click_by_text(client, mock_io):
    mock_io[1].find_and_click = MagicMock(return_value=_click_result(element="确定", mode="text"))
    r = _post(client, "/output/ui/click", json={"element_text": "确定", "element_type": "button"})
    assert r.status_code == 200
    assert r.json()["data"]["mode"] == "text"
    mock_io[1].find_and_click.assert_called_once_with("确定", "button")


def test_ui_click_by_position(client, mock_io):
    mock_io[1].click_at_position = MagicMock(return_value=_click_result(mode="position"))
    r = _post(client, "/output/ui/click", json={"x": 5, "y": 6, "random_offset": True, "offset_range": 5})
    assert r.status_code == 200
    mock_io[1].click_at_position.assert_called_once_with(5, 6, True, 5)


def test_ui_click_current_position(client, mock_io):
    mock_io[1].click_at_position = MagicMock(return_value=_click_result(mode="current"))
    r = _post(client, "/output/ui/click", json={})
    assert r.status_code == 200
    mock_io[1].click_at_position.assert_called_once_with(100, 200, True, 5)


def test_ui_click_error(client, mock_io):
    mock_io[1].find_and_click = MagicMock(side_effect=RuntimeError("click"))
    r = _post(client, "/output/ui/click", json={"element_text": "x"})
    assert r.status_code == 500


def test_ui_hover(client, mock_io):
    r = _post(client, "/output/ui/hover", json={"x": 5, "y": 6})
    assert r.status_code == 200
    mock_io[0].move_to.assert_called_once_with(5, 6, duration=0.3)


def test_ui_hover_error(client, mock_io):
    mock_io[0].move_to.side_effect = RuntimeError("hw")
    r = _post(client, "/output/ui/hover", json={"x": 5, "y": 6})
    assert r.status_code == 500


def test_ui_type_with_pos(client, mock_io):
    r = _post(client, "/output/ui/type", content="你好", params={"x": 1, "y": 2})
    assert r.status_code == 200
    mock_io[0].click.assert_called_once_with(1, 2)
    mock_io[0].typewrite.assert_called_once_with("你好")


def test_ui_type_no_pos(client, mock_io):
    r = _post(client, "/output/ui/type", content="你好")
    assert r.status_code == 200
    mock_io[0].click.assert_not_called()
    mock_io[0].typewrite.assert_called_once_with("你好")


def test_ui_type_error(client, mock_io):
    mock_io[0].typewrite.side_effect = RuntimeError("hw")
    r = _post(client, "/output/ui/type", content="你好")
    assert r.status_code == 500


# ── 控制 / 状态 ───────────────────────────────────────────────────────

def test_pause_resume_controller(client, mock_io):
    r = _post(client, "/output/controller/pause")
    assert r.status_code == 200
    mock_io[0].pause.assert_called_once()
    r = _post(client, "/output/controller/resume")
    assert r.status_code == 200
    mock_io[0].resume.assert_called_once()


def test_get_status(client, mock_io):
    r = _get(client, "/output/status")
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["module"] == "output"
    assert body["data"]["controller"]["paused"] is False


# ── 鉴权 ──────────────────────────────────────────────────────────────

def test_output_requires_auth(client, mock_io):
    r = client.post("/output/text", json={"text": "x"})
    assert r.status_code == 401
