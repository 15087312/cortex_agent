"""
ThinkingContext — 思考模块上下文管理

核心组件:
- GlobalContextPool: 所有数据的唯一存储地（文件、状态、事件日志）
- CompressionEngine: 5 级上下文压缩 + 冗余检测
- Synchronizer: 文件监听 + 模型/工具/探针输出同步
- Auditor: 健康监控（冗余/内存/一致性）
- ContextManager: 构建 LLM prompt 的上下文
- wire: 集成适配器（将 GCM 注入现有系统）
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
from .global_context_pool import GlobalContextPool, gcm_pool
from .compression import CompressionEngine
from .synchronizer import Synchronizer, synchronizer
from .auditor import Auditor, auditor
from . import wire

__all__ = [
    "GlobalContextPool",
    "ContextManager",
    "WorkingContext",
    "CompressionEngine",
    "Synchronizer",
    "Auditor",
    "ModelRole",
    "CompressionLevel",
    "EventType",
    "FileInfo",
    "ProjectMetadata",
    "GlobalState",
    "EventRecord",
    "ContextView",
    "gcm_pool",
    "synchronizer",
    "auditor",
    "wire",
]
