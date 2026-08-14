"""setup.py — 感知系统装配分支覆盖扩展

补足: 已启动时重建、event_bus 为空、PetEngine 失败、屏幕感知关闭、
      非 hotkey 语音模式、语音依赖不可用、窗口后台循环各分支、
      OCR 初始化异常、定时任务失败、stop 全组件收尾、get_status 全量、单例双重检查锁。
"""
import threading
from unittest.mock import MagicMock, patch

from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from modules.perception.setup import PerceptionSystem


def _patch_common(monkeypatch):
    """patch 事件总线 / 世界状态 / 桌宠，隔离真实子系统"""
    import modules.perception.events.bus as bus_mod
    import modules.perception.state.world_state as ws_mod
    import modules.desktop_pet.pet_engine as pe_mod
    bus = MagicMock()
    monkeypatch.setattr(bus_mod, "get_event_bus", lambda: bus)
    ws = MagicMock()
    monkeypatch.setattr(ws_mod, "WorldStateManager", lambda: ws)
    monkeypatch.setattr(pe_mod.PetEngine, "get_instance", classmethod(lambda cls, eb: MagicMock()))
    return bus, ws


def _new_system():
    ps = PerceptionSystem()
    ps._setup_window_detector = MagicMock()
    ps._setup_ocr_detector = MagicMock()
    return ps


# ====================================================================
# setup() 装配分支
# ====================================================================

def test_setup_restarts_when_started(monkeypatch):
    _patch_common(monkeypatch)
    ps = _new_system()
    ps._started = True
    with patch.object(ps, "stop") as mock_stop:
        ps.setup(voice_enabled=False, proactive_enabled=False)
    mock_stop.assert_called_once()


def test_setup_event_bus_none(monkeypatch):
    import modules.perception.events.bus as bus_mod
    import modules.perception.state.world_state as ws_mod
    import modules.desktop_pet.pet_engine as pe_mod
    monkeypatch.setattr(bus_mod, "get_event_bus", lambda: None)
    ws = MagicMock()
    monkeypatch.setattr(ws_mod, "WorldStateManager", lambda: ws)
    monkeypatch.setattr(pe_mod.PetEngine, "get_instance", classmethod(lambda cls, eb: MagicMock()))
    ps = _new_system()
    ps.setup(voice_enabled=False, proactive_enabled=False)
    assert ps.event_bus is None
    ws.start.assert_not_called()


def test_setup_pet_engine_failure(monkeypatch):
    import modules.desktop_pet.pet_engine as pe_mod
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        pe_mod.PetEngine, "get_instance",
        classmethod(lambda cls, eb: (_ for _ in ()).throw(RuntimeError("pet down"))),
    )
    ps = _new_system()
    ps.setup(voice_enabled=False, proactive_enabled=False)
    assert ps.pet_engine is None


def test_setup_screen_disabled(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "PERCEPTION_SCREEN_ENABLED", False)
    _patch_common(monkeypatch)
    ps = _new_system()
    ps.setup(voice_enabled=False, proactive_enabled=False)
    ps._setup_window_detector.assert_not_called()
    ps._setup_ocr_detector.assert_not_called()


def test_setup_voice_wakeword_mode(monkeypatch):
    import modules.perception.detectors.voice_detector as vd_mod
    _patch_common(monkeypatch)
    vd = MagicMock()
    vd.is_available.return_value = True
    monkeypatch.setattr(vd_mod, "VoiceDetector", lambda **k: vd)
    ps = _new_system()
    ps.setup(voice_enabled=True, proactive_enabled=False, voice_mode="wakeword")
    assert ps.voice_detector is vd
    vd.start.assert_called_once()


def test_setup_voice_unavailable(monkeypatch):
    import modules.perception.detectors.voice_detector as vd_mod
    _patch_common(monkeypatch)
    vd = MagicMock()
    vd.is_available.return_value = False
    monkeypatch.setattr(vd_mod, "VoiceDetector", lambda **k: vd)
    ps = _new_system()
    ps.setup(voice_enabled=True, proactive_enabled=False, voice_mode="wakeword")
    assert ps.voice_detector is None
    vd.start.assert_not_called()


# ====================================================================
# _window_detector_loop 分支
# ====================================================================

def test_window_detector_loop_publishes(monkeypatch):
    ps = PerceptionSystem()
    ps.event_bus = MagicMock()
    det = MagicMock()
    det.detect.return_value = [PerceptionEvent(event_type=PerceptionEventType.SCREEN_WINDOW)]
    ps.window_detector = det
    ps._window_stop_event = threading.Event()

    def _sleep(secs):
        ps._window_stop_event.set()

    with patch("time.sleep", _sleep):
        ps._window_detector_loop()
    det.detect.assert_called_once()
    ps.event_bus.publish.assert_called_once()


def test_window_detector_loop_detect_exception(monkeypatch):
    ps = PerceptionSystem()
    ps.event_bus = MagicMock()
    det = MagicMock()
    det.detect.side_effect = RuntimeError("boom")
    ps.window_detector = det
    ps._window_stop_event = threading.Event()

    def _sleep(secs):
        ps._window_stop_event.set()

    with patch("time.sleep", _sleep):
        ps._window_detector_loop()
    det.detect.assert_called_once()
    ps.event_bus.publish.assert_not_called()


def test_window_detector_loop_no_detector(monkeypatch):
    ps = PerceptionSystem()
    ps.event_bus = MagicMock()
    ps.window_detector = None
    ps._window_stop_event = threading.Event()

    def _sleep(secs):
        ps._window_stop_event.set()

    with patch("time.sleep", _sleep):
        ps._window_detector_loop()


def test_window_detector_loop_no_bus(monkeypatch):
    ps = PerceptionSystem()
    ps.event_bus = None
    det = MagicMock()
    det.detect.return_value = [PerceptionEvent(event_type=PerceptionEventType.SCREEN_WINDOW)]
    ps.window_detector = det
    ps._window_stop_event = threading.Event()

    def _sleep(secs):
        ps._window_stop_event.set()

    with patch("time.sleep", _sleep):
        ps._window_detector_loop()
    det.detect.assert_called_once()


def test_window_detector_loop_exit_via_condition(monkeypatch):
    """for 循环自然走完 10 次后由 while 条件退出（172->exit / 181->172）"""
    ps = PerceptionSystem()
    ps.event_bus = MagicMock()
    det = MagicMock()
    det.detect.return_value = []
    ps.window_detector = det
    ps._window_stop_event = threading.Event()
    calls = {"n": 0}

    def _sleep(secs):
        calls["n"] += 1
        if calls["n"] >= 10:
            ps._window_stop_event.set()

    with patch("time.sleep", _sleep):
        ps._window_detector_loop()
    assert calls["n"] == 10


# ====================================================================
# OCR / start / stop / status
# ====================================================================

def test_setup_ocr_detector_exception(monkeypatch):
    import modules.perception.detectors.ocr_detector as ocr_mod
    monkeypatch.setattr(
        ocr_mod, "OCRDetector",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("ocr down")),
    )
    ps = PerceptionSystem()
    ps._setup_ocr_detector()
    assert ps.ocr_detector is None


def test_start_task_manager_failure():
    ps = PerceptionSystem()
    ps._started = False
    with patch("modules.thinking.scheduled_tasks.get_task_manager",
               side_effect=RuntimeError("tm down")):
        ps.start()
    assert ps._started is True


def test_stop_with_all_components():
    ps = PerceptionSystem.__new__(PerceptionSystem)
    ps._started = True
    ocr = MagicMock()
    ps.ocr_detector = ocr
    trg = MagicMock()
    ps.proactive_trigger = trg
    t = MagicMock()
    ps._window_detector_thread = t
    ps._window_stop_event = threading.Event()
    ps.world_state = MagicMock()
    bus = MagicMock()
    import modules.perception.events.bus as bus_mod
    with patch.object(bus_mod, "get_event_bus", return_value=bus):
        ps.stop()
    ocr.stop.assert_called_once()
    trg.stop.assert_called_once()
    t.join.assert_called_once_with(timeout=3)
    assert ps._window_detector_thread is None
    ps.world_state.stop.assert_called_once_with(bus)
    assert ps._started is False


def test_get_status_populated():
    ps = PerceptionSystem.__new__(PerceptionSystem)
    ps._started = True
    vd = MagicMock()
    vd.detector_type = "voice"
    ps.voice_detector = vd
    wd = MagicMock()
    wd.is_available.return_value = True
    ps.window_detector = wd
    od = MagicMock()
    od.is_available.return_value = True
    ps.ocr_detector = od
    trg = MagicMock()
    trg.get_stats.return_value = {"sent": 1}
    ps.proactive_trigger = trg
    ws = MagicMock()
    ws.get_state.return_value.to_dict.return_value = {"active_app": "x"}
    ps.world_state = ws
    bus = MagicMock()
    bus.get_stats.return_value = {"total_events": 3}
    ps.event_bus = bus

    status = ps.get_status()
    assert status["started"] is True
    assert status["voice_available"] is True
    assert status["voice_detector_type"] == "voice"
    assert status["window_detector_available"] is True
    assert status["ocr_detector_available"] is True
    assert status["proactive_trigger"] == {"sent": 1}
    assert status["world_state"] == {"active_app": "x"}
    assert status["event_bus"] == {"total_events": 3}


# ====================================================================
# 单例双重检查锁
# ====================================================================

def test_get_system_locked_reentry(monkeypatch):
    import modules.perception.setup as mod
    original = mod._system
    mod._system = None
    real_lock = mod._system_lock

    class _FakeLock:
        def __enter__(self):
            mod._system = object()  # 进入锁内时已有实例 → 走 253->255 分支
            return real_lock.__enter__()

        def __exit__(self, *a):
            return real_lock.__exit__(*a)

    try:
        with patch.object(mod, "_system_lock", _FakeLock()):
            s = mod.get_perception_system()
        assert s is not None
    finally:
        mod._system = original
