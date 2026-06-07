"""
自我进化系统（新版本）

- 价值观系统：核心行为准则
  (新的SecurityMonitor + DifferenceDetector 负责检测和进化)
"""
from .value_system import ValueSystem, value_system

__all__ = [
    "ValueSystem",
    "value_system",
]
