"""差异源抽象基类 + 注册表

差异源（DifferenceSource）是差异的"生产者"，
每个源负责检测某一类差异。
注册表（DifferenceSourceRegistry）管理所有已注册的源。
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from modules.perception.difference.models import Difference


class DifferenceSource(ABC):
    """差异源抽象基类

    子类实现 detect() 方法，返回检测到的差异列表。
    每个源有一个唯一的 source_type 标识。
    """

    def __init__(self):
        self._enabled = True

    @property
    @abstractmethod
    def source_type(self) -> str:
        ...

    @abstractmethod
    def detect(self) -> List[Difference]:
        ...

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value


class DifferenceSourceRegistry:
    """差异源注册表

    管理所有已注册的差异源，支持按类型启用/禁用。
    """

    def __init__(self):
        self._sources: Dict[str, DifferenceSource] = {}

    def register(self, source: DifferenceSource) -> None:
        """注册一个差异源"""
        self._sources[source.source_type] = source

    def get(self, source_type: str) -> Optional[DifferenceSource]:
        """根据类型获取差异源"""
        return self._sources.get(source_type)

    def get_enabled_sources(self) -> List[DifferenceSource]:
        """获取所有已启用的差异源"""
        return [s for s in self._sources.values() if s.enabled]

    def enable(self, source_type: str) -> bool:
        """启用指定差异源"""
        source = self._sources.get(source_type)
        if source:
            source.enabled = True
            return True
        return False

    def disable(self, source_type: str) -> bool:
        """禁用指定差异源"""
        source = self._sources.get(source_type)
        if source:
            source.enabled = False
            return True
        return False

    def list_sources(self) -> List[Dict]:
        """列出所有注册的差异源及状态"""
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
        """已注册的差异源类型列表"""
        return list(self._sources.keys())


__all__ = ["DifferenceSource", "DifferenceSourceRegistry"]
