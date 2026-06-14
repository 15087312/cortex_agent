"""
时间维度差异源

检测内容：
- 空闲时长：5min+ warning(30), 可配置 alert(50), 30min+ critical(55)
- 定时触发：整点/半点标记
- 超期任务：长期未完成任务

空闲 alert 阈值通过 settings.PROACTIVE_OUTREACH_IDLE_MINUTES 配置。
"""
import time
from typing import List, Optional

from modules.difference_detector.sources.base import DifferenceSource
from modules.difference_detector.models import Difference
from utils.logger import setup_logger

logger = setup_logger("time_source")

IDLE_WARNING_SECONDS = 5 * 60      # 5 分钟
IDLE_CRITICAL_SECONDS = 30 * 60    # 30 分钟
# TTL: 空闲差异在下次有活动时自动溶解
IDLE_TTL = 60 * 60                 # 1 小时


def _get_idle_alert_seconds() -> float:
    """从 settings 读取空闲 alert 阈值（秒）"""
    try:
        from config.settings import settings
        return settings.PROACTIVE_OUTREACH_IDLE_MINUTES * 60
    except Exception as e:
        logger.warning(f"读取空闲alert阈值失败，使用默认值: {e}")
        return 15 * 60  # 默认 15 分钟


class TimeDifferenceSource(DifferenceSource):
    """时间维度差异源"""

    def __init__(self):
        super().__init__()
        self._last_activity: float = time.time()
        self._last_hour_check: int = -1
        self._last_reported_category: str | None = None

    @property
    def source_type(self) -> str:
        return "time"

    def _idle_level(self, idle_duration: float) -> str | None:
        """返回当前空闲级别，None 表示正常"""
        if idle_duration >= IDLE_CRITICAL_SECONDS:
            return "idle_critical"
        if idle_duration >= _get_idle_alert_seconds():
            return "idle_alert"
        if idle_duration >= IDLE_WARNING_SECONDS:
            return "idle_warning"
        return None

    def notify_activity(self) -> None:
        """外部调用：通知有活动发生，重置空闲计时"""
        self._last_activity = time.time()
        self._last_reported_category = None

    def detect(self) -> List[Difference]:
        differences = []
        now = time.time()
        idle_duration = now - self._last_activity

        current_level = self._idle_level(idle_duration)

        # 只在状态发生跃迁时生成差异（避免每秒重复触发）
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
