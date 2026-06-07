"""
告警规则定义
"""
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from utils.logger import setup_logger

logger = setup_logger("alert_rules")


@dataclass
class AlertRule:
    """告警规则"""
    name: str
    metric: str
    condition: str  # gt, lt, eq, gte, lte, range
    threshold: float
    severity: str  # critical, warning, info
    cooldown: int = 60  # 秒
    enabled: bool = True
    description: str = ""
    last_triggered: float = 0

    def evaluate(self, value: float) -> bool:
        """评估条件"""
        if not self.enabled:
            return False

        # 冷却检查
        if time.time() - self.last_triggered < self.cooldown:
            return False

        if self.condition == "gt":
            result = value > self.threshold
        elif self.condition == "lt":
            result = value < self.threshold
        elif self.condition == "eq":
            result = value == self.threshold
        elif self.condition == "gte":
            result = value >= self.threshold
        elif self.condition == "lte":
            result = value <= self.threshold
        elif self.condition == "range":
            result = abs(value) < self.threshold
        else:
            result = False

        if result:
            self.last_triggered = time.time()

        return result

    def to_dict(self) -> Dict[str, Any]:
        """转字典"""
        return {
            "name": self.name,
            "metric": self.metric,
            "condition": self.condition,
            "threshold": self.threshold,
            "severity": self.severity,
            "cooldown": self.cooldown,
            "enabled": self.enabled,
            "description": self.description,
            "last_triggered": self.last_triggered
        }