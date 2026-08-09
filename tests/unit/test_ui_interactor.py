"""output_system/ui_interactor 测试（此前 36% 覆盖）：UI 交互器"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from modules.output_system.ui_interactor import UIElement, ClickResult, UIInteractor


def test_ui_element_random_offset():
    el = UIElement(element_type="button", center_x=100, center_y=200)
    x, y = el.random_offset(offset_range=0)
    assert (x, y) == (100, 200)


def test_click_result_defaults():
    r = ClickResult(success=True, element="e", x=1, y=2, mode="click")
    assert r.message == ""


def test_init_with_deps():
    ctrl = MagicMock()
    analyzer = MagicMock()
    ui = UIInteractor(controller=ctrl, image_analyzer=analyzer)
    assert ui.controller is ctrl
    assert ui.image_analyzer is analyzer


def test_init_defaults():
    ic = MagicMock()
    with patch("modules.output_system.input_controller.InputController", lambda: ic):
        ui = UIInteractor(controller=None, image_analyzer=MagicMock())
    assert ui.controller is ic


def test_capture_screen_disabled(monkeypatch):
    ui = UIInteractor(controller=MagicMock(), image_analyzer=MagicMock())
    import utils.screen_capture as sc_mod
    monkeypatch.setattr(sc_mod, "SCREENSHOT_ENABLED", False)
    assert ui.capture_screen() is None


def test_capture_screen_enabled(monkeypatch):
    ui = UIInteractor(controller=MagicMock(), image_analyzer=MagicMock())
    import utils.screen_capture as sc_mod
    monkeypatch.setattr(sc_mod, "SCREENSHOT_ENABLED", True)
    monkeypatch.setattr(sc_mod, "capture_screen_bytes", lambda **k: b"png")
    assert ui.capture_screen() == b"png"


def test_find_element_disabled(monkeypatch):
    ui = UIInteractor(controller=MagicMock(), image_analyzer=MagicMock())
    import utils.screen_capture as sc_mod
    monkeypatch.setattr(sc_mod, "SCREENSHOT_ENABLED", False)
    assert ui.find_element_by_image("/tmp/t.png") is None
    assert ui.find_all_elements_by_image("/tmp/t.png") == []


def test_detect_ui_elements_no_image(monkeypatch):
    ui = UIInteractor(controller=MagicMock(), image_analyzer=MagicMock())
    import utils.screen_capture as sc_mod
    monkeypatch.setattr(sc_mod, "SCREENSHOT_ENABLED", False)
    ui.capture_screen = lambda **k: None
    assert asyncio.run(ui.detect_ui_elements()) == []


def test_detect_ui_elements_success(monkeypatch):
    ui = UIInteractor(controller=MagicMock(), image_analyzer=MagicMock())
    import infra.data_process.core.image_analyzer as ia_mod
    analyzer = MagicMock()
    analyzer.initialize = AsyncMock(return_value=None)
    async def fake_detect(image_data, element_types):
        return {"elements": [{"type": "button", "text": "提交", "bounds": {"x": 0, "y": 0, "width": 10, "height": 20}, "confidence": 0.9}]}
    analyzer.detect_ui_elements = fake_detect
    monkeypatch.setattr(ia_mod, "ImageAnalyzer", lambda: analyzer)
    elements = asyncio.run(ui.detect_ui_elements(image_data=b"x"))
    assert len(elements) == 1
    assert elements[0].element_type == "button"
    assert elements[0].center_x == 5
