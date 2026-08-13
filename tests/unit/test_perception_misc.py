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


def test_ocr_detector_type_and_available(monkeypatch):
    import sys
    import types
    d = OCRDetector()
    assert d.detector_type == "ocr"
    # 不真实加载 onnxruntime：RapidOCR() 初始化会起 17 个线程池线程，
    # 与 GIL 交互在测试进程中偶发死锁（整个 suite 随机挂起）。用假模块测两个分支。
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)
    assert d.is_available() is False
    fake = types.ModuleType("rapidocr_onnxruntime")
    fake.RapidOCR = object
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", fake)
    assert d.is_available() is True


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


def test_setup_full_flow(monkeypatch):
    from modules.perception.setup import PerceptionSystem
    import modules.perception.setup as mod
    import modules.perception.events.bus as bus_mod
    import modules.perception.state.world_state as ws_mod
    import modules.desktop_pet.pet_engine as pe_mod

    bus = MagicMock()
    monkeypatch.setattr(bus_mod, "get_event_bus", lambda: bus)
    ws = MagicMock()
    monkeypatch.setattr(ws_mod, "WorldStateManager", lambda: ws)
    pet = MagicMock()
    monkeypatch.setattr(pe_mod.PetEngine, "get_instance", classmethod(lambda cls, eb: pet))

    ps = PerceptionSystem()
    ps._setup_window_detector = MagicMock()
    ps._setup_ocr_detector = MagicMock()
    ps.setup(voice_enabled=False, proactive_enabled=False)
    assert ps.event_bus is bus
    assert ps.world_state is ws
    assert ps.voice_detector is None
    assert ps.proactive_trigger is None
    assert ps.pet_engine is pet
    ps._setup_window_detector.assert_called_once()
    ps._setup_ocr_detector.assert_called_once()


def test_setup_with_proactive_and_voice(monkeypatch):
    from modules.perception.setup import PerceptionSystem
    import modules.perception.setup as mod
    import modules.perception.events.bus as bus_mod
    import modules.perception.state.world_state as ws_mod
    import modules.desktop_pet.pet_engine as pe_mod
    import modules.perception.trigger as trg_mod

    bus = MagicMock()
    monkeypatch.setattr(bus_mod, "get_event_bus", lambda: bus)
    ws = MagicMock()
    monkeypatch.setattr(ws_mod, "WorldStateManager", lambda: ws)
    monkeypatch.setattr(pe_mod.PetEngine, "get_instance", classmethod(lambda cls, eb: MagicMock()))

    trg = MagicMock()
    monkeypatch.setattr(trg_mod, "ProactiveTrigger", lambda: trg)
    import modules.perception.detectors.hotkey_voice_detector as hvd_mod
    import modules.perception.detectors.voice_detector as vd_mod
    vd = MagicMock()
    vd.is_available.return_value = True
    monkeypatch.setattr(hvd_mod, "HotkeyVoiceDetector", lambda **k: vd)
    monkeypatch.setattr(vd_mod, "VoiceDetector", lambda **k: vd)

    ps = PerceptionSystem()
    ps._setup_window_detector = MagicMock()
    ps._setup_ocr_detector = MagicMock()
    ps.setup(voice_enabled=True, proactive_enabled=True, voice_mode="hotkey")
    assert ps.proactive_trigger is trg
    assert ps.voice_detector is not None
    trg.start.assert_called_once_with(bus)


def test_setup_window_detector_real_impl_available(monkeypatch):
    """_setup_window_detector 真实控制流：检测器可用 → 启动线程"""
    from modules.perception.setup import PerceptionSystem
    import modules.perception.detectors.window_detector as wd_mod
    import threading

    fake = MagicMock()
    fake.is_available.return_value = True
    monkeypatch.setattr(wd_mod, "WindowDetector", lambda: fake)

    ps = PerceptionSystem()  # 真实 __init__
    fake_thread = MagicMock()
    monkeypatch.setattr(threading, "Thread", lambda **k: fake_thread)

    ps._setup_window_detector()
    assert ps.window_detector is fake
    fake_thread.start.assert_called_once()
    assert ps._window_detector_thread is fake_thread


def test_setup_window_detector_real_impl_unavailable(monkeypatch):
    from modules.perception.setup import PerceptionSystem
    import modules.perception.detectors.window_detector as wd_mod
    fake = MagicMock()
    fake.is_available.return_value = False
    monkeypatch.setattr(wd_mod, "WindowDetector", lambda: fake)
    ps = PerceptionSystem()
    ps._setup_window_detector()
    assert ps.window_detector is None
    assert ps._window_detector_thread is None


def test_setup_window_detector_real_impl_exception(monkeypatch):
    from modules.perception.setup import PerceptionSystem
    import modules.perception.detectors.window_detector as wd_mod
    monkeypatch.setattr(wd_mod, "WindowDetector", lambda: (_ for _ in ()).throw(RuntimeError("no window")))
    ps = PerceptionSystem()
    ps._setup_window_detector()
    assert ps.window_detector is None


def test_setup_ocr_detector_real_impl_available(monkeypatch):
    """_setup_ocr_detector 真实控制流：OCR 可用 → start"""
    from modules.perception.setup import PerceptionSystem
    import modules.perception.detectors.ocr_detector as ocr_mod

    fake = MagicMock()
    fake.is_available.return_value = True
    monkeypatch.setattr(ocr_mod, "OCRDetector", lambda **kw: fake)

    ps = PerceptionSystem()  # 真实 __init__
    ps.event_bus = MagicMock()
    ps._setup_ocr_detector()
    assert ps.ocr_detector is fake
    fake.start.assert_called_once_with(event_bus=ps.event_bus)


def test_setup_ocr_detector_real_impl_unavailable(monkeypatch):
    from modules.perception.setup import PerceptionSystem
    import modules.perception.detectors.ocr_detector as ocr_mod
    fake = MagicMock()
    fake.is_available.return_value = False
    monkeypatch.setattr(ocr_mod, "OCRDetector", lambda **kw: fake)
    ps = PerceptionSystem()
    ps._setup_ocr_detector()
    assert ps.ocr_detector is None
