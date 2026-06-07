"""
探针模块 - 自动埋点探针
"""
from .base import probe, MetricsProbe, probe_context

__all__ = ["probe", "MetricsProbe", "probe_context"]