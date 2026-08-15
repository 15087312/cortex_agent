"""
OCR 检测器 — 屏幕文字识别

被动感知组件，监听 SCREEN_DIFF 事件，大幅变化时触发 OCR。
"""
import time
import threading
import weakref
from typing import Any, Dict, List, Optional

import numpy as np

from modules.perception.detectors.base import PerceptionDetector
from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from utils.logger import setup_logger

logger = setup_logger("perception_ocr_detector")

# 触发 OCR 的最小变化比例
OCR_TRIGGER_THRESHOLD = 0.35


class OCRDetector(PerceptionDetector):
    """OCR 检测器

    监听 SCREEN_DIFF 事件，变化比例 >= 15% 时触发 OCR 识别。

    活跃实例追踪（weakref）：测试/退出时统一 stop，避免后台线程遗留。
    """

    _all_instances: "weakref.WeakSet[OCRDetector]" = weakref.WeakSet()

    def __init__(self, threshold: float = OCR_TRIGGER_THRESHOLD, cooldown: float = 5.0):
        self._threshold = threshold
        OCRDetector._all_instances.add(self)
        self._cooldown = cooldown
        self._running = False
        self._ocr_engine: Any = None
        self._last_texts: List[str] = []
        self._last_trigger_time: float = 0.0
        self._sub_id: str = ""
        self._event_bus = None
        self._lock = threading.Lock()

    @property
    def detector_type(self) -> str:
        return "ocr"

    def is_available(self) -> bool:
        try:
            from rapidocr_onnxruntime import RapidOCR  # noqa: F401
            return True
        except ImportError:
            return False

    def start(self, event_bus=None) -> None:
        """启动 OCR 检测器，订阅 SCREEN_DIFF 事件"""
        if not self.is_available() or self._running:
            return

        self._event_bus = event_bus
        if event_bus:
            self._sub_id = event_bus.subscribe(
                PerceptionEventType.SCREEN_DIFF,
                handler=self._on_screen_diff,
            )

        self._running = True
        logger.info(f"OCR 检测器: 已启动 (阈值={self._threshold:.0%}, 冷却={self._cooldown}s)")

    def stop(self) -> None:
        self._running = False
        if self._event_bus and self._sub_id:
            self._event_bus.unsubscribe(self._sub_id)
            self._sub_id = ""
        OCRDetector._all_instances.discard(self)
        logger.info("OCR 检测器: 已停止")

    def _on_screen_diff(self, event) -> None:
        """屏幕变化事件回调"""
        change_ratio = event.payload.get("change_ratio", 0)

        if change_ratio < self._threshold:
            return

        with self._lock:
            now = time.time()
            if now - self._last_trigger_time < self._cooldown:
                return
            self._last_trigger_time = now

        logger.debug(f"触发 OCR: change_ratio={change_ratio:.0%}")
        threading.Thread(
            target=self._run_ocr,
            daemon=True,
            name="ocr-trigger",
        ).start()

    def _run_ocr(self) -> None:
        from utils.screen_capture import capture_screen

        screenshot = capture_screen()
        if not screenshot:
            return

        texts = self._ocr识别(screenshot)
        if not texts:
            return

        new_texts = [t for t in texts if t not in self._last_texts]
        removed_texts = [t for t in self._last_texts if t not in texts]

        if new_texts or removed_texts:
            self._last_texts = texts
            self._publish_event(new_texts, removed_texts, texts)

    def _ocr识别(self, screenshot_b64: str) -> List[str]:
        import base64
        import tempfile
        import os

        try:
            from rapidocr_onnxruntime import RapidOCR

            if self._ocr_engine is None:
                self._ocr_engine = RapidOCR()

            img_data = base64.b64decode(screenshot_b64)
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
                f.write(img_data)
                tmp_path = f.name

            try:
                result, _ = self._ocr_engine(tmp_path)
                if not result:
                    return []

                texts = []
                for item in result:
                    text = item[1]
                    confidence = float(item[2])
                    if confidence >= 0.6 and text.strip():
                        texts.append(text.strip())
                return texts
            finally:
                os.unlink(tmp_path)

        except Exception as e:
            logger.debug(f"OCR 识别失败: {e}")
            return []

    def _publish_event(self, new_texts: List[str], removed_texts: List[str], all_texts: List[str]) -> None:
        from modules.perception.events.bus import get_event_bus

        desc_parts = []
        if new_texts:
            desc_parts.append(f"新增文字: {', '.join(new_texts[:5])}")
        if removed_texts:
            desc_parts.append(f"消失文字: {', '.join(removed_texts[:5])}")

        description = " | ".join(desc_parts) if desc_parts else f"屏幕文字: {len(all_texts)} 段"

        event = PerceptionEvent(
            event_type=PerceptionEventType.SCREEN_OCR,
            source="ocr_detector",
            importance=0.6,
            payload={
                "new_texts": new_texts[:20],
                "removed_texts": removed_texts[:20],
                "all_texts": all_texts[:50],
                "text_count": len(all_texts),
            },
        )
        event.description = description  # type: ignore[attr-defined]

        try:
            bus = get_event_bus()
            bus.publish(event)
        except Exception:
            pass

    def detect(self, roi_image: np.ndarray, roi_name: str, context: Optional[Dict] = None) -> List[PerceptionEvent]:
        return []


_detector: Optional[OCRDetector] = None


def get_ocr_detector() -> OCRDetector:
    global _detector
    if _detector is None:
        _detector = OCRDetector()
    return _detector
