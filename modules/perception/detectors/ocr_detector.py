"""
OCR 检测器 — 屏幕文字识别

被动感知组件，定时截图并识别文字，发布 OCR 事件。
"""
import time
import threading
from typing import Any, Dict, List, Optional

import numpy as np

from modules.perception.detectors.base import PerceptionDetector
from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from utils.logger import setup_logger

logger = setup_logger("perception_ocr_detector")


class OCRDetector(PerceptionDetector):
    """OCR 检测器

    后台线程定时截图 + OCR，识别文字变化并发布事件。
    """

    def __init__(self, interval: float = 10.0, min_confidence: float = 0.6):
        """
        Args:
            interval: 检测间隔（秒）
            min_confidence: 最小置信度阈值
        """
        self._interval = interval
        self._min_confidence = min_confidence
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._ocr_engine = None
        self._last_texts: List[str] = []
        self._stop_event = threading.Event()

    @property
    def detector_type(self) -> str:
        return "ocr"

    def is_available(self) -> bool:
        try:
            from rapidocr_onnxruntime import RapidOCR
            return True
        except ImportError:
            return False

    def start(self) -> None:
        """启动后台 OCR 检测线程"""
        if not self.is_available() or self._running:
            return

        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._ocr_loop,
            daemon=True,
            name="perception-ocr",
        )
        self._thread.start()
        logger.info(f"OCR 检测器: 已启动 (间隔 {self._interval}s)")

    def stop(self) -> None:
        """停止检测"""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        logger.info("OCR 检测器: 已停止")

    def _ocr_loop(self) -> None:
        """后台 OCR 循环"""
        while not self._stop_event.is_set():
            try:
                self._run_ocr()
            except Exception as e:
                logger.debug(f"OCR 检测异常: {e}")

            # 等待，支持提前唤醒
            self._stop_event.wait(self._interval)

    def _run_ocr(self) -> None:
        """执行一次 OCR"""
        from utils.screen_capture import capture_screen

        # 截图
        screenshot = capture_screen()
        if not screenshot:
            return

        # OCR 识别
        texts = self._ocr识别(screenshot)
        if not texts:
            return

        # 检测文字变化
        new_texts = [t for t in texts if t not in self._last_texts]
        removed_texts = [t for t in self._last_texts if t not in texts]

        if new_texts or removed_texts:
            self._last_texts = texts
            self._publish_event(new_texts, removed_texts, texts)

    def _ocr识别(self, screenshot_b64: str) -> List[str]:
        """执行 OCR 识别"""
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

                # 过滤低置信度并提取文字
                texts = []
                for item in result:
                    text = item[1]
                    confidence = float(item[2])
                    if confidence >= self._min_confidence and text.strip():
                        texts.append(text.strip())
                return texts
            finally:
                os.unlink(tmp_path)

        except Exception as e:
            logger.debug(f"OCR 识别失败: {e}")
            return []

    def _publish_event(self, new_texts: List[str], removed_texts: List[str], all_texts: List[str]) -> None:
        """发布 OCR 事件"""
        from modules.perception.events.bus import get_event_bus

        # 构建描述
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
        event.description = description

        try:
            bus = get_event_bus()
            bus.publish(event)
        except Exception:
            pass

    def detect(self, roi_image: np.ndarray, roi_name: str, context: Optional[Dict] = None) -> List[PerceptionEvent]:
        """PerceptionDetector 接口实现"""
        return []


# 全局单例
_detector: Optional[OCRDetector] = None


def get_ocr_detector() -> OCRDetector:
    global _detector
    if _detector is None:
        _detector = OCRDetector()
    return _detector
