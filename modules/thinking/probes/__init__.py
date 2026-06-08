"""
探针模块

提供探针工具（probe_start/stop/list）、探针缓存和模板。
自动检测探针系统（ProbeRegistry/concrete_probes）已移除 — 仅保留模型主动触发机制。
"""
from .probe_cache import ProbeCache, get_probe_cache
from .templates import ProbeTemplate, MANAGER_PROBE_TEMPLATES, EXPERT_PROBE_TEMPLATES

__all__ = [
    # 探针缓存
    "ProbeCache",
    "get_probe_cache",
    # 模板
    "ProbeTemplate",
    "MANAGER_PROBE_TEMPLATES",
    "EXPERT_PROBE_TEMPLATES",
]
