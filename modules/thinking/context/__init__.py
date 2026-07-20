"""
ThinkingContext — 思考模块上下文管理

核心组件:
- TurnContext: 单轮上下文池 + 生命周期状态机
- ContextController: 上下文路由、去重、压缩
- CompressionEngine: 5 级上下文压缩 + 冗余检测
- ContextManager: 运行时格式化（外部引导、委托状态）
"""
from .types import (
    CompressionLevel,
    EventType,
    EventRecord,
)
from .pool import TurnContext, TurnState, ContextFragment
from .manager import ContextManager
from .compression import CompressionEngine, get_compression_engine
from .controller import ContextController, get_context_controller

__all__ = [
    "TurnContext",
    "TurnState",
    "ContextFragment",
    "ContextController",
    "get_context_controller",
    "ContextManager",
    "CompressionEngine",
    "get_compression_engine",
    "CompressionLevel",
    "EventType",
    "EventRecord",
]
