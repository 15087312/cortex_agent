"""感知系统单元测试

覆盖: Event Bus, Event Types, Frame Diff, ROI Dispatcher,
      Detectors (OCR/UI/Window), ROI Manager, WorldState,
      PerceptionDifferenceSource, Pipeline, Setup
"""
import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from modules.perception.events.bus import PerceptionEventBus
from modules.perception.pipeline.frame_diff import FrameDiffDetector, FrameDiffResult
from modules.perception.pipeline.roi_dispatcher import ROIDispatcher, ROIRegion
from modules.perception.detectors.base import PerceptionDetector
from modules.perception.detectors.ocr_detector import OCRDetector
from modules.perception.detectors.ui_detector import UIDetector
from modules.perception.detectors.window_detector import WindowDetector
from modules.perception.roi.manager import ROIManager
from modules.perception.state.world_state import WorldState, WorldStateManager
from modules.perception.state.perception_source import PerceptionDifferenceSource
from modules.perception.pipeline.capture import _NullBackend, create_capture_backend


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


# ====================================================================
# Frame Diff Detector
# ====================================================================

class TestFrameDiffDetector:
    def test_first_frame_always_changed(self):
        fd = FrameDiffDetector()
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        result = fd.detect(frame)
        assert result.has_changed is True
        assert result.change_ratio == 1.0

    def test_no_change(self):
        fd = FrameDiffDetector()
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        fd.detect(frame)  # 首帧
        result = fd.detect(frame.copy())
        assert result.has_changed is False
        assert result.change_ratio < 0.01

    def test_significant_change(self):
        fd = FrameDiffDetector()
        frame1 = np.zeros((100, 100, 3), dtype=np.uint8)
        frame2 = frame1.copy()
        frame2[10:60, 10:60] = 255  # 25% 变化
        fd.detect(frame1)
        result = fd.detect(frame2)
        assert result.has_changed is True
        assert result.change_ratio > 0.2  # 25% 变化应 > 20%
        assert len(result.changed_regions) > 0

    def test_small_change_ignored(self):
        fd = FrameDiffDetector(change_area_threshold=0.05)
        frame1 = np.zeros((100, 100, 3), dtype=np.uint8)
        frame2 = frame1.copy()
        frame2[0:2, 0:2] = 255  # 0.04% 变化
        fd.detect(frame1)
        result = fd.detect(frame2)
        assert result.has_changed is False

    def test_reset(self):
        fd = FrameDiffDetector()
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        fd.detect(frame)
        fd.reset()
        result = fd.detect(frame)
        assert result.has_changed is True  # 首帧重新开始

    def test_grayscale_input(self):
        fd = FrameDiffDetector()
        frame = np.zeros((100, 100), dtype=np.uint8)
        result = fd.detect(frame)
        assert result.has_changed is True

    def test_size_mismatch(self):
        fd = FrameDiffDetector()
        fd.detect(np.zeros((100, 100, 3), dtype=np.uint8))
        result = fd.detect(np.zeros((50, 50, 3), dtype=np.uint8))
        assert result.has_changed is True

    def test_empty_frame(self):
        fd = FrameDiffDetector()
        result = fd.detect(np.array([]))
        assert result.has_changed is False

    def test_none_frame(self):
        fd = FrameDiffDetector()
        result = fd.detect(None)
        assert result.has_changed is False


# ====================================================================
# ROI Dispatcher
# ====================================================================

class TestROIDispatcher:
    def test_dispatch_to_matching_roi(self):
        rd = ROIDispatcher()
        rd.register_roi(ROIRegion("test", (0, 0, 100, 100), "ocr", overlap_threshold=0.05))
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        result = rd.dispatch([(0, 0, 50, 50)], frame)
        assert "ocr" in result
        assert len(result["ocr"]) == 1
        assert result["ocr"][0][0] == "test"

    def test_dispatch_no_overlap(self):
        rd = ROIDispatcher()
        rd.register_roi(ROIRegion("test", (0, 0, 50, 50), "ocr"))
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        result = rd.dispatch([(80, 80, 10, 10)], frame)
        assert "ocr" not in result

    def test_dispatch_ignore_type(self):
        rd = ROIDispatcher()
        rd.register_roi(ROIRegion("video", (0, 0, 100, 100), "ignore"))
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        result = rd.dispatch([(0, 0, 50, 50)], frame)
        assert len(result) == 0

    def test_dispatch_empty_regions(self):
        rd = ROIDispatcher()
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        result = rd.dispatch([], frame)
        assert len(result) == 0

    def test_dispatch_full_frame(self):
        rd = ROIDispatcher()
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        result = rd.dispatch_full_frame(frame, "ocr")
        assert "ocr" in result
        assert result["ocr"][0][0] == "_full_frame"

    def test_unregister_roi(self):
        rd = ROIDispatcher()
        rd.register_roi(ROIRegion("test", (0, 0, 100, 100), "ocr"))
        assert rd.unregister_roi("test") is True
        assert rd.unregister_roi("test") is False
        assert len(rd.get_rois()) == 0

    def test_overlap_calculation(self):
        assert ROIDispatcher._calc_overlap((0, 0, 10, 10), (5, 5, 10, 10)) == 25
        assert ROIDispatcher._calc_overlap((0, 0, 10, 10), (20, 20, 10, 10)) == 0

    def test_priority_order(self):
        rd = ROIDispatcher()
        rd.register_roi(ROIRegion("low", (0, 0, 100, 100), "ocr", priority=1))
        rd.register_roi(ROIRegion("high", (0, 0, 100, 100), "ui", priority=10))
        rois = rd.get_rois()
        assert rois[0].name == "high"


# ====================================================================
# Detectors
# ====================================================================

class TestOCRDetector:
    def test_not_available_without_engine(self):
        """无 OCR 引擎时应该不可用"""
        with patch.dict("sys.modules", {"rapidocr_onnxruntime": None, "paddleocr": None}):
            det = OCRDetector()
            assert det.is_available() is False
            assert det.detect(np.zeros((50, 50, 3), dtype=np.uint8), "test") == []

    def test_available_with_engine(self):
        """有 OCR 引擎时应该可用并能提取文本"""
        det = OCRDetector()
        if not det.is_available():
            pytest.skip("无 OCR 引擎")
        # 纯白图应该返回空或极少文字
        result = det.detect(np.ones((100, 200, 3), dtype=np.uint8) * 255, "test")
        assert isinstance(result, list)

    def test_detect_returns_events_on_change(self):
        """OCR 检测到文字变化时应返回事件"""
        det = OCRDetector()
        if not det.is_available():
            pytest.skip("无 OCR 引擎")
        # 第一次检测
        img1 = np.zeros((100, 200, 3), dtype=np.uint8)
        det._extract_text = MagicMock(return_value="hello")
        events1 = det.detect(img1, "screen")
        assert len(events1) == 1  # 首次检测应有事件
        # 相同内容不应重复触发
        events2 = det.detect(img1, "screen")
        assert len(events2) == 0

    def test_detector_type(self):
        det = OCRDetector()
        assert det.detector_type == "ocr"

    def test_empty_image(self):
        det = OCRDetector()
        assert det.detect(np.array([]), "test") == []

    def test_diff_text(self):
        new = OCRDetector._diff_text("hello\nworld", "hello\nworld\nnew")
        assert new == ["new"]

    def test_diff_text_empty_old(self):
        new = OCRDetector._diff_text("", "hello\nworld")
        assert new == ["hello", "world"]

    def test_diff_text_no_change(self):
        new = OCRDetector._diff_text("hello", "hello")
        assert new == []

    def test_paddleocr_v36_dict_format(self):
        """PaddleOCR 3.6+ 返回 dict 格式而非 list，必须正确处理"""
        from unittest.mock import MagicMock, patch

        det = OCRDetector()
        # 模拟 PaddleOCR 3.6 dict 格式
        det._ocr_type = "paddleocr"
        det._ocr_engine = MagicMock()
        det._ocr_engine.ocr.return_value = [{
            "rec_texts": ["Hello World", "def test():", "print('hi')"],
            "rec_scores": [0.95, 0.88, 0.92],
            "rec_polys": [],
        }]

        img = np.zeros((100, 200, 3), dtype=np.uint8)
        text = det._extract_text(img)
        assert "Hello World" in text
        assert "def test():" in text

    def test_paddleocr_v36_empty_texts(self):
        """PaddleOCR 3.6 dict 格式，无文字"""
        from unittest.mock import MagicMock

        det = OCRDetector()
        det._ocr_type = "paddleocr"
        det._ocr_engine = MagicMock()
        det._ocr_engine.ocr.return_value = [{
            "rec_texts": [],
            "rec_scores": [],
            "rec_polys": [],
        }]

        img = np.zeros((100, 200, 3), dtype=np.uint8)
        text = det._extract_text(img)
        assert text == ""

    def test_paddleocr_legacy_list_format(self):
        """PaddleOCR 旧版 list 格式仍需兼容"""
        from unittest.mock import MagicMock

        det = OCRDetector()
        det._ocr_type = "paddleocr"
        det._ocr_engine = MagicMock()
        # 旧格式: result = [[[bbox, (text, confidence)], ...]]
        det._ocr_engine.ocr.return_value = [[
            [[[0, 0], [100, 0], [100, 20], [0, 20]], ("Hello", 0.95)],
            [[[0, 30], [100, 30], [100, 50], [0, 50]], ("World", 0.88)],
        ]]

        img = np.zeros((100, 200, 3), dtype=np.uint8)
        text = det._extract_text(img)
        assert "Hello" in text
        assert "World" in text


class TestUIDetector:
    def test_detector_type(self):
        det = UIDetector()
        assert det.detector_type == "ui"

    def test_not_available_without_cv2(self):
        """无 cv2 时应该不可用"""
        with patch("modules.perception.detectors.ui_detector.HAS_CV2", False):
            det = UIDetector()
            assert det.is_available() is False
            assert det.detect(np.zeros((50, 50, 3), dtype=np.uint8), "test") == []

    def test_no_templates_no_events(self):
        """无模板时不应产生事件"""
        det = UIDetector()
        if not det.is_available():
            pytest.skip("无 cv2")
        result = det.detect(np.zeros((100, 100, 3), dtype=np.uint8), "test")
        assert result == []


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


class TestNullBackend:
    def test_not_available(self):
        backend = _NullBackend()
        assert backend.is_available() is False

    def test_get_frame_returns_none(self):
        backend = _NullBackend()
        assert backend.get_frame() is None

    def test_platform_name(self):
        backend = _NullBackend()
        assert backend.platform_name == "null"


# ====================================================================
# ROI Manager
# ====================================================================

class TestROIManager:
    def test_add_and_get(self):
        rm = ROIManager()
        rm.add(ROIRegion("test", (0, 0, 100, 100), "ocr"))
        assert rm.get("test") is not None
        assert rm.get("test").detector_type == "ocr"

    def test_remove(self):
        rm = ROIManager()
        rm.add(ROIRegion("test", (0, 0, 100, 100), "ocr"))
        assert rm.remove("test") is True
        assert rm.get("test") is None
        assert rm.remove("test") is False

    def test_get_all(self):
        rm = ROIManager()
        rm.add(ROIRegion("a", (0, 0, 100, 100), "ocr"))
        rm.add(ROIRegion("b", (0, 0, 100, 100), "ui"))
        assert len(rm.get_all()) == 2

    def test_apply_to_dispatcher(self):
        rm = ROIManager()
        rm.add(ROIRegion("test", (0, 0, 100, 100), "ocr"))
        rd = ROIDispatcher()
        rm.apply_to_dispatcher(rd)
        assert len(rd.get_rois()) == 1

    def test_save_and_load(self, tmp_path):
        path = str(tmp_path / "rois.json")
        rm = ROIManager(config_path=path)
        rm.add(ROIRegion("test", (0, 0, 100, 100), "ocr", priority=5))
        assert rm.save_to_file() is True

        rm2 = ROIManager()
        assert rm2.load_from_file(path) is True
        assert len(rm2.get_all()) == 1
        assert rm2.get("test").priority == 5


# ====================================================================
# World State
# ====================================================================

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
# PerceptionDifferenceSource
# ====================================================================

class TestPerceptionDifferenceSource:
    def test_convert_events_to_differences(self):
        bus = PerceptionEventBus()
        src = PerceptionDifferenceSource(event_bus=bus)
        src.start(bus)

        event = PerceptionEvent(
            event_type=PerceptionEventType.SCREEN_OCR,
            source="ocr",
            payload={"text": "hello"},
        )
        bus.publish(event)

        diffs = src.detect()
        assert len(diffs) == 1
        assert diffs[0].source_type == "perception"
        assert diffs[0].category == "screen_ocr"

        src.stop(bus)

    def test_non_mapped_event_ignored(self):
        bus = PerceptionEventBus()
        src = PerceptionDifferenceSource(event_bus=bus)
        src.start(bus)

        bus.publish(PerceptionEvent(event_type="unknown.type"))
        diffs = src.detect()
        assert len(diffs) == 0

        src.stop(bus)

    def test_queue_full_drops_oldest(self):
        bus = PerceptionEventBus()
        src = PerceptionDifferenceSource(event_bus=bus)
        # 缩小队列
        import queue
        src._event_queue = queue.Queue(maxsize=2)
        src.start(bus)

        for i in range(5):
            bus.publish(PerceptionEvent(
                event_type=PerceptionEventType.SCREEN_OCR,
                payload={"i": i},
            ))

        diffs = src.detect()
        assert len(diffs) == 2, f"队列应恰好 2 个: {len(diffs)}"

        src.stop(bus)

    def test_start_stop_idempotent(self):
        bus = PerceptionEventBus()
        src = PerceptionDifferenceSource(event_bus=bus)
        src.start(bus)
        src.start(bus)  # 不应重复订阅
        src.stop(bus)
        src.stop(bus)  # 不应报错


# ====================================================================
# Capture Backend Factory
# ====================================================================

class TestCaptureFactory:
    def test_create_returns_backend(self):
        backend = create_capture_backend()
        assert backend is not None
        assert hasattr(backend, "is_available")
        assert hasattr(backend, "start")
        assert hasattr(backend, "stop")
        assert hasattr(backend, "get_frame")

    def test_null_backend_frame(self):
        backend = _NullBackend()
        backend.start()
        assert backend.get_frame() is None
        backend.stop()


# ====================================================================
# Perception Pipeline
# ====================================================================

class TestPerceptionPipeline:
    def test_stats_initial(self):
        from modules.perception.pipeline.pipeline import PerceptionPipeline
        pipeline = PerceptionPipeline()
        stats = pipeline.get_stats()
        assert stats["frames_captured"] == 0
        assert stats["events_published"] == 0

    def test_register_detector(self):
        from modules.perception.pipeline.pipeline import PerceptionPipeline
        pipeline = PerceptionPipeline()
        det = MagicMock(spec=PerceptionDetector)
        det.detector_type = "test"
        det.is_available.return_value = True
        pipeline.register_detector(det)
        assert "test" in pipeline._detectors
