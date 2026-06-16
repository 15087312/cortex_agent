"""感知模块接口门面 — 定义 PerceptionPort 协议和适配器

跨模块调用优先依赖此接口，降低耦合。
"""
from __future__ import annotations

from typing import Any, List, Protocol, runtime_checkable


@runtime_checkable
class PerceptionPort(Protocol):
    """感知端口协议

    定义跨模块调用的最小接口：
    - is_running: 感知是否运行中
    - get_attention_items: 获取值得注意的感知项
    """

    @property
    def is_running(self) -> bool:
        """感知收集是否处于活动状态。"""

    def get_attention_items(self, max_age_seconds: float = 10.0) -> List[Any]:
        """返回最近的值得注意的感知项。"""


class PerceptionManagerAdapter:
    """PerceptionPort 协议的适配器实现

    将旧版 perception_manager 包装为 PerceptionPort 接口。
    """

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
