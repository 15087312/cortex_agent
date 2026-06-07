"""
探针基类和装饰器 - 自动埋点
"""
import time
import functools
import asyncio
from typing import Callable, Any, Optional
from utils.logger import setup_logger

from ..collector import metrics_collector

logger = setup_logger("metrics_probes")


class MetricsProbe:
    """指标探针基类"""

    def __init__(self, module_name: str):
        self.module_name = module_name
        self.collector = metrics_collector

    def record_latency(self, operation: str, latency_ms: float):
        """记录延迟"""
        metric_name = f"{self.module_name}.{operation}.latency"
        self.collector.record_gauge(metric_name, latency_ms)

    def record_throughput(self, operation: str, count: int = 1):
        """记录吞吐量"""
        metric_name = f"{self.module_name}.{operation}.ops"
        self.collector.record_counter(metric_name, count)

    def record_error(self, operation: str, error_type: str):
        """记录错误"""
        self.collector.record_error(f"{self.module_name}.{operation}", error_type)


def probe(
    operation_name: str,
    module: str = "system",
    threshold_ms: float = 1000,
    record_throughput: bool = True,
    record_error: bool = True
):
    """
    探针装饰器 - 自动记录延迟和错误

    Args:
        operation_name: 操作名称 (如 "memory.save", "thinking.think")
        module: 模块名称 (默认 "system")
        threshold_ms: 超时阈值 (毫秒)
        record_throughput: 是否记录吞吐量
        record_error: 是否记录错误

    Usage:
        @probe("memory.save", module="memory", threshold_ms=100)
        async def save_memory(self, data):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                latency_ms = (time.time() - start_time) * 1000

                metric_name = f"{module}.{operation_name}.latency"
                metrics_collector.record_gauge(metric_name, latency_ms)

                if latency_ms > threshold_ms:
                    logger.warning(
                        f"操作 {operation_name} 延迟超标: {latency_ms:.1f}ms > {threshold_ms}ms"
                    )

                if record_throughput:
                    ops_name = f"{module}.{operation_name}.ops"
                    metrics_collector.record_counter(ops_name, 1)

                return result

            except Exception as e:
                if record_error:
                    metrics_collector.record_error(operation_name, type(e).__name__)
                raise

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                latency_ms = (time.time() - start_time) * 1000

                metric_name = f"{module}.{operation_name}.latency"
                metrics_collector.record_gauge(metric_name, latency_ms)

                if latency_ms > threshold_ms:
                    logger.warning(
                        f"操作 {operation_name} 延迟超标: {latency_ms:.1f}ms > {threshold_ms}ms"
                    )

                if record_throughput:
                    ops_name = f"{module}.{operation_name}.ops"
                    metrics_collector.record_counter(ops_name, 1)

                return result

            except Exception as e:
                if record_error:
                    metrics_collector.record_error(operation_name, type(e).__name__)
                raise

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


class ProbeContext:
    """探针上下文 - 手动埋点"""

    def __init__(self, module: str, operation: str):
        self.module = module
        self.operation = operation
        self.start_time = None

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        latency_ms = (time.time() - self.start_time) * 1000
        metric_name = f"{self.module}.{self.operation}.latency"
        metrics_collector.record_gauge(metric_name, latency_ms)

        if exc_type is not None:
            metrics_collector.record_error(self.operation, exc_type.__name__)

    async def __aenter__(self):
        self.start_time = time.time()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        latency_ms = (time.time() - self.start_time) * 1000
        metric_name = f"{self.module}.{self.operation}.latency"
        metrics_collector.record_gauge(metric_name, latency_ms)

        if exc_type is not None:
            metrics_collector.record_error(self.operation, exc_type.__name__)


def probe_context(module: str, operation: str) -> ProbeContext:
    """创建探针上下文"""
    return ProbeContext(module, operation)