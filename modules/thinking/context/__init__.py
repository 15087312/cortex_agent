"""
ThinkingContext — 思考模块上下文管理

核心组件:
- ContextController: 所有上下文注入的单一入口和决策者
- CompressionEngine: 5 级上下文压缩 + 冗余检测
- ContextManager: 构建 LLM prompt 的上下文
"""
from .types import (
    ModelRole,
    CompressionLevel,
    EventType,
    FileInfo,
    ProjectMetadata,
    GlobalState,
    EventRecord,
    ContextView,
)
from .manager import ContextManager, WorkingContext
from .compression import CompressionEngine, get_compression_engine
from .controller import ContextController, get_context_controller

__all__ = [
    "ContextController",
    "get_context_controller",
    "ContextManager",
    "WorkingContext",
    "CompressionEngine",
    "get_compression_engine",
    "ModelRole",
    "CompressionLevel",
    "EventType",
    "FileInfo",
    "ProjectMetadata",
    "GlobalState",
    "EventRecord",
    "ContextView",
]
