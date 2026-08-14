"""mouse_keyboard 测试（此前 28% 覆盖）：全部 mock 系统边界，绝不真实控制鼠标键盘

全局 _controller 被替换为 MagicMock，PyAutoGUI/subprocess 全部拦截。
平台分支（win32/darwin/linux 剪贴板）用 monkeypatch sys.platform 模拟。
"""
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from infra.tool_manager.tools import mouse_keyboard


@pytest.fixture
def ctrl(monkeypatch):
    c = MagicMock()
    c.move_to.return_value = True
    c.click.return_value = True
    c.scroll.return_value = True
    c.drag.return_value = True
    c.type_text.return_value = True
    c.press_key.return_value = True
    c.hotkey.return_value = True
    c.get_current_position.return_value = (10, 20)
    monkeypatch.setattr(mouse_keyboard, "_controller", c)
    return c


# ── mouse_move ───────────────────────────────────────────────────────────────

def test_mouse_move_success(ctrl):
    assert mouse_keyboard.mouse_move(10, 20) == "鼠标移动到 (10, 20)"
    ctrl.move_to.assert_called_once_with(10, 20, 0.3)


def test_mouse_move_fail(ctrl):
    ctrl.move_to.return_value = False
    assert mouse_keyboard.mouse_move(1, 2) == "鼠标移动失败，请检查硬件控制器状态"


# ── mouse_click / mouse_double_click ─────────────────────────────────────────

def test_mouse_click_with_position(ctrl):
    assert mouse_keyboard.mouse_click(10, 20) == "鼠标 left 键点击 1 次 at (10, 20)"
    ctrl.click.assert_called_once_with(10, 20, "left", 1)


def test_mouse_click_current_position(ctrl):
    assert mouse_keyboard.mouse_click() == "鼠标 left 键点击 1 次 at 当前位置"
    ctrl.click.assert_called_once_with(None, None, "left", 1)


def test_mouse_click_partial_coords(ctrl):
    assert mouse_keyboard.mouse_click(x=5) == "鼠标 left 键点击 1 次 at 当前位置"


def test_mouse_click_custom_button(ctrl):
    assert mouse_keyboard.mouse_click(x=5, y=5, button="right", clicks=2) == "鼠标 right 键点击 2 次 at (5, 5)"


def test_mouse_click_fail(ctrl):
    ctrl.click.return_value = False
    assert mouse_keyboard.mouse_click(1, 2) == "鼠标点击失败，请检查硬件控制器状态"


def test_mouse_double_click_success(ctrl):
    assert mouse_keyboard.mouse_double_click(10, 20) == "鼠标 left 键双击 at (10, 20)"
    ctrl.click.assert_called_once_with(10, 20, "left", 2)


def test_mouse_double_click_fail(ctrl):
    ctrl.click.return_value = False
    assert mouse_keyboard.mouse_double_click() == "双击失败，请检查硬件控制器状态"


# ── mouse_scroll ─────────────────────────────────────────────────────────────

def test_mouse_scroll_up(ctrl):
    assert mouse_keyboard.mouse_scroll(5) == "鼠标滚轮向上滚动 5 单位"
    ctrl.scroll.assert_called_once_with(5, None, None)


def test_mouse_scroll_down(ctrl):
    assert mouse_keyboard.mouse_scroll(-3, x=1, y=2) == "鼠标滚轮向下滚动 3 单位"
    ctrl.scroll.assert_called_once_with(-3, 1, 2)


def test_mouse_scroll_fail(ctrl):
    ctrl.scroll.return_value = False
    assert mouse_keyboard.mouse_scroll(2) == "滚动失败，请检查硬件控制器状态"


# ── mouse_drag ───────────────────────────────────────────────────────────────

def test_mouse_drag_success(ctrl):
    assert mouse_keyboard.mouse_drag(1, 2, 3, 4) == "鼠标拖拽: (1,2) → (3,4)"
    ctrl.drag.assert_called_once_with(1, 2, 3, 4, 0.5)


def test_mouse_drag_fail(ctrl):
    ctrl.drag.return_value = False
    assert mouse_keyboard.mouse_drag(1, 2, 3, 4) == "拖拽失败，请检查硬件控制器状态"


# ── keyboard_type ────────────────────────────────────────────────────────────

def test_keyboard_type_empty(ctrl):
    assert mouse_keyboard.keyboard_type("") == "[错误] 请输入要输入的文本"


def test_keyboard_type_ascii(ctrl):
    assert mouse_keyboard.keyboard_type("hello") == "键盘输入: hello"
    ctrl.type_text.assert_called_once_with("hello", 0.05)


def test_keyboard_type_ascii_long_preview(ctrl):
    text = "a" * 60
    assert mouse_keyboard.keyboard_type(text) == "键盘输入: " + "a" * 50 + "..."


def test_keyboard_type_ascii_fail(ctrl):
    ctrl.type_text.return_value = False
    assert mouse_keyboard.keyboard_type("hi") == "文本输入失败，请检查硬件控制器状态"


def test_keyboard_type_non_ascii_uses_clipboard(ctrl, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    assert mouse_keyboard.keyboard_type("你好") == "键盘输入(剪贴板): 你好"
    ctrl.hotkey.assert_called_once_with("command", "v")


# ── _type_via_clipboard ──────────────────────────────────────────────────────

def test_clipboard_win32_success(ctrl, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    assert mouse_keyboard._type_via_clipboard("中文") == "键盘输入(剪贴板): 中文"
    ctrl.hotkey.assert_called_once_with("ctrl", "v")


def test_clipboard_win32_fallback_type_text(ctrl, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    assert mouse_keyboard._type_via_clipboard("中文") == "键盘输入: 中文"
    ctrl.type_text.assert_called_once_with("中文", interval=0.1)


def test_clipboard_win32_fallback_fail(ctrl, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    ctrl.type_text.return_value = False
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    assert mouse_keyboard._type_via_clipboard("中文") == "文本输入失败"


def test_clipboard_darwin_success(ctrl, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    assert mouse_keyboard._type_via_clipboard("日本語") == "键盘输入(剪贴板): 日本語"
    ctrl.hotkey.assert_called_once_with("command", "v")


def test_clipboard_linux_xclip(ctrl, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    assert mouse_keyboard._type_via_clipboard("测试") == "键盘输入(剪贴板): 测试"
    ctrl.hotkey.assert_called_once_with("ctrl", "v")


def test_clipboard_linux_xsel_fallback(ctrl, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    fake_run = MagicMock(side_effect=[FileNotFoundError("xclip missing"), SimpleNamespace(returncode=0)])
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert mouse_keyboard._type_via_clipboard("测试") == "键盘输入(剪贴板): 测试"
    ctrl.hotkey.assert_called_once_with("ctrl", "v")
    assert fake_run.call_args[0][0] == ["xsel", "-b", "-i"]


def test_clipboard_long_text_preview(ctrl, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    text = "你" * 60
    assert mouse_keyboard._type_via_clipboard(text) == "键盘输入(剪贴板): " + "你" * 50 + "..."


def test_clipboard_exception_fallback(ctrl, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")

    def run(*a, **k):
        raise OSError("no pbcopy")

    monkeypatch.setattr(subprocess, "run", run)
    assert mouse_keyboard._type_via_clipboard("中文") == "键盘输入: 中文"
    ctrl.type_text.assert_called_once_with("中文", interval=0.1)


def test_clipboard_exception_fallback_fail(ctrl, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    ctrl.type_text.return_value = False
    monkeypatch.setattr(subprocess, "run", MagicMock(side_effect=OSError))
    assert mouse_keyboard._type_via_clipboard("中文") == "文本输入失败"


# ── keyboard_press ───────────────────────────────────────────────────────────

def test_keyboard_press_single(ctrl):
    assert mouse_keyboard.keyboard_press(key="enter") == "按键: enter"
    ctrl.press_key.assert_called_once_with("enter")


def test_keyboard_press_keys_list(ctrl):
    assert mouse_keyboard.keyboard_press(keys=["enter"]) == "按键: enter"
    ctrl.press_key.assert_called_once_with("enter")


def test_keyboard_press_keys_str(ctrl):
    assert mouse_keyboard.keyboard_press(keys="tab") == "按键: tab"
    ctrl.press_key.assert_called_once_with("tab")


def test_keyboard_press_no_key(ctrl):
    assert mouse_keyboard.keyboard_press() == "[错误] 请指定按键，如 keyboard_press(key='enter')"


def test_keyboard_press_keys_nonlist_nonstr(ctrl):
    assert mouse_keyboard.keyboard_press(keys=123) == "[错误] 请指定按键，如 keyboard_press(key='enter')"


def test_keyboard_press_fail(ctrl):
    ctrl.press_key.return_value = False
    assert mouse_keyboard.keyboard_press(key="x") == "按键 x 失败，请检查硬件控制器状态"


# ── keyboard_hotkey ──────────────────────────────────────────────────────────

def test_keyboard_hotkey_keys(ctrl):
    assert mouse_keyboard.keyboard_hotkey(keys=["command", "c"]) == "组合键: command+c"
    ctrl.hotkey.assert_called_once_with("command", "c")


def test_keyboard_hotkey_key_arg(ctrl):
    assert mouse_keyboard.keyboard_hotkey(key="enter") == "组合键: enter"
    ctrl.hotkey.assert_called_once_with("enter")


def test_keyboard_hotkey_str_keys(ctrl):
    assert mouse_keyboard.keyboard_hotkey(keys="enter") == "组合键: enter"
    ctrl.hotkey.assert_called_once_with("enter")


def test_keyboard_hotkey_empty(ctrl):
    assert mouse_keyboard.keyboard_hotkey() == "[错误] 请指定按键，如 keyboard_hotkey(keys=['enter']) 或 keyboard_hotkey(key='enter')"


def test_keyboard_hotkey_none_in_list(ctrl):
    assert mouse_keyboard.keyboard_hotkey(keys=[None]) == "[错误] 请指定按键，如 keyboard_hotkey(keys=['enter']) 或 keyboard_hotkey(key='enter')"


def test_keyboard_hotkey_fail(ctrl):
    ctrl.hotkey.return_value = False
    assert mouse_keyboard.keyboard_hotkey(keys=["a"]) == "组合键 a 失败，请检查硬件控制器状态"


# ── get_mouse_position ───────────────────────────────────────────────────────

def test_get_mouse_position(ctrl):
    assert mouse_keyboard.get_mouse_position() == "当前鼠标位置: (10, 20)"
    ctrl.get_current_position.assert_called_once()
