"""UI 检测器 — 模板匹配

Phase 1: OpenCV 模板匹配（红点、图标、按钮）
Phase 2: YOLOv8n（预留）
"""
import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from modules.perception.detectors.base import PerceptionDetector
from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from utils.logger import setup_logger

logger = setup_logger("perception_ui_detector")

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


class UIDetector(PerceptionDetector):
    """UI 元素检测器

    使用 OpenCV 模板匹配检测屏幕上的 UI 元素（红点、图标、按钮等）。

    用法:
        detector = UIDetector()
        detector.load_template("notification_dot", "templates/dot.png")
        events = detector.detect(roi_image, "notification_area")
    """

    def __init__(self, match_threshold: float = 0.8):
        self._templates: Dict[str, Tuple[np.ndarray, str]] = {}
        # {name: (template_image, detector_event_subtype)}
        self._match_threshold = match_threshold
        self._prev_matches: Dict[str, set] = {}  # roi_name → matched names

    def is_available(self) -> bool:
        return HAS_CV2

    @property
    def detector_type(self) -> str:
        return "ui"

    def load_template(
        self,
        name: str,
        template_path: str,
        event_subtype: str = "element_detected",
    ) -> bool:
        """加载模板图像

        Args:
            name: 模板名
            template_path: 模板图像路径
            event_subtype: 匹配时产出的事件子类型

        Returns:
            是否加载成功
        """
        if not HAS_CV2:
            return False
        if not os.path.exists(template_path):
            logger.warning(f"模板不存在: {template_path}")
            return False
        try:
            img = cv2.imread(template_path, cv2.IMREAD_COLOR)
            if img is None:
                return False
            self._templates[name] = (img, event_subtype)
            logger.debug(f"加载模板: {name} ({img.shape})")
            return True
        except Exception as e:
            logger.warning(f"加载模板失败: {name} {e}")
            return False

    def detect(
        self,
        roi_image: np.ndarray,
        roi_name: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[PerceptionEvent]:
        """检测 ROI 中的 UI 元素"""
        if not HAS_CV2 or not self._templates or roi_image is None:
            return []

        events = []
        current_matches = set()

        for name, (template, subtype) in self._templates.items():
            try:
                # 模板不能大于 ROI 图像
                if (template.shape[0] > roi_image.shape[0] or
                        template.shape[1] > roi_image.shape[1]):
                    continue
                result = cv2.matchTemplate(roi_image, template, cv2.TM_CCOEFF_NORMED)
                locations = np.where(result >= self._match_threshold)

                if len(locations[0]) > 0:
                    current_matches.add(name)
                    # 只在新出现时触发事件
                    prev = self._prev_matches.get(roi_name, set())
                    if name not in prev:
                        events.append(PerceptionEvent(
                            event_type=PerceptionEventType.SCREEN_UI,
                            source="ui",
                            importance=0.7,
                            roi_name=roi_name,
                            payload={
                                "subtype": subtype,
                                "template_name": name,
                                "match_count": len(locations[0]),
                            },
                        ))
            except Exception as e:
                logger.debug(f"模板匹配异常: {name} {e}")

        self._prev_matches[roi_name] = current_matches
        return events

    def reset(self) -> None:
        self._prev_matches.clear()
