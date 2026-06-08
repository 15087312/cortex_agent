"""
差异检测器模块 — Stage 1: 全维度持续感知

提供便捷工厂函数：
- get_detector() → DifferenceDetector 单例
- get_heartbeat() → ExistentialHeartbeat 单例
- get_registry() → DifferenceSourceRegistry 单例
"""
from modules.difference_detector.detector import DifferenceDetector, get_detector
from modules.difference_detector.heartbeat import ExistentialHeartbeat, get_heartbeat
from modules.difference_detector.sources.base import DifferenceSourceRegistry


def get_registry() -> DifferenceSourceRegistry:
    """获取差异源注册表单例"""
    return get_detector().registry


__all__ = [
    "DifferenceDetector",
    "ExistentialHeartbeat",
    "DifferenceSourceRegistry",
    "get_detector",
    "get_heartbeat",
    "get_registry",
]
