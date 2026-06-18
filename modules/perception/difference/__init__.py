"""差异检测包 — 导出 DifferenceDetector、ExistentialHeartbeat 等核心组件"""
from modules.perception.difference.detector import DifferenceDetector, get_detector
from modules.perception.difference.heartbeat import ExistentialHeartbeat, get_heartbeat
from modules.perception.difference.sources.base import DifferenceSourceRegistry
from modules.perception.difference.sources.mcp_screen_source import ScreenDiffSource, get_screen_diff_source


def get_registry() -> DifferenceSourceRegistry:
    return get_detector().registry


__all__ = [
    "DifferenceDetector",
    "ExistentialHeartbeat",
    "ScreenDiffSource",
    "DifferenceSourceRegistry",
    "get_detector",
    "get_heartbeat",
    "get_screen_diff_source",
    "get_registry",
]
