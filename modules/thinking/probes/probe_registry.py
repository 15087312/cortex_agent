"""
探针注册器

管理所有探针，提供统一检测接口
支持异步并行检测，主流程不等待探针
"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
import asyncio
import time
import threading

from utils.logger import setup_logger
from .probe_base import Probe, ProbeSignal, ProbePriority


@dataclass
class DetectionResult:
    """检测结果"""
    signals: List[ProbeSignal]
    triggered_count: int
    high_confidence_count: int
    latency_ms: float = 0
    probe_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def get_triggered_signals(self) -> List[ProbeSignal]:
        """获取触发信号"""
        return sorted(
            self.signals,
            key=lambda x: x.priority.value,
            reverse=True
        )

    def get_high_confidence_signals(self, threshold: float = 0.7) -> List[ProbeSignal]:
        """获取高置信度信号"""
        return [s for s in self.signals if s.confidence >= threshold]


class ProbeRegistry:
    """探针注册器 — 线程安全"""

    def __init__(self):
        self.logger = setup_logger("probe_registry")
        self._probes: Dict[str, Probe] = {}
        self._enabled_probes: List[str] = []
        self._async_enabled = True
        self._lock = threading.RLock()  # 保护 _probes / _enabled_probes

    def register(self, probe: Probe) -> None:
        """注册探针（防止重复注册）"""
        with self._lock:
            if probe.name in self._probes:
                self.logger.warning(f"探针已存在，跳过重复注册: {probe.name}")
                return
            self._probes[probe.name] = probe
            if probe.name not in self._enabled_probes:
                self._enabled_probes.append(probe.name)
        self.logger.info(f"注册探针: {probe.name} (优先级: {probe.priority.name})")

    def unregister(self, name: str) -> None:
        """注销探针"""
        with self._lock:
            if name in self._probes:
                del self._probes[name]
            if name in self._enabled_probes:
                self._enabled_probes.remove(name)
        self.logger.info(f"注销探针: {name}")

    def get_probe(self, name: str) -> Optional[Probe]:
        """获取探针"""
        with self._lock:
            return self._probes.get(name)

    def list_probes(self) -> List[str]:
        """列出所有探针"""
        with self._lock:
            return list(self._probes.keys())

    def enable_probe(self, name: str) -> bool:
        """启用探针"""
        with self._lock:
            probe = self._probes.get(name)
            if probe:
                probe.enable()
                if name not in self._enabled_probes:
                    self._enabled_probes.append(name)
                return True
        return False

    def disable_probe(self, name: str) -> bool:
        """禁用探针"""
        with self._lock:
            probe = self._probes.get(name)
            if probe:
                probe.disable()
                if name in self._enabled_probes:
                    self._enabled_probes.remove(name)
                return True
        return False

    def detect_all(
        self,
        outputs: List[Any],
        parallel: bool = True
    ) -> DetectionResult:
        """检测所有探针（同步版本，线程安全）"""
        start_time = time.time()
        all_signals = []
        probe_stats = {}

        with self._lock:
            enabled_names = list(self._enabled_probes)
            probes_snapshot = dict(self._probes)

        if parallel:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            with ThreadPoolExecutor(
                max_workers=min(len(enabled_names), 8)
            ) as executor:
                future_to_name = {}
                for name in enabled_names:
                    probe = probes_snapshot.get(name)
                    if not probe or not probe.is_enabled():
                        continue
                    future = executor.submit(probe.detect, outputs)
                    future_to_name[future] = name

                for future in as_completed(future_to_name):
                    name = future_to_name[future]
                    try:
                        signals = future.result(timeout=10)
                        all_signals.extend(signals)
                        probe_stats[name] = {"success": True, "signals": len(signals)}
                    except Exception as e:
                        self.logger.error(f"探针 {name} 检测失败: {e}")
                        probe_stats[name] = {"success": False, "error": str(e)}
        else:
            for name in enabled_names:
                probe = probes_snapshot.get(name)
                if not probe or not probe.is_enabled():
                    continue
                try:
                    signals = probe.detect(outputs)
                    all_signals.extend(signals)
                    probe_stats[name] = {"success": True, "signals": len(signals)}
                except Exception as e:
                    self.logger.error(f"探针 {name} 检测失败: {e}")
                    probe_stats[name] = {"success": False, "error": str(e)}

        all_signals.sort(key=lambda x: (x.priority.value, x.confidence), reverse=True)

        latency_ms = (time.time() - start_time) * 1000

        return DetectionResult(
            signals=all_signals,
            triggered_count=len(all_signals),
            high_confidence_count=len([s for s in all_signals if s.is_high_confidence()]),
            latency_ms=latency_ms,
            probe_stats=probe_stats
        )

    async def detect_all_async(
        self,
        outputs: List[Any],
        parallel: bool = True
    ) -> DetectionResult:
        """检测所有探针（异步版本，线程安全）"""
        start_time = time.time()
        all_signals = []
        probe_stats = {}

        with self._lock:
            enabled_names = list(self._enabled_probes)
            probes_snapshot = dict(self._probes)

        if parallel:
            tasks = []
            probe_names = []

            for name in enabled_names:
                probe = probes_snapshot.get(name)
                if not probe or not probe.is_enabled():
                    continue
                tasks.append(self._detect_single_async(probe, outputs))
                probe_names.append(name)

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for name, result in zip(probe_names, results):
                if isinstance(result, Exception):
                    self.logger.error(f"探针 {name} 检测失败: {result}")
                    probe_stats[name] = {"success": False, "error": str(result)}
                else:
                    all_signals.extend(result)
                    probe_stats[name] = {"success": True, "signals": len(result)}
        else:
            for name in enabled_names:
                probe = probes_snapshot.get(name)
                if not probe or not probe.is_enabled():
                    continue
                try:
                    signals = await self._detect_single_async(probe, outputs)
                    all_signals.extend(signals)
                    probe_stats[name] = {"success": True, "signals": len(signals)}
                except Exception as e:
                    self.logger.error(f"探针 {name} 检测失败: {e}")
                    probe_stats[name] = {"success": False, "error": str(e)}

        all_signals.sort(key=lambda x: (x.priority.value, x.confidence), reverse=True)

        latency_ms = (time.time() - start_time) * 1000

        return DetectionResult(
            signals=all_signals,
            triggered_count=len(all_signals),
            high_confidence_count=len([s for s in all_signals if s.is_high_confidence()]),
            latency_ms=latency_ms,
            probe_stats=probe_stats
        )

    async def _detect_single_async(self, probe: Probe, outputs: List[Any]) -> List[ProbeSignal]:
        """单个探针的异步检测"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, probe.detect, outputs)

    def get_triggered_units(self, outputs: List[Any]) -> List[str]:
        """获取需要触发的单元列表"""
        result = self.detect_all(outputs)
        units = []
        for signal in result.get_triggered_signals():
            if signal.target not in units:
                units.append(signal.target)
        return units

    def get_summary(self) -> Dict[str, Any]:
        """获取探针摘要"""
        with self._lock:
            probe_details = {}
            for name, probe in self._probes.items():
                probe_details[name] = probe.get_status()
            return {
                "total_probes": len(self._probes),
                "enabled_probes": len(self._enabled_probes),
                "probe_list": [
                    {
                        "name": name,
                        "priority": probe.priority.name,
                        "enabled": probe.is_enabled()
                    }
                    for name, probe in self._probes.items()
                ],
                "probe_details": probe_details
            }


_global_registry: Optional[ProbeRegistry] = None


def get_probe_registry() -> ProbeRegistry:
    """获取全局探针注册器"""
    global _global_registry
    if _global_registry is None:
        _global_registry = ProbeRegistry()
    return _global_registry


def register_probe(probe: Probe) -> None:
    """注册探针到全局注册器"""
    get_probe_registry().register(probe)


def get_triggered_units(outputs: List[Any]) -> List[str]:
    """获取全局触发的单元"""
    return get_probe_registry().get_triggered_units(outputs)
