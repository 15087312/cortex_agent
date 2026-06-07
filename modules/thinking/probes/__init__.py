"""
探针模块

提供探针基类、注册器和各类探针实现
"""
from .probe_base import Probe, ProbeSignal, ProbePriority
from .probe_registry import ProbeRegistry, DetectionResult, get_probe_registry, register_probe, get_triggered_units
from .probe_cache import ProbeCache, get_probe_cache
from .templates import ProbeTemplate, MANAGER_PROBE_TEMPLATES, EXPERT_PROBE_TEMPLATES
from .concrete_probes import (
    SafetyProbe, CodeProbe, DeepAnalysisProbe, SearchProbe,
    register_concrete_probes,
)

# 自动注册具体探针（幂等，重复调用安全）
register_concrete_probes()

__all__ = [
    # 基类
    "Probe",
    "ProbeSignal",
    "ProbePriority",
    # 注册器
    "ProbeRegistry",
    "DetectionResult",
    "get_probe_registry",
    "register_probe",
    "get_triggered_units",
    # 探针缓存
    "ProbeCache",
    "get_probe_cache",
    # 模板
    "ProbeTemplate",
    "MANAGER_PROBE_TEMPLATES",
    "EXPERT_PROBE_TEMPLATES",
    # 具体探针
    "SafetyProbe",
    "CodeProbe",
    "DeepAnalysisProbe",
    "SearchProbe",
    "register_concrete_probes",
]
