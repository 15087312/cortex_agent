"""output_system/ui_interactor 补充测试：点击 / 输入 / 悬停 / 滚动 / 图像查找"""
import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

from modules.output_system.ui_interactor import ClickResult, UIElement, UIInteractor


def _ui(controller=None, analyzer=None):
    return UIInteractor(controller=controller, image_analyzer=analyzer)


def _ui_no_controller(analyzer=MagicMock()):
    with patch("modules.output_system.input_controller.InputController",
               side_effect=Exception("no ctrl")):
        return UIInteractor(controller=None, image_analyzer=analyzer)


# ── 依赖初始化失败降级 ─────────────────────────────────────────────────────────

def test_init_deps_input_controller_fail(monkeypatch):
    with patch("modules.output_system.input_controller.InputController",
               side_effect=Exception("no ctrl")):
        ui = UIInteractor(controller=None, image_analyzer=MagicMock())
    assert ui.controller is None


def test_init_deps_analyzer_fail(monkeypatch):
    with patch("infra.data_process.core.image_analyzer.ImageAnalyzer",
               side_effect=Exception("no analyzer")):
        ui = UIInteractor(controller=MagicMock(), image_analyzer=None)
    assert ui.image_analyzer is None


# ── 图像查找 ──────────────────────────────────────────────────────────────────

def test_find_element_by_image_success(monkeypatch):
    ui = _ui(controller=MagicMock(), analyzer=MagicMock())
    import utils.screen_capture as sc_mod
    monkeypatch.setattr(sc_mod, "SCREENSHOT_ENABLED", True)
    pag = types.ModuleType("pyautogui")
    loc = MagicMock()
    center = MagicMock()
    center.x = 100
    center.y = 200
    pag.locateOnScreen = lambda *a, **k: loc
    pag.center = lambda l: center
    monkeypatch.setitem(sys.modules, "pyautogui", pag)
    assert ui.find_element_by_image("/tmp/t.png") == (100, 200)


def test_find_element_by_image_no_location(monkeypatch):
    ui = _ui(controller=MagicMock(), analyzer=MagicMock())
    import utils.screen_capture as sc_mod
    monkeypatch.setattr(sc_mod, "SCREENSHOT_ENABLED", True)
    pag = types.ModuleType("pyautogui")
    pag.locateOnScreen = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "pyautogui", pag)
    assert ui.find_element_by_image("/tmp/t.png") is None


def test_find_element_by_image_error(monkeypatch):
    ui = _ui(controller=MagicMock(), analyzer=MagicMock())
    import utils.screen_capture as sc_mod
    monkeypatch.setattr(sc_mod, "SCREENSHOT_ENABLED", True)
    pag = types.ModuleType("pyautogui")
    pag.locateOnScreen = lambda *a, **k: (_ for _ in ()).throw(Exception("img err"))
    monkeypatch.setitem(sys.modules, "pyautogui", pag)
    assert ui.find_element_by_image("/tmp/t.png") is None


def test_find_all_elements_by_image_success(monkeypatch):
    ui = _ui(controller=MagicMock(), analyzer=MagicMock())
    import utils.screen_capture as sc_mod
    monkeypatch.setattr(sc_mod, "SCREENSHOT_ENABLED", True)
    pag = types.ModuleType("pyautogui")
    c1, c2 = MagicMock(), MagicMock()
    c1.x, c1.y = 1, 2
    c2.x, c2.y = 3, 4
    pag.locateAllOnScreen = lambda *a, **k: [MagicMock(), MagicMock()]
    stack = [c1, c2]
    pag.center = lambda l: stack.pop(0)
    monkeypatch.setitem(sys.modules, "pyautogui", pag)
    result = ui.find_all_elements_by_image("/tmp/t.png")
    assert result == [(1, 2), (3, 4)]


def test_find_all_elements_by_image_error(monkeypatch):
    ui = _ui(controller=MagicMock(), analyzer=MagicMock())
    import utils.screen_capture as sc_mod
    monkeypatch.setattr(sc_mod, "SCREENSHOT_ENABLED", True)
    pag = types.ModuleType("pyautogui")
    pag.locateAllOnScreen = lambda *a, **k: (_ for _ in ()).throw(Exception("err"))
    monkeypatch.setitem(sys.modules, "pyautogui", pag)
    assert ui.find_all_elements_by_image("/tmp/t.png") == []


def test_find_element_disabled_screen(monkeypatch):
    ui = _ui(controller=MagicMock(), analyzer=MagicMock())
    import utils.screen_capture as sc_mod
    monkeypatch.setattr(sc_mod, "SCREENSHOT_ENABLED", False)
    assert ui.find_element_by_image("/tmp/t.png") is None


# ── detect_ui_elements 异常 ───────────────────────────────────────────────────

def test_detect_ui_elements_exception(monkeypatch):
    ui = _ui(controller=MagicMock(), analyzer=MagicMock())
    import infra.data_process.core.image_analyzer as ia_mod
    analyzer = MagicMock()
    analyzer.initialize = AsyncMock(return_value=None)
    async def boom(image_data, element_types):
        raise RuntimeError("model fail")
    analyzer.detect_ui_elements = boom
    monkeypatch.setattr(ia_mod, "ImageAnalyzer", lambda: analyzer)
    assert asyncio.run(ui.detect_ui_elements(image_data=b"x")) == []


def test_detect_ui_elements_no_analyzer():
    ui = _ui(controller=MagicMock(), analyzer=None)
    assert asyncio.run(ui.detect_ui_elements(image_data=b"x")) == []


# ── 点击 / 输入 / 悬停 / 滚动 ─────────────────────────────────────────────────

def test_click_element_controller_none():
    ui = _ui_no_controller()
    r = ui.click_element(UIElement(element_type="button"))
    assert r.success is False
    assert "控制器未初始化" in r.message


def test_click_element_success():
    ctrl = MagicMock()
    ctrl.click.return_value = True
    ui = _ui(controller=ctrl, analyzer=MagicMock())
    el = UIElement(element_type="button", text="提交", center_x=10, center_y=20)
    r = ui.click_element(el, random_offset=False)
    assert r.success is True
    assert r.mode == "real"
    assert r.x == 10 and r.y == 20
    ctrl.click.assert_called_with(10, 20)


def test_click_element_failure():
    ctrl = MagicMock()
    ctrl.click.return_value = False
    ui = _ui(controller=ctrl, analyzer=MagicMock())
    el = UIElement(element_type="button", center_x=10, center_y=20)
    r = ui.click_element(el, random_offset=False)
    assert r.success is False
    assert r.message == "点击失败"


def test_click_at_position_success():
    ctrl = MagicMock()
    ctrl.click.return_value = True
    ui = _ui(controller=ctrl, analyzer=MagicMock())
    r = ui.click_at_position(5, 6, random_offset=False)
    assert r.success is True
    ctrl.click.assert_called_with(5, 6)


def test_click_at_position_controller_none():
    ui = _ui_no_controller()
    r = ui.click_at_position(1, 2)
    assert r.success is False


def test_click_element_random_offset():
    ctrl = MagicMock()
    ctrl.click.return_value = True
    ui = _ui(controller=ctrl, analyzer=MagicMock())
    el = UIElement(element_type="button", center_x=100, center_y=100)
    r = ui.click_element(el, random_offset=True, offset_range=0)
    assert r.success is True
    assert r.x == 100 and r.y == 100


def test_click_at_position_random_offset():
    ctrl = MagicMock()
    ctrl.click.return_value = True
    ui = _ui(controller=ctrl, analyzer=MagicMock())
    r = ui.click_at_position(50, 50, random_offset=True, offset_range=0)
    assert r.success is True
    assert r.x == 50 and r.y == 50


def test_find_and_click_returns_mock(monkeypatch):
    ui = _ui(controller=MagicMock(), analyzer=MagicMock())
    with patch("time.sleep"):
        r = ui.find_and_click("登录", element_type="button")
    assert isinstance(r, ClickResult)
    assert r.mode == "mock"
    assert r.success is False


def test_type_at_element_clear_first():
    ctrl = MagicMock()
    ctrl.click.return_value = True
    ctrl.typewrite.return_value = True
    ui = _ui(controller=ctrl, analyzer=MagicMock())
    el = UIElement(element_type="input", center_x=1, center_y=2)
    with patch("time.sleep"):
        ok = ui.type_at_element(el, "你好", clear_first=True)
    assert ok is True
    ctrl.hotkey.assert_called_with("cmd", "a")
    ctrl.press.assert_called_with("backspace")
    ctrl.typewrite.assert_called_with("你好")


def test_type_at_element_no_controller():
    ui = _ui_no_controller()
    assert ui.type_at_element(UIElement(element_type="input"), "x") is False


def test_hover_element_success():
    ctrl = MagicMock()
    ctrl.move_to.return_value = types.SimpleNamespace(success=True)
    ui = _ui(controller=ctrl, analyzer=MagicMock())
    el = UIElement(element_type="menu", center_x=50, center_y=60)
    assert ui.hover_element(el) is True
    ctrl.move_to.assert_called_with(50, 60, duration=0.2)


def test_hover_element_no_controller():
    ui = _ui_no_controller()
    assert ui.hover_element(UIElement(element_type="menu")) is False


def test_scroll_at_element():
    ctrl = MagicMock()
    ctrl.scroll.return_value = True
    ui = _ui(controller=ctrl, analyzer=MagicMock())
    el = UIElement(element_type="list", center_x=7, center_y=8)
    assert ui.scroll_at_element(el, -5) is True
    ctrl.scroll.assert_called_with(-5, 7, 8)


def test_scroll_at_element_no_controller():
    ui = _ui_no_controller()
    assert ui.scroll_at_element(UIElement(element_type="list"), 1) is False


def test_capture_screen_uses_screen_capture(monkeypatch):
    ui = _ui(controller=MagicMock(), analyzer=MagicMock())
    import utils.screen_capture as sc_mod
    monkeypatch.setattr(sc_mod, "SCREENSHOT_ENABLED", True)
    monkeypatch.setattr(sc_mod, "capture_screen_bytes", lambda **k: b"data")
    assert ui.capture_screen(region=(0, 0, 10, 10)) == b"data"
