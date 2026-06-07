"""
集成适配器 — 将 GlobalContextManager 接入现有系统

提供：
- sync_memory_context_to_gcm(): 记忆上下文同步
- sync_expert_guidance_to_gcm(): 专家指导同步
- probes_write_to_gcm(): 探针信号写入 GCM
- sync_model_call(): 模型调用后同步到 GCM
- gcm_status_for_api(): 管理 API 的 /context 端点数据
- gcm_health_check(): 快速健康检查
"""
import time
from typing import Dict, List, Optional, Any

from utils.logger import setup_logger
from .types import EventRecord, EventType
from .global_context_pool import GlobalContextPool
from .synchronizer import Synchronizer
from .auditor import Auditor

logger = setup_logger("gcm_wire")


# ========================================================================
# Coordinator 集成
# ========================================================================

def sync_memory_context_to_gcm(
    pool: GlobalContextPool,
    memory_context: List[Dict[str, Any]],
    session_id: str = ""
) -> None:
    """将 coordinator 中的 memory_context 同步到 GCM"""
    if not memory_context:
        return

    for item in memory_context[:20]:
        content = item.get("content", "") if isinstance(item, dict) else str(item)
        role = item.get("role", "memory") if isinstance(item, dict) else "memory"

        event = EventRecord(
            event_type=EventType.MEMORY_CONTEXT,
            source_role=role,
            content=content,
            metadata={"session_id": session_id}
        )
        pool.add_event(event)

    logger.debug("记忆上下文已同步: %d 条", len(memory_context[:20]))


def sync_expert_guidance_to_gcm(
    pool: GlobalContextPool,
    expert_guidance: List[str],
    expert_name: str = "expert"
) -> None:
    """将专家指导同步到 GCM"""
    if not expert_guidance:
        return

    for guidance in expert_guidance[:10]:
        event = EventRecord(
            event_type=EventType.EXPERT_RESULT,
            source_role=expert_name,
            content=str(guidance)
        )
        pool.add_event(event)

    logger.debug("专家指导已同步: %d 条", len(expert_guidance[:10]))


# ========================================================================
# 探针集成
# ========================================================================

def probes_write_to_gcm(
    pool: GlobalContextPool,
    probe_signals: List[Dict[str, Any]],
    probe_name: str = "signal_probe"
) -> None:
    """将探针信号写入 GCM"""
    sync = Synchronizer()
    for signal in probe_signals:
        signal_type = signal.get("type", "unknown")
        data = signal.get("data", signal)
        importance = signal.get("importance", 0.5)
        sync.sync_probe_signal(pool, probe_name, signal_type, data, importance)

    logger.debug("探针信号已写入 GCM: %s (%d 条)", probe_name, len(probe_signals))


# ========================================================================
# 模型调用同步
# ========================================================================

def sync_model_call(
    pool: GlobalContextPool,
    source_role: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
    importance: float = 0.5
) -> EventRecord:
    """模型调用后同步输出到 GCM"""
    sync = Synchronizer()
    return sync.sync_model_output(pool, source_role, content, metadata, importance)


# ========================================================================
# 管理 API 集成
# ========================================================================

def gcm_status_for_api() -> Dict[str, Any]:
    """获取 GCM 状态供管理 API 使用"""
    auditor = Auditor()
    pool = GlobalContextPool()

    stats = auditor.get_stats(pool)

    return {
        "success": True,
        "data": {
            "pool": {
                "files_cached": stats.get("files_cached", 0),
                "events_stored": stats.get("events_stored", 0),
                "active_tasks": stats.get("active_tasks", 0),
                "completed_tasks": stats.get("completed_tasks", 0),
                "sessions": stats.get("sessions", 0),
                "progress": stats.get("progress", 0.0),
            },
            "memory": stats.get("memory", {}),
            "redundancy": {
                "ratio": stats.get("redundancy", {}).get("redundancy_ratio", 0),
                "recommendation": stats.get("redundancy", {}).get("recommendation", ""),
            },
            "consistency": stats.get("consistency", {}),
            "event_types": stats.get("event_type_distribution", {}),
            "warnings_count": stats.get("warnings_count", 0),
        }
    }


def gcm_health_check() -> Dict[str, Any]:
    """快速健康检查"""
    pool = GlobalContextPool()
    auditor = Auditor()

    memory = auditor.check_memory(pool)
    pool_stats = pool.get_stats()

    healthy = True
    issues = []

    if memory.get("warning"):
        healthy = False
        issues.append(memory["warning"])
    if pool_stats.get("events_stored", 0) >= 10000:
        issues.append("事件存储已满")

    return {
        "healthy": healthy,
        "issues": issues,
        "timestamp": time.time()
    }
