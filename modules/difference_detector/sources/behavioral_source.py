"""
行为差异源 — 检测事件速率变化（GCM 已移除，当前返回空）

GCM 是此检测器的唯一数据源。GCM 删除后此源无数据可用，
保留空实现以防调用方报错。后续如有新的事件跟踪系统可重新接入。
"""
import time
import threading
from typing import List
from utils.logger import setup_logger
from modules.difference_detector.models import Difference

logger = setup_logger("difference_detector")
BEHAVIORAL_TTL = 30.0


class BehavioralDifferenceSource:
    """行为差异源 — 当前无数据源，返回空"""

    def __init__(self, gcm_pool=None):
        super().__init__()
        logger.info("[behavioral] GCM 已移除，行为差异检测已禁用")

    @property
    def source_type(self) -> str:
        return "behavioral"

    def detect(self) -> List[Difference]:
        return []
