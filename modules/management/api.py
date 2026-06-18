"""
管理 API - 统一控制中心 (Dashboard & Control Center)

为前端控制面板提供完整的数据接口，聚合所有核心模块状态。

API 端点：
1. /dashboard - 仪表盘总览
2. /system - 系统信息
3. /modules - 所有模块状态
4. /modules/{name} - 单个模块详情
5. /modules/{name}/action - 模块操作
6. /memory - 记忆模块
7. /perception - 感知模块
8. /tool-skills - 工具熟练度
9. /database - 数据库状态
10. /resources - 资源状态
"""
from fastapi import APIRouter, Query, Path, Depends
from datetime import datetime
from pathlib import Path as FilePath
import sqlite3
import os

PROJECT_ROOT = FilePath(__file__).resolve().parents[2]
import time

from api.errors import AppError, ErrorCode
from modules.management.core.collector import ModuleRegistry, StatusCollector
from utils.logger import setup_logger

logger = setup_logger("management_api")

# 统一认证：使用 X-API-Key
from api.auth import require_api_key

router = APIRouter(prefix="/management", tags=["管理控制台"], dependencies=[Depends(require_api_key)])

# 全局实例
_registry = ModuleRegistry()
_collector = StatusCollector(_registry)


# ==============================================================================
# 1. 仪表盘总览 (Dashboard Overview)
# ==============================================================================

@router.get("/dashboard")
async def get_dashboard():
    """
    获取仪表盘核心数据
    """
    module_statuses = _collector.collect_all()
    
    healthy_count = sum(1 for m in module_statuses.values() if m.get("status") == "healthy")
    total_count = len(module_statuses)
    
    return {
        "success": True,
        "data": {
            "timestamp": datetime.now().isoformat(),
            "health": {
                "healthy_modules": healthy_count,
                "total_modules": total_count,
                "health_percent": round(healthy_count / total_count * 100) if total_count > 0 else 100
            },
            "modules": {
                name: info.get("status", "unknown")
                for name, info in module_statuses.items()
            }
        }
    }


# ==============================================================================
# 3. 模块管理 (Module Management)
# ==============================================================================

@router.get("/modules")
async def get_all_modules():
    """获取所有模块列表"""
    modules = []
    for info in _registry.get_all_modules():
        modules.append({
            "name": info.name,
            "has_api": info.has_api,
            "has_core": info.has_core,
            "status": info.status,
            "last_check": datetime.fromtimestamp(info.last_check).isoformat()
        })
    
    return {
        "success": True,
        "data": {
            "modules": modules,
            "total": len(modules),
            "with_api": sum(1 for m in modules if m["has_api"]),
            "with_core": sum(1 for m in modules if m["has_core"])
        }
    }


@router.get("/modules/status")
async def get_modules_status():
    """获取所有模块状态详情"""
    return {
        "success": True,
        "data": _collector.collect_all()
    }


@router.get("/modules/{module_name}")
async def get_module_detail(
    module_name: str = Path(..., description="模块名称")
):
    """获取单个模块详情"""
    module = _registry.get_module(module_name)
    if not module:
        raise AppError(ErrorCode.NOT_FOUND, f"模块 {module_name} 不存在")
    
    status = _collector.collect_all().get(module_name, {})
    
    return {
        "success": True,
        "data": {
            "info": {
                "name": module.name,
                "path": module.module_path,
                "has_api": module.has_api,
                "has_core": module.has_core
            },
            "status": status
        }
    }


@router.post("/modules/{module_name}/refresh")
async def refresh_module(
    module_name: str = Path(..., description="模块名称")
):
    """刷新模块状态"""
    module = _registry.get_module(module_name)
    if not module:
        raise AppError(ErrorCode.NOT_FOUND, f"模块 {module_name} 不存在")
    
    module.last_check = time.time()
    
    return {
        "success": True,
        "data": {"message": f"模块 {module_name} 已刷新"}
    }


# ==============================================================================
# 4. 记忆模块 (Memory Module)
# ==============================================================================

@router.get("/memory")
async def get_memory_full():
    """获取记忆模块完整信息"""
    # 旧版 MemoryManager 已废弃，事件记忆由 EventStore 管理
    from modules.memory import EventStore
    store = EventStore.get_instance()
    return {
        "success": True,
        "data": {
            "event_system": "active",
            "event_count": store.count_events(),
            "faiss_vectors": store._faiss_index.ntotal if store._faiss_index else 0,
            "type": "事件驱动记忆 (EventReducer + EventStore + EventRetrieval)",
            "note": "旧版 short_term/long_term/personality/blackbox 已移除",
        }
    }


@router.get("/memory/tool-skills")
async def get_tool_skills():
    """获取工具熟练度（旧版 MemoryManager 已废弃）"""
    return {
        "success": True,
        "data": {
            "skills": [],
            "top_tools": [],
            "note": "旧版 tool_skills 已移除，事件记忆替代",
        }
    }


@router.post("/memory/tool-skills/{tool_name}/success")
async def record_tool_success(
    tool_name: str = Path(..., description="工具名称")
):
    """记录工具使用成功（旧版 MemoryManager 已废弃）"""
    return {
        "success": True,
        "data": {"message": f"旧版 MemoryManager 已废弃", "tool": tool_name}
    }


@router.post("/memory/tool-skills/{tool_name}/failure")
async def record_tool_failure(
    tool_name: str = Path(..., description="工具名称")
):
    """记录工具使用失败（旧版 MemoryManager 已废弃）"""
    return {
        "success": True,
        "data": {"message": f"旧版 MemoryManager 已废弃", "tool": tool_name}
    }


@router.post("/memory/clear")
async def clear_memory(
    scope: str = Query("short_term", description="清理范围: short_term / long_term / all")
):
    """清空记忆"""
    from modules.memory import EventStore
    store = EventStore.get_instance()
    store.clear_all()
    return {
        "success": True,
        "data": {"message": f"事件记忆已清空"}
    }


# ==============================================================================
# 5. 感知模块 (Perception Module)
# ==============================================================================

@router.get("/perception")
async def get_perception_full():
    """获取感知模块完整信息"""
    try:
        import platform
        from modules.perception import get_perception_system

        ps = get_perception_system()
        status = ps.get_status()

        watch_paths = []
        if ps.file_perception and hasattr(ps.file_perception, "watch_paths"):
            watch_paths = ps.file_perception.watch_paths

        return {
            "success": True,
            "data": {
                "status": "running" if ps._started else "stopped",
                "platform": platform.system(),
                "watch_paths": watch_paths,
                "pipeline": status.get("pipeline"),
                "voice_available": status.get("voice_available", False),
                "world_state": status.get("world_state"),
                "event_bus": status.get("event_bus"),
            }
        }
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "管理操作失败")


@router.post("/perception/start")
async def start_perception():
    """启动感知监控"""
    try:
        from modules.perception import perception_manager
        perception_manager.start_monitoring()
        
        return {
            "success": True,
            "data": {"message": "感知监控已启动"}
        }
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "管理操作失败")


@router.post("/perception/stop")
async def stop_perception():
    """停止感知监控"""
    try:
        from modules.perception import perception_manager
        perception_manager.stop_monitoring()
        
        return {
            "success": True,
            "data": {"message": "感知监控已停止"}
        }
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "管理操作失败")


@router.post("/perception/clear")
async def clear_perception():
    """清空调知池（已迁移：新架构无独立注意力池）"""
    try:
        return {
            "success": True,
            "data": {"message": "注意力池功能已迁移至新架构，无需手动清空"}
        }
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "管理操作失败")


# ==============================================================================
# 6. 数据库 (Database)
# ==============================================================================

@router.get("/database")
async def get_database_info():
    """获取数据库信息"""
    try:
        from modules.database.disk_cache import disk_cache
        import sqlite3
        
        stats = disk_cache.get_stats()
        
        db_path = str(PROJECT_ROOT / "data" / "memory.db")
        tables_info = []
        
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            
            for table in tables:
                try:
                    # 安全标识符引用：table 来自 sqlite_master 而非用户输入，但双引号转义是最佳实践
                    safe_table = f'"{table.replace(chr(34), chr(34)+chr(34))}"'
                    cursor.execute(f"SELECT COUNT(*) FROM {safe_table}")
                    count = cursor.fetchone()[0]
                    cursor.execute(f"PRAGMA table_info({safe_table})")
                    columns = [col[1] for col in cursor.fetchall()]
                    tables_info.append({
                        "name": table,
                        "row_count": count,
                        "columns": columns
                    })
                except Exception as e:
                    logger.debug(f"读取表 {table} 信息失败: {e}")
            conn.close()
        except Exception as e:
            logger.warning(f"读取数据库信息失败: {e}")
        
        return {
            "success": True,
            "data": {
                "type": "sqlite",
                "path": db_path,
                "cache": {
                    "mode": stats.get("mode", "unknown"),
                    "hits": stats.get("hits", 0),
                    "misses": stats.get("misses", 0)
                },
                "tables": tables_info
            }
        }
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "管理操作失败")





# ==============================================================================
# 8. 信息处理 (Info Process)
# ==============================================================================

@router.get("/info-process")
async def get_info_process_status():
    """获取信息处理模块状态"""
    try:
        from infra.data_process.core.image_analyzer import ImageAnalyzer
        from infra.data_process.core.speech_recognizer import SpeechRecognizer
        
        analyzer = ImageAnalyzer()
        recognizer = SpeechRecognizer()
        
        return {
            "success": True,
            "data": {
                "image_analyzer": {
                    "type": analyzer.model_type,
                    "initialized": analyzer._initialized,
                    "model": analyzer.local_model
                },
                "speech_recognizer": {
                    "model": recognizer.model_name,
                    "initialized": recognizer._initialized
                }
            }
        }
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "管理操作失败")


# ==============================================================================
# 9. 思维模块 (Thinking)
# ==============================================================================

@router.get("/thinking")
async def get_thinking_status():
    """获取思维模块状态"""
    try:
        from modules.thinking.core.continuous_thinker import ContinuousThinker
        from modules.thinking.core.model_manager import model_manager

        big_ok = model_manager.big_model is not None
        medium_ok = model_manager.middle_model is not None
        small_ok = model_manager.small_model is not None

        return {
            "success": True,
            "data": {
                "status": "healthy" if big_ok else "degraded",
                "available": big_ok,
                "models": {
                    "big": big_ok,
                    "medium": medium_ok,
                    "small": small_ok,
                },
                "capabilities": [
                    "continuous_thinking",
                    "deep_thinking",
                    "emotion_judgment",
                    "value_matching"
                ]
            }
        }
    except Exception as e:
        logger.warning(f"获取思维模块状态失败: {e}")
        return {
            "success": True,
            "data": {"status": "unavailable", "message": "思维模块暂不可用"}
        }


# ==============================================================================
# 10. 注意力模块 (Attention)
# ==============================================================================

@router.get("/attention")
async def get_attention_status():
    """获取注意力模块状态"""
    try:
        return {
            "success": True,
            "data": {
                "status": "available",
                "capabilities": [
                    "weight_calculation",
                    "task_scheduling",
                    "priority_queue"
                ]
            }
        }
    except Exception as e:
        logger.warning(f"获取注意力模块状态失败: {e}")
        return {
            "success": True,
            "data": {"status": "unavailable", "message": "注意力模块暂不可用"}
        }


# ==============================================================================
# 11. 安全模块 (Security)
# ==============================================================================

@router.get("/security")
async def get_security_status():
    """获取安全模块状态"""
    try:
        from modules.security_system.audit_logger import AuditLogger

        audit = AuditLogger()
        audit_ok = audit is not None

        return {
            "success": True,
            "data": {
                "status": "healthy" if audit_ok else "degraded",
                "audit_enabled": audit_ok,
                "available": audit_ok
            }
        }
    except Exception as e:
        logger.warning(f"获取安全模块状态失败: {e}")
        return {
            "success": True,
            "data": {
                "status": "unavailable",
                "message": "安全模块暂不可用"
            }
        }


# ==============================================================================
# 12. 状态检查 (Health Check)
# ==============================================================================

@router.get("/health")
async def health_check():
    """健康检查"""
    statuses = _collector.collect_all()
    healthy_count = sum(1 for s in statuses.values() if s.get("status") == "healthy")
    total_count = len(statuses)
    
    return {
        "success": True,
        "data": {
            "status": "healthy" if healthy_count == total_count else "degraded",
            "healthy_modules": healthy_count,
            "total_modules": total_count,
            "timestamp": datetime.now().isoformat()
        }
    }


@router.get("/")
async def root():
    """管理API根路径"""
    return {
        "success": True,
        "data": {
            "module": "management",
            "version": "1.0.0",
            "endpoints": {
                "dashboard": "/management/dashboard",
                "system": "/management/system",
                "modules": "/management/modules",
                "memory": "/management/memory",
                "perception": "/management/perception",
                "database": "/management/database",
                "info_process": "/management/info-process",
                "thinking": "/management/thinking",
                "attention": "/management/attention",
                "security": "/management/security",
                "health": "/management/health"
            }
        }
    }





# ==============================================================================
# 14. 全局上下文管理 API
# ==============================================================================


@router.get("/context")
async def get_context_status():
    """获取全局上下文池状态 — GCM 已移除"""
    return {"success": True, "data": {"status": "removed", "note": "GCM 已移除，此端点不再提供服务"}}


@router.get("/context/stats")
async def get_context_stats():
    """获取上下文简略统计 — GCM 已移除"""
    return {"success": True, "data": {"status": "removed"}}


@router.get("/context/warnings")
async def get_context_warnings(limit: int = Query(20, ge=1, le=100)):
    """获取上下文审计警告 — GCM 已移除"""
    return {"success": True, "data": {"warnings": [], "note": "GCM 已移除"}}


@router.post("/context/clear-warnings")
async def clear_context_warnings():
    """清除上下文审计警告 — GCM 已移除"""
    return {"success": True, "data": {"message": "GCM 已移除，无警告"}}


# ==============================================================================
# 13. 多模型会话监控 (Multi-Model Session Monitor)
# ==============================================================================

@router.get("/sessions")
async def get_sessions(dialog_limit: int = Query(50, ge=1, le=500)):
    """
    获取所有活跃会话及对话框内容
    """
    try:
        from modules.thinking.cognition.session_lifecycle import get_active_sessions

        sessions = []
        for lifecycle in get_active_sessions():
            bb = lifecycle.blackboard
            dialog = bb.read_dialog(limit=dialog_limit) if bb else []
            sessions.append({
                "session_id": lifecycle.session_id,
                "state": lifecycle.state.value,
                "is_active": lifecycle.is_active,
                "turn_id": lifecycle.turn_id,
                "dialog_size": len(dialog),
                "active_runners": len(lifecycle._active_runners) if hasattr(lifecycle, '_active_runners') else 0,
                "participants": list(lifecycle._participants) if hasattr(lifecycle, '_participants') else [],
                "dialog": [e.to_dict() if hasattr(e, 'to_dict') else e for e in dialog],
            })

        return {"success": True, "data": {"sessions": sessions, "total": len(sessions)}}

    except Exception as e:
        logger.error(f"获取会话失败: {e}")
        raise AppError(ErrorCode.INTERNAL_ERROR, f"获取会话失败: {e}")


# ==============================================================================
# 14. 模型实例监控 (Model Runner Status)
# ==============================================================================

@router.get("/models")
async def get_model_runners():
    """
    获取所有活跃模型实例（runner）状态
    """
    try:
        from modules.thinking.cognition.session_lifecycle import get_active_sessions
        from modules.thinking.core.model_runner import get_runner_manager, ModelRunnerManager

        all_runners = []
        for lifecycle in get_active_sessions():
            rm = get_runner_manager(lifecycle.session_id)
            if rm:
                runners = rm.list_runners()
                all_runners.extend(runners)

        summary = {}
        for r in all_runners:
            tier = r.get("tier", "unknown")
            if tier not in summary:
                summary[tier] = {"active": 0, "max": ModelRunnerManager.MAX_RUNNERS.get(tier, 8)}
            summary[tier]["active"] += 1

        return {
            "success": True,
            "data": {
                "runners": all_runners,
                "summary": summary,
            },
        }
    except Exception as e:
        logger.error(f"获取模型列表失败: {e}")
        return {"success": True, "data": {"runners": [], "summary": {}}}


@router.get("/sessions/{session_id}/dialog")
async def get_session_dialog(
    session_id: str,
    limit: int = Query(100, ge=1, le=1000),
):
    """
    获取指定会话的对话框内容
    """
    try:
        from modules.thinking.cognition.session_lifecycle import get_active_sessions

        bb = None
        for lifecycle in get_active_sessions():
            if lifecycle.session_id == session_id:
                bb = lifecycle.blackboard
                break

        if not bb:
            raise AppError(ErrorCode.NOT_FOUND, f"会话不存在: {session_id}")

        dialog = bb.read_dialog(limit=limit)
        return {
            "success": True,
            "data": {
                "session_id": session_id,
                "dialog_size": len(dialog),
                "dialog": [e.to_dict() if hasattr(e, 'to_dict') else e for e in dialog],
            },
        }

    except AppError:
        raise
    except Exception as e:
        logger.error(f"获取会话对话框失败: {e}")
        raise AppError(ErrorCode.INTERNAL_ERROR, f"获取会话对话框失败: {e}")


@router.get("/runners")
async def get_runners():
    """
    获取所有活跃的 ModelRunner
    """
    try:
        from modules.thinking.cognition.session_lifecycle import get_active_sessions
        from modules.thinking.core.model_runner import get_runner_manager

        runners = []
        for lifecycle in get_active_sessions():
            rm = get_runner_manager(lifecycle.session_id)
            if rm:
                try:
                    runner_list = rm.list_runners() if hasattr(rm, 'list_runners') else []
                    for r in runner_list:
                        if isinstance(r, dict):
                            runners.append({
                                "model_id": r.get("model_id", ""),
                                "identity_key": r.get("identity_key", ""),
                                "tier": r.get("tier", ""),
                                "role": r.get("role", ""),
                                "status": r.get("status", "active"),
                                "session_id": lifecycle.session_id,
                            })
                except Exception as e:
                    logger.debug(f"读取 runner 信息失败: {e}")

        return {"success": True, "data": {"count": len(runners), "runners": runners}}

    except Exception as e:
        logger.error(f"获取 runner 失败: {e}")
        raise AppError(ErrorCode.INTERNAL_ERROR, f"获取 runner 失败: {e}")


@router.get("/bus")
async def get_bus_stats(peek: bool = Query(False), peek_all: bool = Query(False)):
    """
    获取 MessageBus 统计和消息队列

    Args:
        peek: 是否查看队列消息（不消费）
        peek_all: 是否查看所有队列
    """
    try:
        from modules.thinking.communication.message_bus import get_message_bus

        bus = get_message_bus()
        stats = await bus.get_stats() if hasattr(bus, 'get_stats') else {}
        recipients = await bus.list_recipients() if hasattr(bus, 'list_recipients') else []

        result = {
            "stats": stats,
            "recipients": recipients,
        }

        if peek_all:
            result["queues"] = {}
            if hasattr(bus, 'peek_all'):
                for rid, msgs in bus.peek_all().items():
                    result["queues"][rid] = {
                        "count": len(msgs),
                        "messages": [m.to_dict() if hasattr(m, 'to_dict') else str(m) for m in msgs[:50]],
                    }
        elif peek and recipients:
            result["queues"] = {}
            if hasattr(bus, 'peek'):
                for rid in recipients[:10]:
                    msgs = await bus.peek(rid, limit=20)
                    if msgs:
                        result["queues"][rid] = {
                            "count": len(msgs),
                            "messages": [m.to_dict() if hasattr(m, 'to_dict') else str(m) for m in msgs],
                        }

        return {"success": True, "data": result}

    except Exception as e:
        logger.error(f"获取总线统计失败: {e}")
        raise AppError(ErrorCode.INTERNAL_ERROR, f"获取总线统计失败: {e}")
