"""感知流水线 — Capture → FrameDiff → ROI → Detectors → Events

核心编排器，驱动整个感知系统的运行。
"""
import threading
import time
from typing import Any, Dict, List, Optional

import numpy as np

from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from modules.perception.events.bus import PerceptionEventBus, get_event_bus
from modules.perception.pipeline.capture import CaptureBackend, create_capture_backend
from modules.perception.pipeline.frame_diff import FrameDiffDetector, FrameDiffResult
from modules.perception.pipeline.roi_dispatcher import ROIDispatcher, ROIRegion
from modules.perception.detectors.base import PerceptionDetector
from utils.logger import setup_logger

logger = setup_logger("perception_pipeline")

# 尝试导入 psutil 用于 CPU 监控（可选）
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


class PerceptionPipeline:
    """感知流水线

    运行流程:
    1. Capture: 从屏幕获取帧
    2. FrameDiff: 与上一帧比较，检测变化
    3. ROI Dispatch: 将变化区域路由到对应检测器
    4. Detectors: 各检测器处理 ROI，产出事件
    5. Event Bus: 发布事件

    用法:
        pipeline = PerceptionPipeline()
        pipeline.start()
        ...
        pipeline.stop()
    """

    def __init__(
        self,
        capture: Optional[CaptureBackend] = None,
        frame_diff: Optional[FrameDiffDetector] = None,
        roi_dispatcher: Optional[ROIDispatcher] = None,
        detectors: Optional[Dict[str, PerceptionDetector]] = None,
        event_bus: Optional[PerceptionEventBus] = None,
        fps: int = 5,
    ):
        self._capture = capture or create_capture_backend()
        self._frame_diff = frame_diff or FrameDiffDetector()
        self._roi_dispatcher = roi_dispatcher or ROIDispatcher()
        self._detectors: Dict[str, PerceptionDetector] = detectors or {}
        self._event_bus = event_bus or get_event_bus()
        self._fps = fps

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._platform = self._capture.platform_name

        # 统计
        self._stats = {
            "frames_captured": 0,
            "frames_with_change": 0,
            "events_published": 0,
            "roi_dispatches": 0,
            "detector_calls": {dt: 0 for dt in self._detectors},
            "start_time": 0.0,
            "errors": 0,
        }

    def register_detector(self, detector: PerceptionDetector) -> None:
        """注册检测器"""
        self._detectors[detector.detector_type] = detector
        self._stats["detector_calls"][detector.detector_type] = 0
        logger.info(f"注册检测器: {detector.detector_type} (available={detector.is_available()})")

    def start(self) -> None:
        """启动流水线"""
        if self._running:
            return

        if not self._capture.is_available():
            logger.error("捕获后端不可用，流水线无法启动")
            return

        self._capture.start(fps=self._fps)
        self._running = True
        self._stats["start_time"] = time.time()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="perception-pipeline"
        )
        self._thread.start()
        logger.info(
            f"感知流水线启动: fps={self._fps} platform={self._platform} "
            f"detectors={list(self._detectors.keys())}"
        )

    def stop(self) -> None:
        """停止流水线"""
        self._running = False
        self._capture.stop()
        if self._thread:
            self._thread.join(timeout=5)
        for det in self._detectors.values():
            det.reset()
        self._frame_diff.reset()
        logger.info("感知流水线已停止")

    @property
    def is_running(self) -> bool:
        return self._running

    def get_stats(self) -> Dict[str, Any]:
        """获取流水线统计"""
        elapsed = time.time() - self._stats["start_time"] if self._stats["start_time"] else 0
        return {
            **self._stats,
            "elapsed_seconds": round(elapsed, 1),
            "actual_fps": round(self._stats["frames_captured"] / elapsed, 1) if elapsed > 0 else 0,
            "capture_backend": self._capture.platform_name,
            "detectors": {
                dt: {"available": d.is_available(), "calls": self._stats["detector_calls"].get(dt, 0)}
                for dt, d in self._detectors.items()
            },
        }

    def _run_loop(self):
        """主循环"""
        interval = 1.0 / self._fps
        consecutive_no_change = 0

        while self._running:
            try:
                frame = self._capture.get_frame()
                if frame is None:
                    time.sleep(interval)
                    continue

                self._stats["frames_captured"] += 1

                # 帧差检测
                diff_result = self._frame_diff.detect(frame)

                # ── 非图像检测器（独立于帧差，每帧都运行）──
                self._run_non_image_detectors()

                if not diff_result.has_changed:
                    consecutive_no_change += 1
                    if consecutive_no_change > 10:
                        time.sleep(interval * 2)
                    else:
                        time.sleep(interval)
                    continue

                consecutive_no_change = 0
                self._stats["frames_with_change"] += 1

                # ROI 分发
                has_rois = len(self._roi_dispatcher.get_rois()) > 0
                if has_rois:
                    dispatched = self._roi_dispatcher.dispatch(
                        diff_result.changed_regions, frame
                    )
                else:
                    # 无 ROI 定义，全帧发送到默认检测器
                    default_type = self._get_default_detector_type()
                    if default_type:
                        dispatched = self._roi_dispatcher.dispatch_full_frame(
                            frame, default_type
                        )
                    else:
                        dispatched = {}

                self._stats["roi_dispatches"] += 1

                # 检测器处理
                for detector_type, roi_items in dispatched.items():
                    detector = self._detectors.get(detector_type)
                    if not detector or not detector.is_available():
                        continue

                    for roi_name, roi_image in roi_items:
                        try:
                            events = detector.detect(roi_image, roi_name)
                            self._stats["detector_calls"][detector_type] = \
                                self._stats["detector_calls"].get(detector_type, 0) + 1

                            for event in events:
                                event.platform = self._platform
                                self._event_bus.publish(event)
                                self._stats["events_published"] += 1
                        except Exception as e:
                            self._stats["errors"] += 1
                            logger.warning(f"检测器 {detector_type} 异常: {e}")

                time.sleep(interval)

            except Exception as e:
                self._stats["errors"] += 1
                logger.error(f"流水线异常: {e}")
                time.sleep(1)

    def _run_non_image_detectors(self):
        """运行非图像检测器（如窗口状态）

        这些检测器不依赖屏幕帧，使用系统 API，每帧都运行。
        """
        _NON_IMAGE_TYPES = {"window", "voice"}
        for det_type in _NON_IMAGE_TYPES:
            detector = self._detectors.get(det_type)
            if not detector or not detector.is_available():
                continue
            try:
                events = detector.detect(
                    np.empty(0), "_system", context=None
                )
                self._stats["detector_calls"][det_type] = \
                    self._stats["detector_calls"].get(det_type, 0) + 1
                for event in events:
                    event.platform = self._platform
                    self._event_bus.publish(event)
                    self._stats["events_published"] += 1
            except Exception as e:
                self._stats["errors"] += 1
                logger.warning(f"非图像检测器 {det_type} 异常: {e}")

    def _get_default_detector_type(self) -> Optional[str]:
        """获取默认检测器类型（无 ROI 时使用）"""
        # 优先 OCR，其次 UI
        for dtype in ("ocr", "ui", "motion"):
            if dtype in self._detectors and self._detectors[dtype].is_available():
                return dtype
        return None
