"""帧分发器 — 将完整帧发送到检测器"""
from typing import Dict, List, Tuple

import numpy as np


def dispatch(
    frame: np.ndarray,
    detector_type: str = "ocr",
) -> Dict[str, List[Tuple[str, np.ndarray]]]:
    """将完整帧分发到指定检测器

    Args:
        frame: 当前帧
        detector_type: 目标检测器类型

    Returns:
        {detector_type: [(name, frame)]}
    """
    if frame is None or frame.size == 0:
        return {}
    return {detector_type: [("_full_frame", frame)]}
