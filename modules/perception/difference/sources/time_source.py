"""时间差异源 — 基于空闲时间的差异检测

根据用户最后一次活动时间，产生空闲警告/提醒/临界等差异。
空闲分钟数通过 config.settings 动态配置。
"""
import time
from typing import List, Optional

from modules.perception.difference.sources.base import DifferenceSource
from modules.perception.difference.models import Difference
from utils.logger import setup_logger

logger = setup_logger("time_source")

# 空闲阈值：5 分钟警告、30 分钟临界
IDLE_WARNING_SECONDS = 5 * 60
IDLE_CRITICAL_SECONDS = 30 * 60
# 空闲差异 TTL：1 小时
IDLE_TTL = 60 * 60


def _get_idle_alert_seconds() -> float:
    """从配置读取空闲提醒阈值（秒）

    如果配置不可用（测试环境 / 配置未加载），使用 15 分钟默认值。
    """
    try:
        from config.settings import settings
        return settings.PROACTIVE_OUTREACH_IDLE_MINUTES * 60
    except Exception as e:
        logger.warning(f"读取空闲alert阈值失败，使用默认值: {e}")
        return 15 * 60


class TimeDifferenceSource(DifferenceSource):
    def __init__(self):
        super().__init__()
        self._last_activity: float = time.time()
        self._last_hour_check: int = -1
        self._last_reported_category: str | None = None

    @property
    def source_type(self) -> str:
        return "time"

    def _idle_level(self, idle_duration: float) -> str | None:
        """判断当前空闲级别

        优先级: idle_critical > idle_alert > idle_warning
        只返回最高级别，不同时触发多个。
        """
        if idle_duration >= IDLE_CRITICAL_SECONDS:
            return "idle_critical"
        if idle_duration >= _get_idle_alert_seconds():
            return "idle_alert"
        if idle_duration >= IDLE_WARNING_SECONDS:
            return "idle_warning"
        return None

    def notify_activity(self) -> None:
        """用户有活动时重置，避免重复触发同级别空闲事件"""
        self._last_activity = time.time()
        self._last_reported_category = None

    def detect(self) -> List[Difference]:
        """检测空闲状态变化，只在级别变化时才产生差异

        使用 _last_reported_category 跟踪上一轮报告的级别，
        避免每轮心跳重复产生同一级别的空闲差异。
        """
        differences = []
        now = time.time()
        idle_duration = now - self._last_activity

        current_level = self._idle_level(idle_duration)

        if current_level != self._last_reported_category:
            self._last_reported_category = current_level
            if current_level is not None:
                intensity_map = {
                    "idle_critical": 55.0,
                    "idle_alert": 50.0,
                    "idle_warning": 30.0,
                }
                threshold_map = {
                    "idle_critical": IDLE_CRITICAL_SECONDS,
                    "idle_alert": _get_idle_alert_seconds(),
                    "idle_warning": IDLE_WARNING_SECONDS,
                }
                differences.append(Difference(
                    source_type="time",
                    category=current_level,
                    intensity=intensity_map[current_level],
                    ttl=IDLE_TTL,
                    payload={
                        "idle_seconds": round(idle_duration, 1),
                        "idle_minutes": round(idle_duration / 60, 1),
                        "threshold": threshold_map[current_level],
                    },
                ))

        return differences

    @property
    def idle_seconds(self) -> float:
        return time.time() - self._last_activity
