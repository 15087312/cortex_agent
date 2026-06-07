"""
指标采集系统 - 统一指标采集框架

提供:
- 指标收集器接口（MetricsCollectorPort）
- 指标注册表
- 探针装饰器
- 数据导出

模块间交互使用 MetricsCollectorPort Protocol，通过 get_metrics_collector() 工厂函数获取。
"""
from .interface import MetricsCollectorPort, get_metrics_collector
from .collector import MetricsCollector, metrics_collector
from .registry import MetricRegistry, metric_registry
from .probes.base import probe, MetricsProbe

__all__ = [
    # 抽象接口
    "MetricsCollectorPort",
    "get_metrics_collector",
    # 内部实现
    "MetricsCollector",
    "metrics_collector",
    "MetricRegistry",
    "metric_registry",
    "probe",
    "MetricsProbe"
]