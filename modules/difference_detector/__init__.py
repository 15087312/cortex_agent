"""
差异检测器模块 — Stage 1: 全维度持续感知

提供便捷工厂函数：
- get_detector() → DifferenceDetector 单例
- get_heartbeat() → ExistentialHeartbeat 单例
- get_registry() → DifferenceSourceRegistry 单例
"""
from modules.difference_detector.detector import DifferenceDetector
from modules.difference_detector.heartbeat import ExistentialHeartbeat
from modules.difference_detector.sources.base import DifferenceSourceRegistry


def get_detector() -> DifferenceDetector:
    """获取差异检测器单例"""
    return DifferenceDetector()


def get_heartbeat() -> ExistentialHeartbeat:
    """获取存在心跳单例"""
    return ExistentialHeartbeat()


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
