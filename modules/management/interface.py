"""
管理系统抽象层 - 模块间交互接口

定义管理模块对外暴露的关键接口（时序数据库、告警引擎、性能监控、错误报告）。
模块内部结构（recorders、collectors 等）保持私有。
"""
from typing import Protocol, Dict, Any, List, Optional


class ErrorReporterPort(Protocol):
    """错误报告接口 - 统一上报结构化错误"""

    def report(self, error: Exception, **kwargs) -> None:
        ...


class TimeSeriesDBPort(Protocol):

    def query(self, metric_name: str, start_time: float, end_time: float, limit: int = 100) -> List[Dict[str, Any]]:
        """查询时序数据"""
        ...

    def delete(self, metric_name: str, before_timestamp: float) -> int:
        """删除时间戳之前的数据"""
        ...


class AlertEnginePort(Protocol):
    """告警引擎接口 - 告警规则和事件处理"""

    def add_rule(self, rule_name: str, condition: str, threshold: float, action: str) -> None:
        """添加告警规则"""
        ...

    def remove_rule(self, rule_name: str) -> None:
        """删除告警规则"""
        ...

    def trigger_alert(self, alert_name: str, severity: str, message: str, metadata: Dict[str, Any] = None) -> None:
        """触发告警"""
        ...

    def get_active_alerts(self) -> List[Dict[str, Any]]:
        """获取活跃告警"""
        ...


class PerformanceMonitorPort(Protocol):
    """性能监控接口 - 探针心跳、延迟统计、成功率追踪"""

    def start(self) -> None:
        """启动性能监控"""
        ...

    def stop(self) -> None:
        """停止性能监控"""
        ...

    def get_stats(self) -> Dict[str, Any]:
        """获取性能统计"""
        ...


def get_timeseries_db() -> TimeSeriesDBPort:
    """工厂函数 - 获取时序数据库"""
    from .core.timeseries import timeseries_db
    return timeseries_db


def get_alert_engine() -> AlertEnginePort:
    """工厂函数 - 获取告警引擎"""
    from .core.alert import alert_engine
    return alert_engine


def get_error_reporter() -> ErrorReporterPort:
    """工厂函数 - 获取统一错误报告器"""
    from .core.error_reporter import _reporter
    return _reporter


def get_perf_monitor() -> PerformanceMonitorPort:
    """工厂函数 - 获取性能监控器"""
    from .core.perf_monitor import perf_monitor
    return perf_monitor
