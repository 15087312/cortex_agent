"""感知检测器抽象基类

所有检测器（OCR、UI、Window）实现此接口。
检测器只负责: ROI 图像 → PerceptionEvent，不做其他事。
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import numpy as np

from modules.perception.events.types import PerceptionEvent


class PerceptionDetector(ABC):
    """感知检测器抽象基类

    子类实现 detect() 方法，接收 ROI 图像，产出 PerceptionEvent 列表。
    """

    @property
    @abstractmethod
    def detector_type(self) -> str:
        """检测器类型标识: "ocr" / "ui" / "window" / "motion" """

    @abstractmethod
    def detect(
        self,
        roi_image: np.ndarray,
        roi_name: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[PerceptionEvent]:
        """检测 ROI 图像中的变化

        Args:
            roi_image: ROI 区域图像 (BGR numpy array)
            roi_name: ROI 区域名
            context: 可选上下文（如上一帧的 OCR 文本）

        Returns:
            检测到的事件列表
        """

    @abstractmethod
    def is_available(self) -> bool:
        """检测器是否可用（依赖是否安装）"""

    def reset(self) -> None:
        """重置检测器状态（可选实现）"""
        pass
