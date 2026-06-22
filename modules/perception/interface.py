"""感知模块接口门面 — 定义 PerceptionPort 协议"""
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
