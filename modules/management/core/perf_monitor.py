"""
性能监控器 — 探针心跳 + 延迟统计 + 成功率追踪

替代旧的 modules/resource/resource_manager.py，
集成到管理模块已有的 TimeSeriesDB + AlertEngine 基础设施。

用法：
    from modules.management.core.perf_monitor import perf_monitor

    perf_monitor.heartbeat("thinking_engine", latency_ms=150, success=True)
    stats = perf_monitor.get_probe("thinking_engine")
"""
import time
import threading
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

from utils.logger import setup_logger

logger = setup_logger("perf_monitor")


@dataclass
class ProbeStats:
    """单个探针的运行统计"""
    name: str
    calls: int = 0
    errors: int = 0
    total_latency_ms: float = 0.0
    last_latency_ms: float = 0.0
    min_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    last_success: bool = True
    last_error: str = ""
    last_heartbeat: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def avg_latency_ms(self) -> float:
        if self.calls == 0:
            return 0.0
        return self.total_latency_ms / self.calls

    @property
    def success_rate(self) -> float:
        if self.calls == 0:
            return 1.0
        return (self.calls - self.errors) / self.calls

    @property
    def status(self) -> str:
        if self.calls == 0:
            return "inactive"
        if not self.last_success:
            return "error"
        if self.avg_latency_ms > 5000:
            return "slow"
        return "active"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "calls": self.calls,
            "errors": self.errors,
            "success_rate": round(self.success_rate, 3),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "min_latency_ms": round(self.min_latency_ms, 1),
            "max_latency_ms": round(self.max_latency_ms, 1),
            "last_latency_ms": round(self.last_latency_ms, 1),
            "last_success": self.last_success,
            "last_error": self.last_error[:100] if self.last_error else "",
            "last_heartbeat": self.last_heartbeat,
            "metadata": self.metadata,
        }


class PerformanceMonitor:
    """性能监控器 — 管理探针注册、心跳采集、统计查询"""

    def __init__(self, timeseries_db=None, alert_engine=None):
        self._probes: Dict[str, ProbeStats] = {}
        self._lock = threading.Lock()
        self._timeseries_db = timeseries_db
        self._alert_engine = alert_engine

    def set_timeseries_db(self, db):
        """延迟注入 TimeSeriesDB"""
        self._timeseries_db = db

    def set_alert_engine(self, engine):
        """延迟注入 AlertEngine"""
        self._alert_engine = engine

    def register_probe(self, name: str, metadata: Dict[str, Any] = None):
        """注册一个探针（重复注册会更新 metadata 但保留已有统计）"""
        with self._lock:
            if name in self._probes:
                if metadata:
                    self._probes[name].metadata.update(metadata)
                return
            self._probes[name] = ProbeStats(
                name=name,
                metadata=metadata or {},
                last_heartbeat=time.time(),
            )
            logger.debug(f"探针已注册: {name}")

    def heartbeat(
        self,
        probe_name: str,
        latency_ms: float = 0.0,
        success: bool = True,
        metadata: Dict[str, Any] = None,
    ):
        """记录一次探针心跳

        Args:
            probe_name: 探针名称（自动注册不存在探针）
            latency_ms: 本次操作耗时(ms)
            success: 是否成功
            metadata: 额外的统计元数据
        """
        now = time.time()

        with self._lock:
            probe = self._probes.get(probe_name)
            if probe is None:
                probe = ProbeStats(name=probe_name)
                self._probes[probe_name] = probe

            probe.calls += 1
            if not success:
                probe.errors += 1
            probe.total_latency_ms += latency_ms
            probe.last_latency_ms = latency_ms
            probe.last_success = success
            probe.last_heartbeat = now

            if latency_ms > 0:
                if probe.min_latency_ms == 0 or latency_ms < probe.min_latency_ms:
                    probe.min_latency_ms = latency_ms
                if latency_ms > probe.max_latency_ms:
                    probe.max_latency_ms = latency_ms

            if metadata:
                probe.metadata.update(metadata)

        # 写入时序数据库（异步不阻塞）
        if self._timeseries_db and latency_ms > 0:
            try:
                self._timeseries_db.write(
                    metric_name=f"probe.{probe_name}.latency",
                    value=latency_ms,
                    tags={"success": str(success)},
                    module=probe_name,
                )
            except Exception as e:
                logger.debug(f"时序写入失败: {e}")

        # 评估告警规则
        if self._alert_engine and latency_ms > 0:
            try:
                self._alert_engine.evaluate(
                    f"probe.{probe_name}.latency", latency_ms
                )
            except Exception as e:
                logger.debug(f"告警评估失败: {e}")

    def get_probe(self, name: str) -> Optional[ProbeStats]:
        """获取单个探针状态"""
        with self._lock:
            return self._probes.get(name)

    def get_all_probes(self) -> List[Dict[str, Any]]:
        """获取所有探针状态"""
        with self._lock:
            return [p.to_dict() for p in self._probes.values()]

    def get_probe_summary(self) -> Dict[str, Any]:
        """获取探针汇总"""
        with self._lock:
            total = len(self._probes)
            active = sum(1 for p in self._probes.values() if p.status == "active")
            error_count = sum(1 for p in self._probes.values() if p.status == "error")
            slow_count = sum(1 for p in self._probes.values() if p.status == "slow")

            total_calls = sum(p.calls for p in self._probes.values())
            total_errors = sum(p.errors for p in self._probes.values())
            avg_latency = (
                sum(p.avg_latency_ms for p in self._probes.values()) / max(total, 1)
            )

            return {
                "total_probes": total,
                "active": active,
                "error": error_count,
                "slow": slow_count,
                "total_calls": total_calls,
                "total_errors": total_errors,
                "overall_success_rate": round(
                    (total_calls - total_errors) / max(total_calls, 1), 3
                ),
                "avg_latency_ms": round(avg_latency, 1),
            }

    def reset_probe(self, name: str):
        """重置单个探针统计"""
        with self._lock:
            if name in self._probes:
                meta = self._probes[name].metadata
                self._probes[name] = ProbeStats(name=name, metadata=meta)

    def reset_all(self):
        """重置所有探针统计"""
        with self._lock:
            for name, probe in self._probes.items():
                meta = probe.metadata
                self._probes[name] = ProbeStats(name=name, metadata=meta)

    def unregister_probe(self, name: str):
        """移除探针"""
        with self._lock:
            self._probes.pop(name, None)


# 全局单例
perf_monitor = PerformanceMonitor()


def init_perf_monitor(timeseries_db=None, alert_engine=None):
    """初始化性能监控器（注入依赖）"""
    if timeseries_db:
        perf_monitor.set_timeseries_db(timeseries_db)
    if alert_engine:
        perf_monitor.set_alert_engine(alert_engine)
    logger.info("性能监控器已初始化")
    return perf_monitor
