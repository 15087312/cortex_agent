"""帧差检测器 — 判断两帧之间是否有显著变化

核心原则: Cheap First, Expensive Last
绝大多数帧不进入后续流程，只有有变化的帧才提取 ROI。
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from utils.logger import setup_logger

logger = setup_logger("perception_frame_diff")

# 尝试导入 OpenCV，降级到 numpy
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False
    logger.warning("OpenCV 未安装，帧差检测使用 numpy 降级模式")


@dataclass
class FrameDiffResult:
    """帧差检测结果"""
    has_changed: bool = False                # 是否有显著变化
    change_ratio: float = 0.0                # 变化面积比例 0.0-1.0
    changed_regions: List[Tuple[int, int, int, int]] = field(default_factory=list)
    # changed_regions: [(x, y, w, h), ...] 变化区域
    diff_mask: Optional[np.ndarray] = None   # 差分掩码（二值化）


class FrameDiffDetector:
    """帧差检测器

    通过比较相邻两帧的像素差异，判断是否有显著变化。
    变化面积低于阈值的帧被视为噪声（鼠标光标、闪烁等）。

    Args:
        threshold: 二值化阈值 (0-255)，默认 25
        change_area_threshold: 变化面积比例阈值，默认 0.01 (1%)
        min_region_area: 最小变化区域面积（像素），默认 200
        blur_kernel: 高斯模糊核大小（奇数），默认 5
    """

    def __init__(
        self,
        threshold: int = 25,
        change_area_threshold: float = 0.01,
        min_region_area: int = 200,
        blur_kernel: int = 5,
    ):
        self._threshold = threshold
        self._change_area_threshold = change_area_threshold
        self._min_region_area = min_region_area
        self._blur_kernel = blur_kernel
        self._prev_frame_gray: Optional[np.ndarray] = None

    def detect(self, frame: np.ndarray) -> FrameDiffResult:
        """检测当前帧与上一帧的差异

        Args:
            frame: 当前帧 (BGR 或灰度)

        Returns:
            FrameDiffResult
        """
        # 转灰度
        if frame is None or frame.size == 0:
            return FrameDiffResult()

        if len(frame.shape) == 3:
            if HAS_CV2:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            else:
                # numpy 降级: 取绿色通道近似灰度
                gray = frame[:, :, 1].copy()
        else:
            gray = frame

        # 首帧，无上一帧
        if self._prev_frame_gray is None:
            self._prev_frame_gray = gray
            return FrameDiffResult(has_changed=True, change_ratio=1.0)

        # 尺寸不匹配
        if gray.shape != self._prev_frame_gray.shape:
            self._prev_frame_gray = gray
            return FrameDiffResult(has_changed=True, change_ratio=1.0)

        # 计算差分
        if HAS_CV2:
            # 高斯模糊去噪
            blurred_curr = cv2.GaussianBlur(gray, (self._blur_kernel, self._blur_kernel), 0)
            blurred_prev = cv2.GaussianBlur(
                self._prev_frame_gray, (self._blur_kernel, self._blur_kernel), 0
            )
            diff = cv2.absdiff(blurred_curr, blurred_prev)
            _, thresh = cv2.threshold(diff, self._threshold, 255, cv2.THRESH_BINARY)
            # 形态学操作去噪
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
            thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        else:
            diff = np.abs(gray.astype(np.int16) - self._prev_frame_gray.astype(np.int16))
            thresh = (diff > self._threshold).astype(np.uint8) * 255

        # 计算变化面积比例
        total_pixels = thresh.shape[0] * thresh.shape[1]
        changed_pixels = np.count_nonzero(thresh)
        change_ratio = changed_pixels / total_pixels if total_pixels > 0 else 0.0

        # 更新上一帧
        self._prev_frame_gray = gray

        # 低于阈值，视为噪声
        if change_ratio < self._change_area_threshold:
            return FrameDiffResult(has_changed=False, change_ratio=change_ratio)

        # 提取变化区域
        regions = self._find_regions(thresh)

        return FrameDiffResult(
            has_changed=True,
            change_ratio=change_ratio,
            changed_regions=regions,
            diff_mask=thresh,
        )

    def _find_regions(self, mask: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """从掩码中提取变化区域"""
        if HAS_CV2:
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            regions = []
            for contour in contours:
                area = cv2.contourArea(contour)
                if area >= self._min_region_area:
                    x, y, w, h = cv2.boundingRect(contour)
                    regions.append((x, y, w, h))
            return regions
        else:
            # numpy 降级: 简单的连通区域检测
            # 只返回一个包含所有变化的大区域
            ys, xs = np.where(mask > 0)
            if len(ys) == 0:
                return []
            x_min, x_max = int(xs.min()), int(xs.max())
            y_min, y_max = int(ys.min()), int(ys.max())
            w, h = x_max - x_min, y_max - y_min
            if w * h >= self._min_region_area:
                return [(x_min, y_min, w, h)]
            return []

    def reset(self) -> None:
        """重置（清除上一帧缓存）"""
        self._prev_frame_gray = None
