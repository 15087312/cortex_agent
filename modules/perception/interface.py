"""感知模块接口门面。"""
from __future__ import annotations

from typing import Any, List, Protocol, runtime_checkable


@runtime_checkable
class PerceptionPort(Protocol):
    """注意力相关感知状态的协议。"""

    @property
    def is_running(self) -> bool:
        """感知收集是否处于活动状态。"""

    def get_attention_items(self, max_age_seconds: float = 10.0) -> List[Any]:
        """返回最近的值得注意的感知项。"""


class PerceptionManagerAdapter:
    """具体感知管理器单例的适配器。"""

    def __init__(self, manager: Any):
        self._manager = manager

    @property
    def is_running(self) -> bool:
        return bool(getattr(self._manager, "_running", False))

    def get_attention_items(self, max_age_seconds: float = 10.0) -> List[Any]:
        return self._manager.get_attention_items(max_age_seconds=max_age_seconds)


def create_perception_port() -> PerceptionPort:
    """创建默认感知端口，延迟导入具体实现。"""
    from modules.perception import perception_manager

    return PerceptionManagerAdapter(perception_manager)


def get_perception_port() -> PerceptionPort:
    """为期望 get_* 命名方式的调用者提供的兼容性别名。"""
    return create_perception_port()
