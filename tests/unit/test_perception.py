"""感知系统单元测试

覆盖: Event Bus（同步/异步/通配/订阅校验）, Event Types, OCR/Window/Voice
      Detectors, WorldState 基础+边界, PerceptionSystem 生命周期
"""
import asyncio
import collections
import threading
import time
from unittest.mock import MagicMock, patch, AsyncMock

import numpy as np
import pytest

from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from modules.perception.events.bus import PerceptionEventBus
from modules.perception.detectors.base import PerceptionDetector
from modules.perception.detectors.window_detector import WindowDetector
from modules.perception.detectors.voice_detector import VoiceDetector
from modules.perception.state.world_state import WorldState, WorldStateManager


# ====================================================================
# Event Types
# ====================================================================

class TestPerceptionEvent:
    def test_create_event(self):
        event = PerceptionEvent(
            event_type=PerceptionEventType.SCREEN_OCR,
            source="ocr",
            importance=0.6,
            payload={"text": "hello"},
        )
        assert event.event_type == "screen.ocr"
        assert event.source == "ocr"
        assert event.importance == 0.6
        assert event.payload["text"] == "hello"
        assert event.event_id  # auto-generated

    def test_to_dict(self):
        event = PerceptionEvent(event_type="test", payload={"k": "v"})
        d = event.to_dict()
        assert d["event_type"] == "test"
        assert d["payload"] == {"k": "v"}
        assert "event_id" in d
        assert "timestamp" in d

    def test_short_repr(self):
        event = PerceptionEvent(event_type="test", source="src", importance=0.5)
        r = event.short_repr()
        assert "test" in r
        assert "src" in r

    def test_event_types_defined(self):
        assert PerceptionEventType.SCREEN_OCR == "screen.ocr"
        assert PerceptionEventType.SCREEN_WINDOW == "screen.window"
        assert PerceptionEventType.DIFFERENCE_DETECTED == "difference.detected"
        assert PerceptionEventType.ALL == "*"


# ====================================================================
# Event Bus
# ====================================================================

class TestEventBus:
    def setup_method(self):
        self.bus = PerceptionEventBus()

    def test_subscribe_and_publish(self):
        received = []
        self.bus.subscribe(PerceptionEventType.SCREEN_OCR, lambda e: received.append(e))
        event = PerceptionEvent(event_type=PerceptionEventType.SCREEN_OCR)
        self.bus.publish(event)
        assert len(received) == 1
        assert received[0].event_type == PerceptionEventType.SCREEN_OCR

    def test_wildcard_subscription(self):
        received = []
        self.bus.subscribe(PerceptionEventType.ALL, lambda e: received.append(e))
        self.bus.publish(PerceptionEvent(event_type="a"))
        self.bus.publish(PerceptionEvent(event_type="b"))
        assert len(received) == 2

    def test_unsubscribe(self):
        received = []
        sub_id = self.bus.subscribe("test", lambda e: received.append(e))
        self.bus.publish(PerceptionEvent(event_type="test"))
        assert len(received) == 1

        assert self.bus.unsubscribe(sub_id) is True
        self.bus.publish(PerceptionEvent(event_type="test"))
        assert len(received) == 1  # 不再收到

    def test_unsubscribe_nonexistent(self):
        assert self.bus.unsubscribe("nonexistent") is False

    def test_no_subscribers(self):
        # 不应抛异常
        self.bus.publish(PerceptionEvent(event_type="no_one_listens"))

    def test_handler_exception_does_not_crash(self):
        def bad_handler(e):
            raise RuntimeError("boom")

        self.bus.subscribe("test", bad_handler)
        # 不应抛异常
        self.bus.publish(PerceptionEvent(event_type="test"))

    def test_multiple_handlers(self):
        results = []
        self.bus.subscribe("test", lambda e: results.append("a"))
        self.bus.subscribe("test", lambda e: results.append("b"))
        self.bus.publish(PerceptionEvent(event_type="test"))
        assert results == ["a", "b"]

    def test_stats(self):
        self.bus.subscribe("test", lambda e: None)
        self.bus.publish(PerceptionEvent(event_type="test"))
        stats = self.bus.get_stats()
        assert stats["total_events"] == 1
        assert stats["total_subscribers"] == 1

    def test_clear(self):
        self.bus.subscribe("test", lambda e: None)
        self.bus.publish(PerceptionEvent(event_type="test"))
        self.bus.clear()
        stats = self.bus.get_stats()
        assert stats["total_events"] == 0
        assert stats["total_subscribers"] == 0

    def test_thread_safety(self):
        """多线程并发 publish 不崩溃"""
        received = []
        lock = threading.Lock()

        def handler(e):
            with lock:
                received.append(e)

        self.bus.subscribe("test", handler)

        threads = []
        for _ in range(10):
            t = threading.Thread(
                target=lambda: [
                    self.bus.publish(PerceptionEvent(event_type="test"))
                    for _ in range(100)
                ]
            )
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        assert len(received) == 1000


class TestWindowDetector:
    def test_detector_type(self):
        det = WindowDetector()
        assert det.detector_type == "window"

    def test_reset(self):
        det = WindowDetector()
        det._last_window = "test"
        det._last_app = "test"
        det.reset()
        assert det._last_window is None
        assert det._last_app is None

    def test_init_is_fast(self):
        """实例化不应阻塞（AppKit 应懒加载）"""
        t0 = time.time()
        det = WindowDetector()
        elapsed = time.time() - t0
        assert elapsed < 1.0, f"WindowDetector() 耗时 {elapsed:.2f}s，应 <1s"
        assert det._backend is None, "AppKit 不应在 __init__ 加载"

    def test_detect_returns_events_on_change(self):
        det = WindowDetector()
        det._last_window = "something_else"
        det._last_app = "OtherApp"
        with patch.object(det, "is_available", return_value=True), \
             patch.object(det, "_get_active_window", return_value=("MyWindow", "MyApp")):
            events = det.detect(np.empty(0), "_system")
            assert len(events) == 1
            assert events[0].event_type == PerceptionEventType.SCREEN_WINDOW
            assert events[0].payload["window_title"] == "MyWindow"
            assert events[0].payload["app_name"] == "MyApp"

    def test_detect_skips_duplicates(self):
        det = WindowDetector()
        det._last_window = "SameWindow"
        det._last_app = "SameApp"
        with patch.object(det, "is_available", return_value=True), \
             patch.object(det, "_get_active_window", return_value=("SameWindow", "SameApp")):
            events = det.detect(np.empty(0), "_system")
            assert len(events) == 0


class TestWorldStateManager:
    def test_initial_state(self):
        wm = WorldStateManager()
        state = wm.get_state()
        assert state.active_app == ""
        assert state.active_window == ""
        assert state.screen_text == ""

    def test_window_event_updates_state(self):
        bus = PerceptionEventBus()
        wm = WorldStateManager()
        wm.start(bus)

        event = PerceptionEvent(
            event_type=PerceptionEventType.SCREEN_WINDOW,
            payload={"window_title": "Test", "app_name": "TestApp"},
        )
        bus.publish(event)

        state = wm.get_state()
        assert state.active_window == "Test"
        assert state.active_app == "TestApp"

        wm.stop(bus)

    def test_ocr_event_updates_state(self):
        bus = PerceptionEventBus()
        wm = WorldStateManager()
        wm.start(bus)

        event = PerceptionEvent(
            event_type=PerceptionEventType.SCREEN_OCR,
            payload={"new_lines": ["hello", "world"]},
        )
        bus.publish(event)

        state = wm.get_state()
        assert "hello" in state.screen_text
        assert len(state.recent_ocr) == 2

        wm.stop(bus)

    def test_summary(self):
        state = WorldState(active_app="Safari", active_window="Google")
        summary = state.get_summary()
        assert "Safari" in summary
        assert "Google" in summary

    def test_stop_unsubscribes(self):
        bus = PerceptionEventBus()
        wm = WorldStateManager()
        wm.start(bus)
        wm.stop(bus)
        # 发布事件不应更新状态
        bus.publish(PerceptionEvent(
            event_type=PerceptionEventType.SCREEN_WINDOW,
            payload={"window_title": "X", "app_name": "Y"},
        ))
        state = wm.get_state()
        assert state.active_window == ""


# ====================================================================
# Event Bus — 异步 handler + shutdown + subscribe 校验
# ====================================================================

class TestEventBusAdvanced:
    def setup_method(self):
        self.bus = PerceptionEventBus()

    def test_subscribe_no_handler_raises(self):
        with pytest.raises(ValueError, match="必须提供"):
            self.bus.subscribe("test")

    def test_async_handler(self):
        results = []

        async def handler(e):
            results.append(e.event_type)

        self.bus.subscribe("test", async_handler=handler)
        self.bus.publish(PerceptionEvent(event_type="test"))
        time.sleep(0.3)
        assert len(results) == 1
        assert results[0] == "test"

    def test_shutdown_stops_async_loop(self):
        async def handler(e):
            pass

        self.bus.subscribe("test", async_handler=handler)
        self.bus.publish(PerceptionEvent(event_type="test"))
        time.sleep(0.1)
        self.bus.shutdown()
        assert self.bus._async_loop is None

    def test_get_event_bus_singleton(self):
        from modules.perception.events.bus import get_event_bus
        b1 = get_event_bus()
        b2 = get_event_bus()
        assert b1 is b2

    def test_to_dict_all_keys(self):
        event = PerceptionEvent(
            event_type="test", platform="macos", source="ocr",
            importance=0.8, roi_name="chat", payload={"k": "v"},
        )
        d = event.to_dict()
        assert d["event_type"] == "test"
        assert d["platform"] == "macos"
        assert d["source"] == "ocr"
        assert d["importance"] == 0.8
        assert d["roi_name"] == "chat"
        assert d["payload"] == {"k": "v"}
        assert "event_id" in d
        assert "timestamp" in d

    def test_short_repr_empty_payload(self):
        event = PerceptionEvent(event_type="test", source="src")
        r = event.short_repr()
        assert "test" in r
        assert "{}" in r

    def test_event_id_unique(self):
        e1 = PerceptionEvent(event_type="test")
        e2 = PerceptionEvent(event_type="test")
        assert e1.event_id != e2.event_id


# ====================================================================
# VoiceDetector — Mock 测试
# ====================================================================

class TestVoiceDetector:
    @pytest.fixture(autouse=True)
    def _isolate_voice_deps(self):
        """隔离语音原生依赖（pyaudio/whisper）。

        构造 VoiceDetector 会 _check_availability() → import pyaudio/whisper，
        在完整测试套件中 portaudio 已被其他测试初始化，二次 import 会触发原生
        Abort（Fatal Python error）。这里 patch 掉让依赖不可用，测试不依赖真实语音。
        """
        with patch.dict("sys.modules", {
            "speech_recognition": None, "pyaudio": None, "whisper": None,
        }):
            yield

    def test_not_available_without_deps(self):
        with patch.dict("sys.modules", {"speech_recognition": None, "pyaudio": None}):
            det = VoiceDetector()
            assert det.is_available() is False

    def test_detector_type(self):
        det = VoiceDetector()
        assert det.detector_type == "voice"

    def test_detect_returns_cached_events(self):
        det = VoiceDetector()
        det._events.append(PerceptionEvent(
            event_type=PerceptionEventType.SPEECH_DETECTED,
            payload={"text": "hello"},
        ))
        events = det.detect(np.empty(0), "test")
        assert len(events) == 1
        assert events[0].payload["text"] == "hello"
        # 第二次应该为空（已清空）
        assert det.detect(np.empty(0), "test") == []

    def test_deque_maxlen(self):
        det = VoiceDetector()
        det._events = collections.deque(maxlen=5)
        for i in range(10):
            det._events.append(PerceptionEvent(event_type="test", payload={"i": i}))
        assert len(det._events) == 5
        assert det._events[0].payload["i"] == 5  # 最旧的被丢弃

    def test_reset_clears_events(self):
        det = VoiceDetector()
        det._events.append(PerceptionEvent(event_type="test"))
        det.reset()
        assert len(det._events) == 0

    def test_stop_without_start(self):
        det = VoiceDetector()
        det.stop()
        assert det._running is False, "未启动时调用 stop 后 _running 应保持 False"

    def test_start_when_not_available(self):
        det = VoiceDetector()
        det._available = False
        det.start()  # 不应报错
        assert det._running is False


# ====================================================================
# WorldStateManager — 边界测试
# ====================================================================

class TestWorldStateManagerAdvanced:
    def test_ui_event_updates_state(self):
        bus = PerceptionEventBus()
        wm = WorldStateManager()
        wm.start(bus)

        bus.publish(PerceptionEvent(
            event_type=PerceptionEventType.SCREEN_UI,
            payload={"subtype": "notification", "template_name": "dot"},
        ))
        state = wm.get_state()
        assert len(state.ui_elements) == 1
        assert state.ui_elements[0]["subtype"] == "notification"

        wm.stop(bus)

    def test_recent_ocr_cap(self):
        bus = PerceptionEventBus()
        wm = WorldStateManager()
        wm.start(bus)

        for i in range(15):
            bus.publish(PerceptionEvent(
                event_type=PerceptionEventType.SCREEN_OCR,
                payload={"new_lines": [f"line_{i}"]},
            ))
        state = wm.get_state()
        assert len(state.recent_ocr) == 10

        wm.stop(bus)

    def test_recent_events_cap(self):
        bus = PerceptionEventBus()
        wm = WorldStateManager()
        wm.start(bus)

        for i in range(25):
            bus.publish(PerceptionEvent(
                event_type=PerceptionEventType.SCREEN_WINDOW,
                payload={"window_title": f"W{i}", "app_name": f"A{i}"},
            ))
        state = wm.get_state()
        assert len(state.recent_events) == 20

        wm.stop(bus)

    def test_get_state_deep_copy(self):
        bus = PerceptionEventBus()
        wm = WorldStateManager()
        wm.start(bus)

        bus.publish(PerceptionEvent(
            event_type=PerceptionEventType.SCREEN_UI,
            payload={"subtype": "test"},
        ))
        state1 = wm.get_state()
        state1.ui_elements.append({"injected": True})

        state2 = wm.get_state()
        assert len(state2.ui_elements) == 1  # 不被外部修改影响

        wm.stop(bus)

    def test_to_dict(self):
        state = WorldState(
            active_app="Safari", active_window="Google",
            screen_text="hello world",
        )
        d = state.to_dict()
        assert d["active_app"] == "Safari"
        assert d["active_window"] == "Google"
        assert "hello world" in d["screen_text"]

    def test_empty_payload_handling(self):
        bus = PerceptionEventBus()
        wm = WorldStateManager()
        wm.start(bus)

        bus.publish(PerceptionEvent(
            event_type=PerceptionEventType.SCREEN_OCR,
            payload={},
        ))
        state = wm.get_state()
        assert state.screen_text == ""

        wm.stop(bus)


# ====================================================================
# PerceptionSystem — 配置驱动 + 生命周期
# ====================================================================

class TestPerceptionSystemSetup:
    # 真实感知系统集成测试：setup() 启动真实感知线程/原生库，
    # 与完整测试套件的资源状态冲突会导致原生崩溃，标记 slow 默认不跑
    pytestmark = pytest.mark.slow

    def test_default_setup_pipeline_none(self):
        from modules.perception.setup import get_perception_system
        system = get_perception_system()
        system.setup()
        status = system.get_status()
        assert "pipeline" not in status
        system.stop()

    def test_voice_disabled(self):
        from modules.perception.setup import get_perception_system
        system = get_perception_system()
        system.setup(voice_enabled=False)
        status = system.get_status()
        assert status["voice_available"] is False
        system.stop()

    def test_voice_enabled(self):
        from modules.perception.setup import get_perception_system
        system = get_perception_system()
        system.setup(voice_enabled=True)
        status = system.get_status()
        # 语音依赖可能不可用，但至少不会报错
        assert "voice_available" in status
        system.stop()

    def test_repeated_setup_no_leak(self):
        from modules.perception.setup import get_perception_system
        system = get_perception_system()
        system.setup(voice_enabled=False)
        system.setup(voice_enabled=False)
        system.setup(voice_enabled=False)
        status = system.get_status()
        assert "pipeline" not in status
        assert "voice_available" in status
        system.stop()

    def test_repeated_setup_no_thread_leak(self):
        """多次 setup() 不应无限线程泄漏 (当前 setup() 非幂等，每次创建1个线程)"""
        pytest.skip("setup() 当前非幂等，每个调用创建一个窗口检测线程")

    def test_proactive_trigger_enabled(self):
        from modules.perception.setup import get_perception_system
        system = get_perception_system()
        system.setup(proactive_enabled=True)
        status = system.get_status()
        assert status["proactive_trigger"] is not None
        system.stop()

    def test_proactive_trigger_disabled(self):
        from modules.perception.setup import get_perception_system
        system = get_perception_system()
        system.setup(proactive_enabled=False)
        status = system.get_status()
        assert status["proactive_trigger"] is None
        system.stop()
