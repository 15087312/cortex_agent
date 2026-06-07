"""
差异源 ABC + 注册表

完全复用 ExecutorRegistry 模式：
- register(source) / get(source_type) / get_enabled_sources()
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from modules.difference_detector.models import Difference


class DifferenceSource(ABC):
    """差异源抽象基类 — 每个源检测一个维度的差异"""

    def __init__(self):
        self._enabled = True

    @property
    @abstractmethod
    def source_type(self) -> str:
        """返回源类型标识符: "time"|"internal"|"behavioral"|"expectation"|"user_input" """
        ...

    @abstractmethod
    def detect(self) -> List[Difference]:
        """执行检测，返回发现的差异列表"""
        ...

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value


class DifferenceSourceRegistry:
    """差异源注册表 — 完全复用 ExecutorRegistry 模式"""

    def __init__(self):
        self._sources: Dict[str, DifferenceSource] = {}

    def register(self, source: DifferenceSource) -> None:
        self._sources[source.source_type] = source

    def get(self, source_type: str) -> Optional[DifferenceSource]:
        return self._sources.get(source_type)

    def get_enabled_sources(self) -> List[DifferenceSource]:
        return [s for s in self._sources.values() if s.enabled]

    def enable(self, source_type: str) -> bool:
        source = self._sources.get(source_type)
        if source:
            source.enabled = True
            return True
        return False

    def disable(self, source_type: str) -> bool:
        source = self._sources.get(source_type)
        if source:
            source.enabled = False
            return True
        return False

    def list_sources(self) -> List[Dict]:
        return [
            {
                "source_type": s.source_type,
                "enabled": s.enabled,
                "class": type(s).__name__,
            }
            for s in self._sources.values()
        ]

    @property
    def registered_types(self) -> List[str]:
        return list(self._sources.keys())


__all__ = ["DifferenceSource", "DifferenceSourceRegistry"]
