"""
告警引擎 - 增强版
"""
import time
import threading
from typing import Dict, Any, List, Optional, Callable
from collections import defaultdict
from utils.logger import setup_logger

from .rules import AlertRule

logger = setup_logger("alert_engine")


class AlertEngine:
    """告警引擎"""

    def __init__(self):
        self._rules: Dict[str, AlertRule] = {}
        self._alerts: List[Dict[str, Any]] = []
        self._max_alerts = 1000
        self._lock = threading.Lock()
        self._callbacks: List[Callable] = []
        self._load_default_rules()

    def _load_default_rules(self):
        """加载默认规则"""
        default_rules = [
            # 系统资源
            AlertRule(
                name="cpu_high",
                metric="system.cpu.percent",
                condition="gt",
                threshold=80,
                severity="warning",
                cooldown=60,
                description="CPU 使用率超过 80%"
            ),
            AlertRule(
                name="cpu_critical",
                metric="system.cpu.percent",
                condition="gt",
                threshold=95,
                severity="critical",
                cooldown=30,
                description="CPU 使用率超过 95%"
            ),
            AlertRule(
                name="memory_high",
                metric="system.memory.percent",
                condition="gt",
                threshold=80,
                severity="warning",
                cooldown=60,
                description="内存使用率超过 80%"
            ),
            AlertRule(
                name="memory_critical",
                metric="system.memory.percent",
                condition="gt",
                threshold=90,
                severity="critical",
                cooldown=30,
                description="内存使用率超过 90%"
            ),
            AlertRule(
                name="disk_high",
                metric="system.disk.percent",
                condition="gt",
                threshold=85,
                severity="warning",
                cooldown=300,
                description="磁盘使用率超过 85%"
            ),

            # 记忆模块
            AlertRule(
                name="memory_save_slow",
                metric="memory.save.latency",
                condition="gt",
                threshold=100,
                severity="warning",
                cooldown=60,
                description="记忆保存延迟超过 100ms"
            ),
            AlertRule(
                name="memory_load_slow",
                metric="memory.load.latency",
                condition="gt",
                threshold=100,
                severity="warning",
                cooldown=60,
                description="记忆加载延迟超过 100ms"
            ),
            AlertRule(
                name="memory_search_slow",
                metric="memory.search.latency",
                condition="gt",
                threshold=200,
                severity="warning",
                cooldown=60,
                description="记忆搜索延迟超过 200ms"
            ),

            # 思维模块
            AlertRule(
                name="thinking_slow",
                metric="thinking.think.latency",
                condition="gt",
                threshold=5000,
                severity="warning",
                cooldown=120,
                description="思考延迟超过 5 秒"
            ),
            AlertRule(
                name="thinking_timeout",
                metric="thinking.think.latency",
                condition="gt",
                threshold=30000,
                severity="critical",
                cooldown=60,
                description="思考超时 30 秒"
            ),

            # 感知模块
            AlertRule(
                name="perception_slow",
                metric="perception.latency",
                condition="gt",
                threshold=500,
                severity="warning",
                cooldown=60,
                description="感知延迟超过 500ms"
            ),
        ]

        for rule in default_rules:
            with self._lock:
                self._rules[rule.name] = rule
        logger.info(f"告警规则加载完成，共 {len(default_rules)} 条: {', '.join(r.name for r in default_rules)}")

    def add_rule(self, rule: AlertRule):
        """添加规则"""
        with self._lock:
            self._rules[rule.name] = rule
            logger.info(f"添加告警规则: {rule.name}")

    def remove_rule(self, name: str):
        """移除规则"""
        with self._lock:
            if name in self._rules:
                del self._rules[name]
                logger.info(f"移除告警规则: {name}")

    def get_rules(self) -> List[Dict[str, Any]]:
        """获取所有规则"""
        with self._lock:
            return [rule.to_dict() for rule in self._rules.values()]

    def evaluate(self, metric_name: str, value: float) -> Optional[Dict[str, Any]]:
        """评估规则"""
        triggered = []

        with self._lock:
            for rule in self._rules.values():
                if rule.metric == metric_name:
                    if rule.evaluate(value):
                        alert = {
                            "id": f"alert_{int(time.time() * 1000)}",
                            "rule_name": rule.name,
                            "metric": metric_name,
                            "value": value,
                            "threshold": rule.threshold,
                            "condition": rule.condition,
                            "severity": rule.severity,
                            "message": rule.description,
                            "timestamp": time.time()
                        }
                        triggered.append(alert)
                        self._add_alert(alert)

                        # 触发回调
                        for callback in self._callbacks:
                            try:
                                callback(alert)
                            except Exception as e:
                                logger.error(f"告警回调失败: {e}")

        return triggered[0] if triggered else None

    def _add_alert(self, alert: Dict[str, Any]):
        """添加告警"""
        with self._lock:
            self._alerts.append(alert)
            if len(self._alerts) > self._max_alerts:
                self._alerts = self._alerts[-self._max_alerts:]

    def get_alerts(
        self,
        severity: str = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """获取告警"""
        with self._lock:
            alerts = self._alerts
            if severity:
                alerts = [a for a in alerts if a.get("severity") == severity]
            return alerts[-limit:]

    def get_alert_summary(self) -> Dict[str, int]:
        """获取告警摘要"""
        with self._lock:
            total = len(self._alerts)
            critical = sum(1 for a in self._alerts if a.get("severity") == "critical")
            warning = sum(1 for a in self._alerts if a.get("severity") == "warning")
            info = sum(1 for a in self._alerts if a.get("severity") == "info")
            return {
                "total": total,
                "critical": critical,
                "warning": warning,
                "info": info
            }

    def clear_alerts(self):
        """清除告警"""
        with self._lock:
            self._alerts.clear()
            logger.info("告警已清除")

    def register_callback(self, callback: Callable):
        """注册告警回调"""
        self._callbacks.append(callback)

    def unregister_callback(self, callback: Callable):
        """注销告警回调"""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def start_monitoring(self, metrics_collector, interval: float = 1.0):
        """启动监控循环"""
        import asyncio

        async def monitor():
            while True:
                try:
                    all_metrics = metrics_collector.get_all()
                    for metric_name, value in all_metrics.items():
                        self.evaluate(metric_name, value)
                except Exception as e:
                    logger.error(f"监控循环错误: {e}")
                await asyncio.sleep(interval)

        return asyncio.create_task(monitor())


alert_engine = AlertEngine()