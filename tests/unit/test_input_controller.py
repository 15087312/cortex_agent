"""InputController 测试：鼠标/键盘/控制/工厂/代理"""
from unittest.mock import MagicMock, patch

import sys
import modules.output_system.input_controller as ic_mod
from modules.output_system.input_controller import InputController, Point, get_input_controller, input_controller

ic_mod = sys.modules["modules.output_system.input_controller"]


def test_point_offset():
    p = Point(10, 20)
    q = p.offset(5, -3)
    assert (q.x, q.y) == (15, 17)
    assert p.offset() == Point(10, 20)


def _make_ctrl(force=False):
    fake_controller = MagicMock()
    for m in ("move_to", "click", "scroll", "drag", "press_key", "type_text",
              "key_down", "key_up", "hotkey"):
        getattr(fake_controller, m).return_value = True
    with patch("modules.output_system.input_controller.PyAutoGUIController", return_value=fake_controller):
        ctrl = InputController(force=force)
    ctrl._controller = fake_controller
    return ctrl, fake_controller


def test_init_idempotent_and_force():
    with patch("modules.output_system.input_controller.PyAutoGUIController") as ctor:
        c1 = InputController()
        c1.__init__()  # 同一实例二次 init → 直接返回
        assert ctor.call_count == 1
        c1.__init__(force=True)  # force 重新初始化
        assert ctor.call_count == 2


# ── 鼠标操作 ───────────────────────────────────────────────────────────

def test_move_to():
    ctrl, fc = _make_ctrl()
    assert ctrl.move_to(1, 2) is True
    fc.move_to.assert_called_once_with(1, 2, 0.3)


def test_move_to_custom_duration():
    ctrl, fc = _make_ctrl()
    assert ctrl.move_to(1, 2, 1.5) is True
    fc.move_to.assert_called_once_with(1, 2, 1.5)


def test_click():
    ctrl, fc = _make_ctrl()
    assert ctrl.click(1, 2, "left") is True
    fc.click.assert_called_once()
    args = fc.click.call_args[0]
    assert args[0:2] == (1, 2)


def test_click_defaults():
    ctrl, fc = _make_ctrl()
    assert ctrl.click() is True
    args = fc.click.call_args.args
    assert args[0:2] == (None, None)
    assert args[2] == "left"
    assert args[3] == 1
    assert args[4] == 0.1


def test_double_click():
    ctrl, fc = _make_ctrl()
    assert ctrl.double_click(3, 4) is True
    args = fc.click.call_args.args
    assert args[3] == 2  # clicks
    assert args[2] == "left"


def test_right_click():
    ctrl, fc = _make_ctrl()
    assert ctrl.right_click(5, 6) is True
    assert fc.click.call_args.args[2] == "right"


def test_middle_click():
    ctrl, fc = _make_ctrl()
    assert ctrl.middle_click(5, 6) is True
    assert fc.click.call_args.args[2] == "middle"


def test_scroll():
    ctrl, fc = _make_ctrl()
    assert ctrl.scroll(3, 10, 20) is True
    fc.scroll.assert_called_once_with(3, 10, 20)


def test_drag():
    ctrl, fc = _make_ctrl()
    assert ctrl.drag(1, 2, 3, 4) is True
    fc.drag.assert_called_once_with(1, 2, 3, 4, 0.5)


def test_get_current_position():
    ctrl, fc = _make_ctrl()
    fc.get_current_position.return_value = (7, 8)
    assert ctrl.get_current_position() == (7, 8)


# ── 键盘操作 ───────────────────────────────────────────────────────────

def test_press():
    ctrl, fc = _make_ctrl()
    assert ctrl.press("enter") is True
    fc.press_key.assert_called_once_with("enter")


def test_typewrite():
    ctrl, fc = _make_ctrl()
    assert ctrl.typewrite("hi", 0.2) is True
    fc.type_text.assert_called_once_with("hi", 0.2)


def test_typewrite_default_interval():
    ctrl, fc = _make_ctrl()
    assert ctrl.typewrite("hi") is True
    fc.type_text.assert_called_once_with("hi", 0.05)


def test_key_down():
    ctrl, fc = _make_ctrl()
    assert ctrl.key_down("shift") is True
    fc.key_down.assert_called_once_with("shift")


def test_key_up():
    ctrl, fc = _make_ctrl()
    assert ctrl.key_up("shift") is True
    fc.key_up.assert_called_once_with("shift")


def test_hotkey():
    ctrl, fc = _make_ctrl()
    assert ctrl.hotkey("cmd", "c") is True
    fc.hotkey.assert_called_once_with("cmd", "c")


def test_screenshot():
    ctrl, fc = _make_ctrl()
    fc.screenshot.return_value = b"png"
    assert ctrl.screenshot((0, 0, 10, 10)) == b"png"
    fc.screenshot.assert_called_once_with((0, 0, 10, 10))


# ── 暂停分支 ───────────────────────────────────────────────────────────

def test_paused_ignores_operations():
    ctrl, fc = _make_ctrl()
    ctrl._paused = True
    assert ctrl.move_to(1, 2) is False
    assert ctrl.click(1, 2) is False
    assert ctrl.scroll(1) is False
    assert ctrl.drag(1, 2, 3, 4) is False
    assert ctrl.press("a") is False
    assert ctrl.typewrite("a") is False
    assert ctrl.key_down("a") is False
    assert ctrl.key_up("a") is False
    assert ctrl.hotkey("a") is False
    assert ctrl.screenshot() is None
    fc.move_to.assert_not_called()
    fc.click.assert_not_called()
    fc.scroll.assert_not_called()
    fc.drag.assert_not_called()
    fc.press_key.assert_not_called()
    fc.type_text.assert_not_called()
    fc.key_down.assert_not_called()
    fc.key_up.assert_not_called()
    fc.hotkey.assert_not_called()
    fc.screenshot.assert_not_called()


# ── 控制 / 状态 ────────────────────────────────────────────────────────

def test_pause_resume():
    ctrl, fc = _make_ctrl()
    assert ctrl._paused is False
    ctrl.pause()
    assert ctrl._paused is True
    ctrl.resume()
    assert ctrl._paused is False


def test_get_status():
    ctrl, fc = _make_ctrl()
    fc.get_current_position.return_value = (1, 2)
    fc._initialized = True
    st = ctrl.get_status()
    assert st["paused"] is False
    assert st["mouse_position"] == {"x": 1, "y": 2}
    assert st["controller_available"] is True


# ── 工厂 / 代理 ────────────────────────────────────────────────────────

def test_get_input_controller_singleton(monkeypatch):
    monkeypatch.setattr(ic_mod, "_input_controller_instance", None)
    with patch("modules.output_system.input_controller.PyAutoGUIController") as ctor:
        a = get_input_controller()
        b = get_input_controller()
    assert a is b
    assert ctor.call_count == 1


def test_proxy_delegates(monkeypatch):
    fake = MagicMock()
    fake.move_to.return_value = True
    monkeypatch.setattr(ic_mod, "get_input_controller", lambda: fake)
    assert input_controller.move_to(1, 2) is True
    fake.move_to.assert_called_once_with(1, 2)
