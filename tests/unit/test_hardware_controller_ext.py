"""hardware_input/controller 补充测试：SerialController + PyAutoGUI 初始化/异常分支"""
import sys
import types
from unittest.mock import MagicMock, patch

from infra.hardware_input.controller import (
    HardwareInputController,
    PyAutoGUIController,
    SerialController,
)


# ── 基类：所有方法抛 NotImplementedError ──────────────────────────────────────

def test_base_class_not_implemented():
    b = HardwareInputController()
    assert b._initialized is False
    for call in [
        lambda: b.move_to(1, 1),
        lambda: b.click(),
        lambda: b.scroll(1),
        lambda: b.drag(1, 2, 3, 4),
        lambda: b.get_current_position(),
        lambda: b.press_key("a"),
        lambda: b.type_text("x"),
        lambda: b.key_down("a"),
        lambda: b.key_up("a"),
        lambda: b.hotkey("a"),
        lambda: b.screenshot(),
    ]:
        try:
            call()
            assert False, "should raise NotImplementedError"
        except NotImplementedError:
            pass


# ── PyAutoGUIController 初始化 ────────────────────────────────────────────────

def test_pyautogui_init_success(monkeypatch):
    import types as _t
    pag = _t.ModuleType("pyautogui")
    pag.FAILSAFE = True
    pag.PAUSE = 0.1
    monkeypatch.setitem(sys.modules, "pyautogui", pag)
    c = PyAutoGUIController()
    assert c._initialized is True
    assert c._controller is pag


def test_pyautogui_init_importerror(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyautogui", None)
    c = PyAutoGUIController()
    assert c._initialized is False
    # 未初始化时所有操作安全降级
    assert c.move_to(1, 1) is False
    assert c.screenshot() is None


def test_not_initialized_all_methods_degrade():
    c = PyAutoGUIController.__new__(PyAutoGUIController)
    c._initialized = False
    assert c.click(1, 1) is False
    assert c.scroll(1) is False
    assert c.drag(1, 2, 3, 4) is False
    assert c.get_current_position() == (0, 0)
    assert c.press_key("a") is False
    assert c.type_text("x") is False
    assert c.key_down("a") is False
    assert c.key_up("a") is False
    assert c.hotkey("a") is False


def _ctrl():
    c = PyAutoGUIController.__new__(PyAutoGUIController)
    c._initialized = True
    c._controller = MagicMock()
    return c


# ── PyAutoGUIController 分支补充 ──────────────────────────────────────────────

def test_click_exception():
    c = _ctrl()
    c._controller.click.side_effect = Exception("boom")
    assert c.click(10, 10) is False


def test_scroll_with_coords():
    c = _ctrl()
    assert c.scroll(5, x=10, y=20) is True
    c._controller.moveTo.assert_called_with(10, 20)
    c._controller.scroll.assert_called_with(5)


def test_scroll_exception():
    c = _ctrl()
    c._controller.scroll.side_effect = Exception("boom")
    assert c.scroll(5) is False


def test_drag_exception():
    c = _ctrl()
    c._controller.drag.side_effect = Exception("boom")
    assert c.drag(1, 2, 3, 4) is False


def test_get_position_exception():
    c = _ctrl()
    c._controller.position.side_effect = Exception("boom")
    assert c.get_current_position() == (0, 0)


def test_press_key_exception():
    c = _ctrl()
    c._controller.press.side_effect = Exception("boom")
    assert c.press_key("enter") is False


def test_type_text_exception():
    c = _ctrl()
    c._controller.write.side_effect = Exception("boom")
    assert c.type_text("x") is False


def test_key_down_up():
    c = _ctrl()
    assert c.key_down("cmd") is True
    c._controller.keyDown.assert_called_with("cmd")
    assert c.key_up("cmd") is True
    c._controller.keyUp.assert_called_with("cmd")


def test_key_down_up_exception():
    c = _ctrl()
    c._controller.keyDown.side_effect = Exception("boom")
    assert c.key_down("cmd") is False
    c._controller.keyUp.side_effect = Exception("boom")
    assert c.key_up("cmd") is False


def test_hotkey_exception():
    c = _ctrl()
    c._controller.hotkey.side_effect = Exception("boom")
    assert c.hotkey("cmd", "c") is False


def test_screenshot_exception(monkeypatch):
    import utils.screen_capture as sc_mod
    monkeypatch.setattr(sc_mod, "SCREENSHOT_ENABLED", True)
    monkeypatch.setattr(sc_mod, "capture_screen_bytes", lambda *a, **k: (_ for _ in ()).throw(Exception("no")))
    c = _ctrl()
    assert c.screenshot() is None


def test_screenshot_not_initialized():
    c = _ctrl()
    c._initialized = False
    assert c.screenshot() is None


# ── SerialController ──────────────────────────────────────────────────────────

def _serial_ctrl(serial_mock=None, initialized=True):
    c = SerialController.__new__(SerialController)
    c._port = "COM3"
    c._baudrate = 115200
    c._serial = serial_mock
    c._initialized = initialized
    return c


def test_serial_init_success(monkeypatch):
    fake = MagicMock()
    fake_serial = types.ModuleType("serial")
    fake_serial.Serial = lambda port, baud, timeout: fake
    monkeypatch.setitem(sys.modules, "serial", fake_serial)
    c = SerialController(port="COM1", baudrate=9600)
    assert c._initialized is True
    assert c._serial is fake
    assert c._port == "COM1"
    assert c._baudrate == 9600


def test_serial_init_importerror(monkeypatch):
    monkeypatch.setitem(sys.modules, "serial", None)
    c = SerialController()
    assert c._initialized is False
    assert c._serial is None


def test_serial_init_error(monkeypatch):
    def boom(*a, **k):
        raise Exception("no device")
    fake_serial = types.ModuleType("serial")
    fake_serial.Serial = staticmethod(boom)
    monkeypatch.setitem(sys.modules, "serial", fake_serial)
    c = SerialController()
    assert c._initialized is False


def test_send_command_no_serial():
    c = _serial_ctrl(serial_mock=None)
    assert c._send_command("MOVE:1,2") is False


def test_send_command_exception():
    m = MagicMock()
    m.write.side_effect = Exception("busy")
    c = _serial_ctrl(serial_mock=m)
    assert c._send_command("MOVE:1,2") is False


def test_serial_all_commands():
    m = MagicMock()
    c = _serial_ctrl(serial_mock=m)
    assert c.move_to(1, 2) is True
    m.write.assert_called_with(b"MOVE:1,2\n")
    assert c.click(1, 2, button="right") is True
    m.write.assert_called_with(b"CLICK:right\n")
    assert c.scroll(-3) is True
    m.write.assert_called_with(b"SCROLL:-3\n")
    assert c.drag(1, 2, 3, 4) is True
    m.write.assert_called_with(b"DRAG:1,2,3,4\n")
    assert c.get_current_position() == (0, 0)
    assert c.press_key("enter") is True
    m.write.assert_called_with(b"KEY:enter\n")
    assert c.type_text("hi") is True
    m.write.assert_called_with(b"TYPE:hi\n")
    assert c.key_down("a") is True
    m.write.assert_called_with(b"KEYDOWN:a\n")
    assert c.key_up("a") is True
    m.write.assert_called_with(b"KEYUP:a\n")
    assert c.hotkey("cmd", "c") is True
    m.write.assert_called_with(b"HOTKEY:cmd+c\n")
    assert c.screenshot() is None
