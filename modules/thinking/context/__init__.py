"""
ThinkingContext — 思考模块上下文管理

核心组件:
- ContextController: 所有上下文注入的单一入口和决策者
- CompressionEngine: 5 级上下文压缩 + 冗余检测
- ContextManager: 构建 LLM prompt 的上下文

已删除组件（保留 stub 兼容旧导入）:
- GlobalContextPool / gcm_pool → 空对象，所有方法无操作
- wire / synchronizer / auditor → 同上
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


# ── GlobalContextPool stub（兼容旧导入）──
class _StubPool:
    """GlobalContextPool 的替代空对象，所有方法无操作"""
    def __getattr__(self, name):
        return lambda *a, **kw: None


gcm_pool = _StubPool()
GlobalContextPool = _StubPool


class _StubWire:
    """wire 模块的替代空对象"""
    @staticmethod
    def sync_model_call(*a, **kw):
        return None

    @staticmethod
    def gcm_status_for_api(*a, **kw):
        return {"status": "removed"}

    @staticmethod
    def gcm_health_check(*a, **kw):
        return {"status": "removed"}


wire = _StubWire()


class _StubSync:
    """synchronizer 替代"""
    def __getattr__(self, name):
        return lambda *a, **kw: None


synchronizer = _StubSync()
Synchronizer = _StubSync


class _StubAuditor:
    """auditor 替代"""
    def __getattr__(self, name):
        return lambda *a, **kw: None


auditor = _StubAuditor()
Auditor = _StubAuditor


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
    # Stub（兼容旧导入）
    "gcm_pool",
    "GlobalContextPool",
    "wire",
    "synchronizer",
    "Synchronizer",
    "auditor",
    "Auditor",
]
