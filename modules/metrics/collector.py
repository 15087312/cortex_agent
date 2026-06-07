"""
指标收集器 - 统一采集指标数据
"""
import time
import threading
from typing import Dict, Any, List, Optional
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from utils.logger import setup_logger

from .registry import metric_registry, MetricDefinition

logger = setup_logger("metrics_collector")


@dataclass
class MetricValue:
    """指标值"""
    name: str
    value: float
    timestamp: float = field(default_factory=time.time)
    tags: Dict[str, str] = field(default_factory=dict)


class MetricsCollector:
    """指标收集器"""

    def __init__(self):
        self._values: Dict[str, List[MetricValue]] = defaultdict(list)
        self._counters: Dict[str, float] = defaultdict(float)
        self._gauges: Dict[str, float] = defaultdict(float)
        self._histograms: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()
        self._max_values = 1000  # 每指标最大缓存数
        self._registry = metric_registry

    def record_latency(self, operation: str, latency_ms: float, tags: Dict[str, str] = None):
        """记录延迟指标"""
        metric_name = f"{operation}.latency"
        self.record_gauge(metric_name, latency_ms, tags)

    def record_throughput(self, operation: str, count: int = 1):
        """记录吞吐量 (counter)"""
        metric_name = f"{operation}.ops.total"
        with self._lock:
            self._counters[metric_name] += count

    def record_error(self, operation: str, error_type: str):
        """记录错误"""
        with self._lock:
            self._counters["errors.total"] += 1
            self._counters[f"errors.{operation}"] += 1

    def record_gauge(self, metric_name: str, value: float, tags: Dict[str, str] = None):
        """记录 gauge 类型指标"""
        with self._lock:
            self._gauges[metric_name] = value
            self._values[metric_name].append(MetricValue(
                name=metric_name,
                value=value,
                tags=tags or {}
            ))
            self._cleanup(metric_name)

    def record_counter(self, metric_name: str, increment: float = 1):
        """记录 counter 类型指标"""
        with self._lock:
            self._counters[metric_name] += increment
            self._values[metric_name].append(MetricValue(
                name=metric_name,
                value=self._counters[metric_name]
            ))

    def record_histogram(self, metric_name: str, value: float):
        """记录 histogram 类型指标"""
        with self._lock:
            self._histograms[metric_name].append(value)
            values = self._histograms[metric_name]
            if len(values) > 1000:
                # Keep recent 1000 values instead of clearing all
                self._histograms[metric_name] = values[-1000:]
            self._values[metric_name].append(MetricValue(
                name=metric_name,
                value=value
            ))

    def _cleanup(self, metric_name: str):
        """清理过期数据"""
        values = self._values[metric_name]
        if len(values) > self._max_values:
            self._values[metric_name] = values[-self._max_values:]

    def get_latest(self, metric_name: str = None) -> Dict[str, Any]:
        """获取最新指标值"""
        if metric_name:
            with self._lock:
                values = self._values.get(metric_name, [])
                if values:
                    return values[-1].__dict__
            return None

        result = {}
        with self._lock:
            for name, value in self._gauges.items():
                result[name] = value
            for name, value in self._counters.items():
                result[name] = value
        return result

    def get_all(self) -> Dict[str, float]:
        """获取所有当前指标"""
        result = {}
        with self._lock:
            for name, value in self._gauges.items():
                result[name] = value
            for name, value in self._counters.items():
                result[name] = value
        return result

    def get_history(
        self,
        metric_name: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """获取指标历史"""
        values = self._values.get(metric_name, [])
        return [v.__dict__ for v in values[-limit:]]

    def get_histogram_stats(self, metric_name: str) -> Dict[str, float]:
        """获取直方图统计"""
        values = self._histograms.get(metric_name, [])
        if not values:
            return {"count": 0, "avg": 0, "p50": 0, "p95": 0, "p99": 0, "max": 0}

        sorted_values = sorted(values)
        n = len(sorted_values)

        return {
            "count": n,
            "avg": sum(values) / n,
            "p50": sorted_values[int(n * 0.5)],
            "p95": sorted_values[int(n * 0.95)],
            "p99": sorted_values[int(n * 0.99)],
            "max": max(values)
        }

    def record_system_metrics(self):
        """采集系统指标"""
        try:
            import psutil
            import platform

            self.record_gauge("system.cpu.percent", psutil.cpu_percent(interval=0.1))
            self.record_gauge("system.memory.percent", psutil.virtual_memory().percent)
            self.record_gauge("system.disk.percent", psutil.disk_usage('/').percent)
            self.record_gauge("system.uptime", time.time())

        except ImportError:
            pass

    def reset(self):
        """重置所有指标"""
        with self._lock:
            self._values.clear()
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


metrics_collector = MetricsCollector()
metrics_collector.record_system_metrics()


class MetricsExporter:
    """指标导出器"""

    @staticmethod
    def to_prometheus() -> str:
        """导出为 Prometheus 格式"""
        lines = []
        collector = metrics_collector

        for name, value in collector.get_all().items():
            safe_name = name.replace(".", "_")
            lines.append(f"# TYPE {safe_name} gauge")
            lines.append(f"{safe_name} {value}")

        return "\n".join(lines)

    @staticmethod
    def to_json() -> Dict[str, Any]:
        """导出为 JSON 格式"""
        return metrics_collector.get_all()