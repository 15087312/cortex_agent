"""perception/screen/router 测试（此前 35% 覆盖）：检测路由"""
import asyncio
from unittest.mock import MagicMock, patch

from modules.perception.screen.router import DetectorRouter, get_detector_router
from modules.perception.screen.context import ScreenContext, UIElement


def _router(**kw):
    r = DetectorRouter.__new__(DetectorRouter)
    tp = kw.get("touchpoint", None)
    if tp is None:
        tp = MagicMock()
        tp.is_available.return_value = True
        ctx = ScreenContext(app_name="app", backend="touchpoint")
        ctx.element_count = 10
        tp.detect.return_value = ctx
    r._touchpoint = tp
    vision = kw.get("vision")
    if vision is None:
        vision = MagicMock()
        vision.is_available.return_value = False
    r._vision = vision
    r._cdp = kw.get("cdp", None)
    return r


def test_detect_native_flow():
    r = _router()
    r._get_active_app = lambda: "Firefox"
    r._is_chromium_app = lambda a: False
    result = r.detect()
    assert result.backend == "touchpoint"


def test_detect_chromium_cdp():
    cdp = MagicMock()
    cdp.find_chromium_ports.return_value = [{"port": 9222}]
    elements = [UIElement(type="button", label="登录")]
    cdp.scan.return_value = elements
    r = _router(cdp=cdp)
    result = r.detect(app="Google Chrome")
    assert result.backend == "cdp"
    assert result.element_count == 1


def test_detect_chromium_cdp_fallback():
    cdp = MagicMock()
    cdp.find_chromium_ports.return_value = []
    r = _router(cdp=cdp)
    result = r.detect(app="Chrome")
    assert result.backend == "touchpoint"


def test_detect_no_backend():
    tp = MagicMock()
    tp.is_available.return_value = False
    r = _router(touchpoint=tp)
    result = r.detect(app="Safari")
    assert result.backend == "none"


async def test_detect_vision_merge(monkeypatch):
    vision = MagicMock()
    vision.is_available.return_value = True
    async def fake_detect(app):
        ctx = ScreenContext(app_name=app, backend="vision")
        ctx.visual_description = "描述"
        ctx.element_count = 2
        ctx.elements = [UIElement(type="text", label="新元素")]
        return ctx
    vision.detect = fake_detect
    tp = MagicMock()
    tp.is_available.return_value = True
    base = ScreenContext(app_name="app", backend="touchpoint")
    base.element_count = 0
    base.elements = []
    tp.detect.return_value = base
    r = _router(touchpoint=tp, vision=vision)
    result = r.detect(app="Safari")
    assert "vision" in result.backend
    assert result.element_count == 1


def test_get_active_app(monkeypatch):
    r = _router()
    class W:
        is_active = True
        app = "Finder"
    class Tp:
        @staticmethod
        def windows():
            return [W()]
    monkeypatch.setitem(__import__("sys").modules, "touchpoint", Tp())
    import sys
    sys.modules["touchpoint"] = Tp()
    assert r._get_active_app() == "Finder"


def test_get_detector_router_singleton():
    r1 = get_detector_router()
    r2 = get_detector_router()
    assert r1 is r2
