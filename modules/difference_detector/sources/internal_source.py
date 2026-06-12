"""
内部状态差异源 — 检测未完成任务/失败任务（GCM 已移除，当前返回空）

GCM 是此检测器的唯一数据源。GCM 删除后此源无数据可用，
保留空实现以防调用方报错。
"""
import time
from typing import List
from utils.logger import setup_logger
from modules.difference_detector.models import Difference

logger = setup_logger("difference_detector")
INTERNAL_TTL = 60.0


class InternalStateDifferenceSource:
    """内部状态差异源 — 当前无数据源，返回空"""

    def __init__(self, gcm_pool=None):
        super().__init__()
        logger.info("[internal] GCM 已移除，内部状态差异检测已禁用")

    @property
    def source_type(self) -> str:
        return "internal"

    def detect(self) -> List[Difference]:
        return []
