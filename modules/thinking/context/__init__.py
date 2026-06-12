"""
ThinkingContext — 思考模块上下文管理

核心组件:
- ContextController: 所有上下文注入的单一入口和决策者
- CompressionEngine: 5 级上下文压缩 + 冗余检测
- ContextManager: 构建 LLM prompt 的上下文

已删除组件:
- GlobalContextPool / wire / synchronizer / auditor
  保留 stub 文件兼容旧导入，所有方法返回 None。
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

# 兼容旧导入：这些模块已删除，但保留 stub 文件
from .global_context_pool import gcm_pool, GlobalContextPool  # noqa
from .wire import (  # noqa
    sync_model_call, sync_expert_guidance_to_gcm,
    gcm_status_for_api, gcm_health_check,
)

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
