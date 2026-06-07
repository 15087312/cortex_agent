"""
指标系统抽象层 - 模块间交互接口

定义指标收集器的抽象 Protocol，其他模块应通过此接口访问指标功能。
"""
from typing import Protocol, Dict, Any, List, Optional


class MetricsCollectorPort(Protocol):
    """指标收集器接口 - 其他模块应通过此接口使用指标功能"""

    def record_latency(self, operation: str, latency_ms: float, tags: Dict[str, str] = None) -> None:
        """记录延迟指标"""
        ...

    def record_throughput(self, operation: str, count: int = 1) -> None:
        """记录吞吐量 (counter)"""
        ...

    def record_error(self, operation: str, error_type: str) -> None:
        """记录错误"""
        ...

    def record_gauge(self, metric_name: str, value: float, tags: Dict[str, str] = None) -> None:
        """记录 gauge 类型指标"""
        ...

    def record_counter(self, metric_name: str, increment: float = 1) -> None:
        """记录 counter 类型指标"""
        ...

    def record_histogram(self, metric_name: str, value: float) -> None:
        """记录 histogram 类型指标"""
        ...

    def get_all(self) -> Dict[str, float]:
        """获取所有当前指标"""
        ...

    def get_history(self, metric_name: str, limit: int = 100) -> List[Dict[str, Any]]:
        """获取指标历史"""
        ...

    def get_histogram_stats(self, metric_name: str) -> Dict[str, float]:
        """获取直方图统计"""
        ...

    def reset(self) -> None:
        """重置所有指标"""
        ...


def get_metrics_collector() -> MetricsCollectorPort:
    """工厂函数 - 获取指标收集器"""
    from .collector import metrics_collector
    return metrics_collector
