"""ROI 分发器 — 根据变化区域路由到对应检测器

不同区域不同处理器:
- 通知栏变化 → UI Detector
- 聊天区变化 → OCR Detector
- 游戏 HUD → Motion Detector
- 视频区域 → 忽略
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from utils.logger import setup_logger

logger = setup_logger("perception_roi")


@dataclass
class ROIRegion:
    """ROI 区域定义

    Attributes:
        name: 区域名（如 "notification", "chat_area"）
        rect: (x, y, w, h) 像素坐标
        detector_type: 路由目标 "ocr" / "ui" / "motion" / "ignore"
        priority: 优先级（高优先级先处理）
        overlap_threshold: 与变化区域的重叠面积比例阈值（0-1）
    """
    name: str
    rect: Tuple[int, int, int, int]  # (x, y, w, h)
    detector_type: str
    priority: int = 0
    overlap_threshold: float = 0.1  # 10% 重叠即匹配


class ROIDispatcher:
    """ROI 分发器

    维护一组 ROI 区域定义，当帧差检测到变化区域时，
    将变化区域与 ROI 匹配，路由到对应检测器。

    用法:
        dispatcher = ROIDispatcher()
        dispatcher.register_roi(ROIRegion("notification", (0, 0, 1920, 50), "ui"))
        dispatcher.register_roi(ROIRegion("chat_area", (0, 100, 800, 600), "ocr"))

        result = dispatcher.dispatch(changed_regions, frame)
        # result = {"ocr": [roi_image, ...], "ui": [roi_image, ...]}
    """

    def __init__(self):
        self._rois: Dict[str, ROIRegion] = {}

    def register_roi(self, roi: ROIRegion) -> None:
        """注册 ROI 区域"""
        self._rois[roi.name] = roi
        logger.debug(f"注册 ROI: {roi.name} -> {roi.detector_type} {roi.rect}")

    def unregister_roi(self, name: str) -> bool:
        """移除 ROI 区域"""
        if name in self._rois:
            del self._rois[name]
            return True
        return False

    def get_rois(self) -> List[ROIRegion]:
        """获取所有已注册的 ROI"""
        return sorted(self._rois.values(), key=lambda r: r.priority, reverse=True)

    def dispatch(
        self,
        changed_regions: List[Tuple[int, int, int, int]],
        frame: np.ndarray,
    ) -> Dict[str, List[Tuple[str, np.ndarray]]]:
        """将变化区域分发到对应检测器

        Args:
            changed_regions: 帧差检测到的变化区域 [(x, y, w, h), ...]
            frame: 当前帧

        Returns:
            {detector_type: [(roi_name, roi_image), ...]}
        """
        result: Dict[str, List[Tuple[str, np.ndarray]]] = {}

        if not changed_regions or frame is None or frame.size == 0:
            return result

        frame_h, frame_w = frame.shape[:2]

        for roi in sorted(self._rois.values(), key=lambda r: r.priority, reverse=True):
            if roi.detector_type == "ignore":
                continue

            rx, ry, rw, rh = roi.rect
            # 裁剪 ROI 区域（带边界检查）
            x1 = max(0, rx)
            y1 = max(0, ry)
            x2 = min(frame_w, rx + rw)
            y2 = min(frame_h, ry + rh)

            if x2 <= x1 or y2 <= y1:
                continue

            # 检查是否有变化区域与 ROI 重叠
            roi_has_change = False
            for cx, cy, cw, ch in changed_regions:
                overlap = self._calc_overlap(
                    (cx, cy, cw, ch), (x1, y1, x2 - x1, y2 - y1)
                )
                roi_area = (x2 - x1) * (y2 - y1)
                if roi_area > 0 and (overlap / roi_area) >= roi.overlap_threshold:
                    roi_has_change = True
                    break

            if not roi_has_change:
                continue

            # 裁剪 ROI 图像
            roi_image = frame[y1:y2, x1:x2].copy()
            detector_type = roi.detector_type

            if detector_type not in result:
                result[detector_type] = []
            result[detector_type].append((roi.name, roi_image))

        return result

    def dispatch_full_frame(
        self,
        frame: np.ndarray,
        detector_type: str = "ocr",
    ) -> Dict[str, List[Tuple[str, np.ndarray]]]:
        """全帧分发（无 ROI 时的降级模式）

        整个帧作为一个 ROI 发送到指定检测器。
        """
        if frame is None or frame.size == 0:
            return {}
        return {detector_type: [("_full_frame", frame)]}

    @staticmethod
    def _calc_overlap(
        rect1: Tuple[int, int, int, int],
        rect2: Tuple[int, int, int, int],
    ) -> int:
        """计算两个矩形的重叠面积 (x, y, w, h)"""
        x1, y1, w1, h1 = rect1
        x2, y2, w2, h2 = rect2

        left = max(x1, x2)
        top = max(y1, y2)
        right = min(x1 + w1, x2 + w2)
        bottom = min(y1 + h1, y2 + h2)

        if left < right and top < bottom:
            return (right - left) * (bottom - top)
        return 0

    def clear(self) -> None:
        """清空所有 ROI"""
        self._rois.clear()
