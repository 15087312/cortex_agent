"""OCR 检测器 — 增量文本检测

核心原则: 只对变化的 ROI 做 OCR，不做全屏 OCR。
通过文本 diff 检测新消息、通知等。
"""
import hashlib
from typing import Any, Dict, List, Optional

import numpy as np

from modules.perception.detectors.base import PerceptionDetector
from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from utils.logger import setup_logger

logger = setup_logger("perception_ocr_detector")


class OCRDetector(PerceptionDetector):
    """OCR 增量检测器

    流程:
    1. 对 ROI 图像做 OCR → 提取文本
    2. 与上一次的文本做 diff
    3. 有新文本 → 产出 SCREEN_OCR 事件
    """

    def __init__(self):
        self._ocr_engine = None
        self._ocr_type = None
        self._prev_texts: Dict[str, str] = {}  # roi_name → 上次完整文本
        self._init_ocr()

    def _init_ocr(self):
        """初始化 OCR 引擎（按优先级尝试）"""
        try:
            from paddleocr import PaddleOCR
            self._ocr_engine = PaddleOCR(lang="ch")
            self._ocr_type = "paddleocr"
            logger.info("OCR 引擎: PaddleOCR")
            return
        except ImportError:
            pass

        try:
            from rapidocr_onnxruntime import RapidOCR
            self._ocr_engine = RapidOCR()
            self._ocr_type = "rapidocr"
            logger.info("OCR 引擎: RapidOCR")
            return
        except ImportError:
            pass

        logger.warning("无可用 OCR 引擎，OCR 检测器禁用")

    def is_available(self) -> bool:
        return self._ocr_engine is not None

    @property
    def detector_type(self) -> str:
        return "ocr"

    def detect(
        self,
        roi_image: np.ndarray,
        roi_name: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[PerceptionEvent]:
        """检测 OCR 文本变化"""
        if not self.is_available() or roi_image is None or roi_image.size == 0:
            return []

        text = self._extract_text(roi_image)
        if text is None:
            return []

        # 与上一次完整文本对比
        prev_text = self._prev_texts.get(roi_name, "")
        self._prev_texts[roi_name] = text

        if text == prev_text:
            return []  # 无变化

        # 计算新增文本行
        new_lines = self._diff_text(prev_text, text)
        if not new_lines:
            return []

        event = PerceptionEvent(
            event_type=PerceptionEventType.SCREEN_OCR,
            source="ocr",
            importance=0.6,
            roi_name=roi_name,
            payload={
                "text": text,
                "new_lines": new_lines,
                "prev_text": prev_text,
            },
        )

        logger.debug(f"OCR 变化 [{roi_name}]: {len(new_lines)} 新行")
        return [event]

    def _extract_text(self, image: np.ndarray) -> Optional[str]:
        """从图像提取文本"""
        try:
            if self._ocr_type == "paddleocr":
                result = self._ocr_engine.ocr(image, cls=True)
                if not result or not result[0]:
                    return ""
                # PaddleOCR 3.6+ 返回 dict 格式
                texts = result[0].get("rec_texts", [])
                return "\n".join(t for t in texts if t)

            elif self._ocr_type == "rapidocr":
                result, _ = self._ocr_engine(image)
                if not result:
                    return ""
                lines = [item[1] for item in result if item and len(item) >= 2]
                return "\n".join(lines)

        except Exception as e:
            logger.debug(f"OCR 提取失败: {e}")
        return None

    @staticmethod
    def _diff_text(old_text: str, new_text: str) -> List[str]:
        """计算新增文本行"""
        old_lines = set(old_text.split("\n")) if old_text else set()
        new_lines = new_text.split("\n")
        return [line for line in new_lines if line.strip() and line not in old_lines]

    def reset(self) -> None:
        self._prev_texts.clear()
