"""perception/screen/router 补充测试：CDP 异常路径、视觉合并分支、_init_backends"""
import sys
from unittest.mock import MagicMock

import infra.data_process.core.cdp_scanner as cdp_mod
from modules.perception.screen.context import ScreenContext, UIElement
from modules.perception.screen.router import DetectorRouter


def _router(**kw):
    r = DetectorRouter.__new__(DetectorRouter)
    tp = kw.get("touchpoint")
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


def test_init_backends_cdp_unavailable(monkeypatch):
    def _boom():
        raise ImportError("no cdp")

    monkeypatch.setattr(cdp_mod, "get_cdp_scanner", _boom)
    r = DetectorRouter()
    assert r._touchpoint is not None
    assert r._vision is not None
    assert r._cdp is None


def test_init_backends_cdp_available(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(cdp_mod, "get_cdp_scanner", lambda: fake)
    r = DetectorRouter()
    assert r._cdp is fake


def test_get_active_app_no_active(monkeypatch):
    class W:
        is_active = False
        app = "Finder"

    class Tp:
        @staticmethod
        def windows():
            return [W()]

    monkeypatch.setitem(sys.modules, "touchpoint", Tp())
    r = _router()
    assert r._get_active_app() == ""


def test_get_active_app_exception(monkeypatch):
    class Tp:
        @staticmethod
        def windows():
            raise RuntimeError("touchpoint broken")

    monkeypatch.setitem(sys.modules, "touchpoint", Tp())
    r = _router()
    assert r._get_active_app() == ""


def test_is_chromium_app_empty():
    r = _router()
    assert r._is_chromium_app("") is False


def test_detect_no_app_uses_active_app():
    """未指定 app → 用活跃窗口名路由"""
    tp = MagicMock()
    tp.is_available.return_value = True
    base = ScreenContext(app_name="Finder", backend="touchpoint")
    base.element_count = 10
    tp.detect.return_value = base
    r = _router(touchpoint=tp)
    r._get_active_app = lambda: "Finder"
    r._is_chromium_app = lambda a: False
    result = r.detect()
    assert result.backend == "touchpoint"
    tp.detect.assert_called_with("Finder", depth=3)


def test_get_active_app_with_active_window(monkeypatch):
    class W:
        is_active = True
        app = "Finder"

    class Tp:
        @staticmethod
        def windows():
            return [W()]

    monkeypatch.setitem(sys.modules, "touchpoint", Tp())
    r = _router()
    assert r._get_active_app() == "Finder"


def test_detect_native_no_touchpoint():
    tp = MagicMock()
    tp.is_available.return_value = False
    r = _router(touchpoint=tp)
    result = r._detect_native("Safari", 3)
    assert result.backend == "none"


def test_detect_chromium_cdp_success():
    cdp = MagicMock()
    cdp.find_chromium_ports.return_value = [{"port": 9222}]
    elements = [UIElement(type="button", label="登录")]
    cdp.scan.return_value = elements
    r = _router(cdp=cdp)
    result = r.detect(app="Google Chrome")
    assert result.backend == "cdp"
    assert result.element_count == 1


def test_get_detector_router_singleton():
    from modules.perception.screen.router import get_detector_router

    r1 = get_detector_router()
    r2 = get_detector_router()
    assert r1 is r2


def test_detect_chromium_scan_empty_fallback():
    """CDP 扫描无元素 → 回退 touchpoint"""
    cdp = MagicMock()
    cdp.find_chromium_ports.return_value = [{"port": 9222}]
    cdp.scan.return_value = []
    r = _router(cdp=cdp)
    result = r.detect(app="Chrome")
    assert result.backend == "touchpoint"


def test_detect_chromium_scan_exception():
    """CDP scan 抛异常 → 回退 touchpoint"""
    cdp = MagicMock()
    cdp.find_chromium_ports.return_value = [{"port": 9222}]
    cdp.scan.side_effect = RuntimeError("cdp dead")
    r = _router(cdp=cdp)
    result = r.detect(app="Edge")
    assert result.backend == "touchpoint"


def test_detect_chromium_ports_exception():
    """find_chromium_ports 抛异常 → 回退 touchpoint"""
    cdp = MagicMock()
    cdp.find_chromium_ports.side_effect = RuntimeError("boom")
    r = _router(cdp=cdp)
    result = r.detect(app="Chrome")
    assert result.backend == "touchpoint"


def test_detect_chromium_no_backend():
    """CDP 不可用且 touchpoint 不可用 → backend none"""
    tp = MagicMock()
    tp.is_available.return_value = False
    cdp = MagicMock()
    cdp.find_chromium_ports.return_value = []
    r = _router(touchpoint=tp, cdp=cdp)
    result = r.detect(app="Vivaldi")
    assert result.backend == "none"


def test_detect_chromium_no_cdp_fallback():
    """无 CDP 后端时 chromium 应用直接走 touchpoint"""
    r = _router()  # cdp=None
    result = r.detect(app="Chrome")
    assert result.backend == "touchpoint"


def test_merge_vision_not_available(monkeypatch):
    """外层检测可用、合并时视觉不可用 → 原样返回"""
    vision = MagicMock()
    vision.is_available.side_effect = [True, False]
    tp = MagicMock()
    tp.is_available.return_value = True
    base = ScreenContext(app_name="a", backend="touchpoint")
    base.element_count = 3
    tp.detect.return_value = base
    r = _router(touchpoint=tp, vision=vision)
    result = r.detect(app="Safari")
    assert result.backend == "touchpoint"
    assert result.element_count == 3


async def test_merge_vision_running_loop_merges():
    """异步上下文中用线程池跑视觉，去重合并新元素"""
    vision = MagicMock()
    vision.is_available.return_value = True

    async def fake_detect(app):
        ctx = ScreenContext(app_name=app, backend="vision")
        ctx.visual_description = "描述"
        ctx.element_count = 2
        ctx.elements = [
            UIElement(type="text", label="已存在"),
            UIElement(type="button", label="新按钮"),
        ]
        return ctx

    vision.detect = fake_detect
    r = _router(vision=vision)
    base = ScreenContext(app_name="app", backend="touchpoint")
    base.element_count = 1
    base.elements = [UIElement(type="text", label="已存在")]
    result = r._merge_with_vision(base)
    assert "vision" in result.backend
    assert result.element_count == 2
    assert {e.label for e in result.elements} == {"已存在", "新按钮"}


async def test_merge_vision_fewer_elements():
    """视觉元素不多于基础 → 跳过补充"""
    vision = MagicMock()
    vision.is_available.return_value = True

    async def fake_detect(app):
        ctx = ScreenContext(app_name=app, backend="vision")
        ctx.visual_description = "描述"
        ctx.element_count = 0
        ctx.elements = []
        return ctx

    vision.detect = fake_detect
    r = _router(vision=vision)
    base = ScreenContext(app_name="app", backend="touchpoint")
    base.element_count = 8
    base.elements = [UIElement(type="text", label="a") for _ in range(8)]
    result = r._merge_with_vision(base)
    assert result.element_count == 8
    assert result.visual_description == "描述"


async def test_merge_vision_exception():
    """视觉检测抛异常 → 降级返回原结果"""
    vision = MagicMock()
    vision.is_available.return_value = True

    async def fake_detect(app):
        raise RuntimeError("vision down")

    vision.detect = fake_detect
    r = _router(vision=vision)
    base = ScreenContext(app_name="app", backend="touchpoint")
    base.element_count = 0
    base.elements = []
    result = r._merge_with_vision(base)
    assert result is base
    assert result.backend == "touchpoint"


def test_merge_vision_sync_no_running_loop():
    """非异步上下文 → asyncio.run 直接跑视觉协程"""
    vision = MagicMock()
    vision.is_available.return_value = True

    async def fake_detect(app):
        ctx = ScreenContext(app_name=app, backend="vision")
        ctx.visual_description = "描述"
        ctx.element_count = 2
        ctx.elements = [UIElement(type="text", label="新元素")]
        return ctx

    vision.detect = fake_detect
    r = _router(vision=vision)
    base = ScreenContext(app_name="app", backend="touchpoint")
    base.element_count = 1
    base.elements = []
    import asyncio
    new_loop = asyncio.new_event_loop()
    old = asyncio.get_event_loop_policy().get_event_loop()
    asyncio.set_event_loop(new_loop)
    try:
        result = r._merge_with_vision(base)
    finally:
        asyncio.set_event_loop(old)
    assert "vision" in result.backend
    assert result.element_count == 1
