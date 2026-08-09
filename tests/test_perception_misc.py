"""perception 感知批次测试：context / router / detectors / setup"""
from unittest.mock import MagicMock, patch

from modules.perception.screen.context import ScreenContext, UIElement
from modules.perception.screen.router import DetectorRouter, get_detector_router
from modules.perception.detectors.touchpoint_detector import TouchpointDetector
from modules.perception.detectors.ocr_detector import OCRDetector
from modules.perception.setup import PerceptionSystem, get_perception_system


def test_ui_element_and_context_to_dict():
    el = UIElement(type="button", label="提交", center_x=10, center_y=20, actions=["click"])
    ctx = ScreenContext(app_name="test", window_title="t", backend="vision", depth=3)
    ctx.elements = [el]
    ctx.element_count = 1
    d = ctx.to_dict()
    assert d["app_name"] == "test"
    assert d["elements"][0]["label"] == "提交"
    assert d["element_count"] == 1
    assert d["backend"] == "vision"


def test_context_get_summary():
    ctx = ScreenContext(app_name="test")
    summary = ctx.get_summary()
    assert "test" in summary


def test_router_is_chromium_app():
    r = DetectorRouter.__new__(DetectorRouter)
    assert r._is_chromium_app("Google Chrome") is True
    assert r._is_chromium_app("Firefox") is False
    assert r._is_chromium_app("") is False


def test_get_detector_router_singleton():
    r1 = get_detector_router()
    r2 = get_detector_router()
    assert r1 is r2


def test_touchpoint_is_electron_app(tmp_path):
    frameworks = tmp_path / "Contents" / "Frameworks"
    frameworks.mkdir(parents=True)
    (frameworks / "Test Helper (Renderer).app").mkdir()
    assert TouchpointDetector._is_electron_app(str(tmp_path)) is True
    (frameworks / "Test Helper (Renderer).app").rmdir()
    assert TouchpointDetector._is_electron_app(str(tmp_path)) is False


def test_touchpoint_is_electron_missing():
    assert TouchpointDetector._is_electron_app("/nonexistent") is False


def test_ocr_detector_type_and_available():
    d = OCRDetector()
    assert d.detector_type == "ocr"
    assert isinstance(d.is_available(), bool)


def test_ocr_start_requires_available():
    d = OCRDetector()
    with patch.object(d, "is_available", return_value=False):
        d.start(event_bus=MagicMock())
        assert d._running is False


def test_perception_system_status_empty():
    ps = PerceptionSystem.__new__(PerceptionSystem)
    ps._started = False
    ps.voice_detector = None
    ps.window_detector = None
    ps.ocr_detector = None
    ps.proactive_trigger = None
    ps.world_state = None
    ps.event_bus = None
    status = ps.get_status()
    assert status["started"] is False
    assert status["voice_available"] is False


def test_perception_system_start_stop_idempotent():
    ps = PerceptionSystem.__new__(PerceptionSystem)
    ps._started = False
    ps.ocr_detector = None
    ps.proactive_trigger = None
    ps._window_detector_thread = None
    ps.world_state = None
    with patch("modules.thinking.scheduled_tasks.get_task_manager"):
        ps.start()
        ps.start()  # 二次调用幂等
        assert ps._started is True
        ps.stop()
        ps.stop()  # 二次调用幂等
        assert ps._started is False


def test_get_perception_system_singleton():
    s1 = get_perception_system()
    s2 = get_perception_system()
    assert s1 is s2
