"""感知系统补全测试 — 覆盖审计发现的测试缺口

覆盖: ThinkTrigger, VoiceDetector(mock), Pipeline集成, WorldState边界,
      Event Bus异步, ROI Manager边界, FrameDiff边界, Setup配置驱动
"""
import asyncio
import collections
import json
import threading
import time
from unittest.mock import MagicMock, patch, AsyncMock

import numpy as np
import pytest

from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from modules.perception.events.bus import PerceptionEventBus
from modules.perception.pipeline.frame_diff import FrameDiffDetector
from modules.perception.pipeline.roi_dispatcher import ROIDispatcher, ROIRegion
from modules.perception.detectors.voice_detector import VoiceDetector
from modules.perception.detectors.ocr_detector import OCRDetector
from modules.perception.detectors.window_detector import WindowDetector
from modules.perception.roi.manager import ROIManager
from modules.perception.state.world_state import WorldState, WorldStateManager
from modules.perception.state.perception_source import PerceptionDifferenceSource
from modules.perception.state.think_trigger import PerceptionThinkTrigger


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
# ThinkTrigger — 完整测试
# ====================================================================

class TestThinkTrigger:
    def test_intensity_filter(self):
        bus = PerceptionEventBus()
        trigger = PerceptionThinkTrigger(min_intensity=50, cooldown_seconds=0)
        trigger.start(bus)

        # 低强度不触发
        bus.publish(PerceptionEvent(
            event_type=PerceptionEventType.DIFFERENCE_DETECTED,
            payload={"intensity": 30},
        ))
        time.sleep(0.05)
        assert trigger._trigger_count == 0

        # 高强度触发
        bus.publish(PerceptionEvent(
            event_type=PerceptionEventType.DIFFERENCE_DETECTED,
            payload={"intensity": 60},
        ))
        time.sleep(0.05)
        assert trigger._trigger_count == 1

        trigger.stop(bus)

    def test_cooldown(self):
        bus = PerceptionEventBus()
        trigger = PerceptionThinkTrigger(min_intensity=50, cooldown_seconds=1)
        trigger.start(bus)

        bus.publish(PerceptionEvent(
            event_type=PerceptionEventType.DIFFERENCE_DETECTED,
            payload={"intensity": 60},
        ))
        time.sleep(0.05)
        assert trigger._trigger_count == 1

        # 冷却期内不触发
        bus.publish(PerceptionEvent(
            event_type=PerceptionEventType.DIFFERENCE_DETECTED,
            payload={"intensity": 70},
        ))
        time.sleep(0.05)
        assert trigger._trigger_count == 1

        # 冷却过后触发
        time.sleep(1.1)
        bus.publish(PerceptionEvent(
            event_type=PerceptionEventType.DIFFERENCE_DETECTED,
            payload={"intensity": 60},
        ))
        time.sleep(0.05)
        assert trigger._trigger_count == 2

        trigger.stop(bus)

    def test_trigger_count_in_lock(self):
        """验证计数器在锁内，不会出现双触发"""
        bus = PerceptionEventBus()
        trigger = PerceptionThinkTrigger(min_intensity=50, cooldown_seconds=0)
        trigger.start(bus)

        # 并发发送
        threads = []
        for _ in range(10):
            t = threading.Thread(
                target=lambda: bus.publish(PerceptionEvent(
                    event_type=PerceptionEventType.DIFFERENCE_DETECTED,
                    payload={"intensity": 60},
                ))
            )
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        time.sleep(0.2)

        # 所有事件都应该触发（cooldown=0）
        assert trigger._trigger_count == 10
        trigger.stop(bus)

    def test_build_context(self):
        trigger = PerceptionThinkTrigger()
        event = PerceptionEvent(
            event_type=PerceptionEventType.DIFFERENCE_DETECTED,
            payload={
                "intensity": 60, "category": "idle_alert",
                "source_type": "time", "description": "空闲 15 分钟",
            },
        )
        ctx = trigger._build_context(event)
        assert "idle_alert" in ctx
        assert "空闲 15 分钟" in ctx
        assert "60" in ctx

    def test_get_stats(self):
        trigger = PerceptionThinkTrigger(min_intensity=40, cooldown_seconds=30)
        stats = trigger.get_stats()
        assert stats["trigger_count"] == 0
        assert stats["min_intensity"] == 40
        assert stats["cooldown_seconds"] == 30
        assert stats["has_trigger_port"] is False

    def test_set_trigger_port(self):
        trigger = PerceptionThinkTrigger()
        assert trigger._trigger_port is None
        port = MagicMock()
        trigger.set_trigger_port(port)
        assert trigger._trigger_port is port

    def test_stop_idempotent(self):
        bus = PerceptionEventBus()
        trigger = PerceptionThinkTrigger()
        trigger.start(bus)
        trigger.stop(bus)
        trigger.stop(bus)  # 不应报错


# ====================================================================
# VoiceDetector — Mock 测试
# ====================================================================

class TestVoiceDetector:
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
        det.stop()  # 不应报错

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
        assert len(state.recent_ocr) == 10  # cap

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
        assert len(state.recent_events) == 20  # cap

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
            payload={},  # 空 payload
        ))
        state = wm.get_state()
        assert state.screen_text == ""  # 无 new_lines

        wm.stop(bus)


# ====================================================================
# PerceptionDifferenceSource — 边界测试
# ====================================================================

class TestPerceptionDifferenceSourceAdvanced:
    def test_difference_fields(self):
        bus = PerceptionEventBus()
        src = PerceptionDifferenceSource(event_bus=bus)
        src.start(bus)

        bus.publish(PerceptionEvent(
            event_type=PerceptionEventType.SPEECH_DETECTED,
            source="voice",
            payload={"text": "hello"},
        ))
        diffs = src.detect()
        assert len(diffs) == 1
        d = diffs[0]
        assert d.source_type == "perception"
        assert d.category == "speech"
        assert d.intensity == 35.0
        assert d.ttl == 300
        assert "text" in d.payload

        src.stop(bus)

    def test_fifo_ordering(self):
        bus = PerceptionEventBus()
        src = PerceptionDifferenceSource(event_bus=bus)
        src.start(bus)

        for i in range(5):
            bus.publish(PerceptionEvent(
                event_type=PerceptionEventType.SCREEN_OCR,
                payload={"i": i},
            ))
        diffs = src.detect()
        assert len(diffs) == 5
        assert diffs[0].payload["i"] == 0
        assert diffs[4].payload["i"] == 4

        src.stop(bus)

    def test_detect_drains_queue(self):
        bus = PerceptionEventBus()
        src = PerceptionDifferenceSource(event_bus=bus)
        src.start(bus)

        bus.publish(PerceptionEvent(event_type=PerceptionEventType.SCREEN_OCR))
        bus.publish(PerceptionEvent(event_type=PerceptionEventType.SCREEN_WINDOW))
        diffs = src.detect()
        assert len(diffs) == 2
        assert src.detect() == []  # 队列已空

        src.stop(bus)

    def test_source_type(self):
        src = PerceptionDifferenceSource()
        assert src.source_type == "perception"


# ====================================================================
# FrameDiffDetector — 边界测试
# ====================================================================

class TestFrameDiffDetectorAdvanced:
    def test_change_ratio_accuracy(self):
        fd = FrameDiffDetector()
        frame1 = np.zeros((100, 100, 3), dtype=np.uint8)
        frame2 = frame1.copy()
        frame2[:25, :] = 255  # 25% 变化
        fd.detect(frame1)
        result = fd.detect(frame2)
        assert result.change_ratio > 0.2  # 应该接近 25%

    def test_min_region_area_filter(self):
        fd = FrameDiffDetector(min_region_area=5000)
        frame1 = np.zeros((100, 100, 3), dtype=np.uint8)
        frame2 = frame1.copy()
        frame2[0:5, 0:5] = 255  # 25 像素区域 < 5000
        fd.detect(frame1)
        result = fd.detect(frame2)
        assert len(result.changed_regions) == 0  # 被过滤

    def test_non_square_frame(self):
        fd = FrameDiffDetector()
        frame1 = np.zeros((50, 200, 3), dtype=np.uint8)
        frame2 = frame1.copy()
        frame2[10:40, 50:150] = 255
        fd.detect(frame1)
        result = fd.detect(frame2)
        assert result.has_changed is True

    def test_consecutive_detection(self):
        fd = FrameDiffDetector()
        f1 = np.zeros((100, 100, 3), dtype=np.uint8)
        f2 = f1.copy(); f2[:50, :50] = 128
        f3 = f2.copy(); f3[50:, 50:] = 255

        fd.detect(f1)
        r2 = fd.detect(f2)
        r3 = fd.detect(f3)
        assert r2.has_changed is True
        assert r3.has_changed is True

    def test_custom_threshold(self):
        fd = FrameDiffDetector(threshold=100, change_area_threshold=0.01)
        frame1 = np.zeros((100, 100, 3), dtype=np.uint8)
        frame2 = frame1.copy()
        frame2[:50, :50] = 50  # 低对比度变化
        fd.detect(frame1)
        result = fd.detect(frame2)
        # 阈值 100，像素差 50 < 100，不应检测到
        assert result.has_changed is False


# ====================================================================
# ROI Manager — 边界测试
# ====================================================================

class TestROIManagerAdvanced:
    def test_load_missing_file(self):
        rm = ROIManager()
        assert rm.load_from_file("/nonexistent/path.json") is False

    def test_load_corrupt_json(self, tmp_path):
        path = str(tmp_path / "bad.json")
        with open(path, "w") as f:
            f.write("not json{{{")
        rm = ROIManager()
        assert rm.load_from_file(path) is False

    def test_load_empty_rois(self, tmp_path):
        path = str(tmp_path / "empty.json")
        with open(path, "w") as f:
            json.dump({"rois": []}, f)
        rm = ROIManager()
        assert rm.load_from_file(path) is True
        assert len(rm.get_all()) == 0

    def test_save_no_path(self):
        rm = ROIManager()
        assert rm.save_to_file() is False

    def test_roundtrip_all_fields(self, tmp_path):
        path = str(tmp_path / "full.json")
        rm = ROIManager(config_path=path)
        rm.add(ROIRegion("test", (10, 20, 300, 400), "ocr", priority=5, overlap_threshold=0.2))
        rm.save_to_file()

        rm2 = ROIManager()
        rm2.load_from_file(path)
        roi = rm2.get("test")
        assert roi.rect == (10, 20, 300, 400)
        assert roi.detector_type == "ocr"
        assert roi.priority == 5
        assert roi.overlap_threshold == 0.2

    def test_load_defaults(self):
        rm = ROIManager()
        rm.load_defaults()
        # _DEFAULT_ROIS 目前为空
        assert len(rm.get_all()) == 0


# ====================================================================
# ROIDispatcher — 边界测试
# ====================================================================

class TestROIDispatcherAdvanced:
    def test_boundary_clipping(self):
        rd = ROIDispatcher()
        rd.register_roi(ROIRegion("test", (-10, -10, 200, 200), "ocr", overlap_threshold=0.01))
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        result = rd.dispatch([(0, 0, 50, 50)], frame)
        assert "ocr" in result
        # ROI 被裁剪到帧边界
        _, roi_img = result["ocr"][0]
        assert roi_img.shape[0] <= 100
        assert roi_img.shape[1] <= 100

    def test_multiple_rois_same_type(self):
        rd = ROIDispatcher()
        rd.register_roi(ROIRegion("a", (0, 0, 50, 100), "ocr"))
        rd.register_roi(ROIRegion("b", (50, 0, 50, 100), "ocr"))
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        result = rd.dispatch([(0, 0, 100, 50)], frame)
        assert "ocr" in result
        assert len(result["ocr"]) == 2

    def test_dispatch_empty_frame(self):
        rd = ROIDispatcher()
        result = rd.dispatch([(0, 0, 10, 10)], np.array([]))
        assert result == {}

    def test_clear(self):
        rd = ROIDispatcher()
        rd.register_roi(ROIRegion("test", (0, 0, 100, 100), "ocr"))
        rd.clear()
        assert len(rd.get_rois()) == 0


# ====================================================================
# Setup — 配置驱动测试
# ====================================================================

class TestPerceptionSystemSetup:
    def test_screen_disabled(self):
        from modules.perception.setup import get_perception_system
        system = get_perception_system()
        system.setup(screen_enabled=False, voice_enabled=False, trigger_enabled=False)
        status = system.get_status()
        assert status["pipeline"] is None
        system.stop()

    def test_trigger_enabled(self):
        from modules.perception.setup import get_perception_system
        system = get_perception_system()
        system.setup(screen_enabled=False, trigger_enabled=True, trigger_min_intensity=40)
        status = system.get_status()
        assert status["think_trigger"] is not None
        assert status["think_trigger"]["min_intensity"] == 40
        system.stop()

    def test_voice_disabled(self):
        from modules.perception.setup import get_perception_system
        system = get_perception_system()
        system.setup(voice_enabled=False)
        status = system.get_status()
        assert status["voice_available"] is False
        system.stop()

    def test_repeated_setup_no_leak(self):
        from modules.perception.setup import get_perception_system
        system = get_perception_system()
        system.setup(screen_enabled=True, fps=1)
        system.setup(screen_enabled=False, trigger_enabled=False)
        system.setup(screen_enabled=True, fps=2)
        status = system.get_status()
        assert status["pipeline"] is not None
        system.stop()

    def test_set_think_trigger_port(self):
        from modules.perception.setup import get_perception_system
        system = get_perception_system()
        system.setup(trigger_enabled=True)
        port = MagicMock()
        system.set_think_trigger_port(port)
        assert system.think_trigger._trigger_port is port
        system.stop()
