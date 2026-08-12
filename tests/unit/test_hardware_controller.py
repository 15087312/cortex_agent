"""hardware_input/controller 测试（此前 23% 覆盖）：PyAutoGUI 包装"""
from unittest.mock import MagicMock

from infra.hardware_input.controller import PyAutoGUIController


def _ctrl():
    c = PyAutoGUIController.__new__(PyAutoGUIController)
    c._initialized = True
    c._controller = MagicMock()
    return c


def test_not_initialized():
    c = _ctrl()
    c._initialized = False
    assert c.move_to(10, 10) is False
    assert c.click() is False


def test_apply_randomness_in_range():
    c = _ctrl()
    for _ in range(20):
        rx, ry = c._apply_randomness(100, 100)
        assert abs(rx - 100) <= 3
        assert abs(ry - 100) <= 3


def test_move_to():
    c = _ctrl()
    assert c.move_to(100, 100, duration=0.1) is True
    c._controller.moveTo.assert_called()


def test_move_to_error():
    c = _ctrl()
    c._controller.moveTo.side_effect = Exception("x")
    assert c.move_to(100, 100) is False


def test_click_at_position():
    c = _ctrl()
    assert c.click(100, 200, button="right", clicks=2) is True
    c._controller.click.assert_called()


def test_click_current_position():
    c = _ctrl()
    c._controller.position.return_value = (300, 400)
    assert c.click() is True


def test_scroll():
    c = _ctrl()
    assert c.scroll(-3) is True
    c._controller.scroll.assert_called_with(-3)


def test_drag():
    c = _ctrl()
    assert c.drag(10, 10, 50, 50, duration=0.1) is True
    c._controller.drag.assert_called()


def test_get_current_position():
    c = _ctrl()
    c._controller.position.return_value = (1, 2)
    assert c.get_current_position() == (1, 2)


def test_press_key():
    c = _ctrl()
    assert c.press_key("enter") is True
    c._controller.press.assert_called()


def test_type_text():
    c = _ctrl()
    assert c.type_text("hello") is True
    c._controller.write.assert_called()


def test_hotkey():
    c = _ctrl()
    assert c.hotkey("cmd", "c") is True
    c._controller.hotkey.assert_called_with("cmd", "c")


def test_screenshot(monkeypatch):
    import utils.screen_capture as sc_mod
    monkeypatch.setattr(sc_mod, "SCREENSHOT_ENABLED", True)
    monkeypatch.setattr(sc_mod, "capture_screen_bytes", lambda *a, **k: b"pngdata")
    c = _ctrl()
    out = c.screenshot()
    assert out == b"pngdata"


def test_screenshot_disabled(monkeypatch):
    import utils.screen_capture as sc_mod
    monkeypatch.setattr(sc_mod, "SCREENSHOT_ENABLED", False)
    c = _ctrl()
    assert c.screenshot() is None
