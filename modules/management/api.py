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
from fastapi import APIRouter, Query, Path
from datetime import datetime
from pathlib import Path as FilePath

PROJECT_ROOT = FilePath(__file__).resolve().parents[2]
import time

from api.errors import AppError, ErrorCode
from modules.management.core.collector import ModuleRegistry, StatusCollector
from utils.logger import setup_logger

logger = setup_logger("management_api")

# 统一认证：使用 X-API-Key
router = APIRouter(prefix="/management", tags=["管理控制台"])

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
            },
            "api_requests": _recent_api_requests(),
        }
    }


def _recent_api_requests(limit: int = 50) -> list:
    try:
        from modules.management.api_log_store import ApiLogStore
        store = ApiLogStore.get_instance()
        store.flush()
        return store.query(limit=limit, include_body=False)
    except Exception:
        return []


@router.get("/api-requests")
async def get_api_requests(method: str = "", path: str = "", status: int = 0,
                           limit: int = 50, offset: int = 0, since_hours: float = 0):
    """API 请求日志：按方法/路径/状态/时间筛选 + 分页（可追溯）"""
    from modules.management.api_log_store import ApiLogStore
    store = ApiLogStore.get_instance()
    store.flush()
    return {
        "success": True,
        "data": {
            "items": store.query(method, path, status, limit, offset, since_hours),
            "total": store.count(method, path, status, since_hours),
        }
    }


@router.get("/api-requests/stats")
async def get_api_requests_stats(since_hours: float = 0):
    """API 请求统计：总量/平均耗时/按方法/按状态"""
    from modules.management.api_log_store import ApiLogStore
    store = ApiLogStore.get_instance()
    store.flush()
    return {"success": True, "data": store.stats(since_hours)}


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


@router.get("/orchestration")
async def get_orchestration():
    """编排界面数据：Agent 定义（人设/模型/工具/覆盖）——合并内置 + 自定义 agent"""
    from config.prompts.loader import get_loader
    from config.settings import settings
    roles = (get_loader().load("roles") or {}).get("roles") or {}
    agents = []
    # 内置 agent（roles.yaml）
    for key, data in roles.items():
        agents.append({
            "role": key,
            "name": data.get("name", key),
            "tier": data.get("tier", ""),
            "personality": data.get("personality", ""),
            "speaking_style": data.get("speaking_style", ""),
            "expertise": data.get("expertise", ""),
            "model_id": data.get("model_id", ""),
            "custom_persona": settings.get_persona(key),
            "system_override": settings.get_system_override(key),
            "role_tools": settings.get_role_tools(key),
            "model_params": settings.get_model_params(key),
            "is_custom": False,
        })
    # 自定义 agent（personas.yaml）
    for ca in settings.get_custom_agents():
        role = ca.get("role", "")
        if role:
            ca["custom_persona"] = settings.get_persona(role)
            ca["system_override"] = settings.get_system_override(role)
            ca["role_tools"] = settings.get_role_tools(role)
            ca["model_params"] = settings.get_model_params(role)
            ca["is_custom"] = True
            agents.append(ca)
    agents.sort(key=lambda a: {"large": 0, "supervisor": 1, "expert": 2}.get(a["tier"], 9))
    return {"success": True, "data": {"agents": agents}}


@router.get("/todos")
async def get_todos(session_id: str = ""):
    """读取指定会话的任务列表（todo 工具数据，按会话隔离）"""
    from infra.tool_manager.tools.todo import _load_todos
    return {"success": True, "data": {"todos": _load_todos(session_id)}}


@router.put("/todos/{task_id}/status")
async def set_todo_status(task_id: str, body: dict = None, session_id: str = ""):
    """更新任务状态（前端勾选/取消完成）"""
    import time
    body = body or {}
    status = str(body.get("status", "pending"))
    if status not in ("pending", "in_progress", "completed"):
        return {"success": False, "error": {"code": "VALIDATION_ERROR", "message": f"无效状态: {status}"}}
    from infra.tool_manager.tools.todo import _load_todos, _save_todos
    todos = _load_todos(session_id)
    for t in todos:
        if t.get("id") == task_id:
            t["status"] = status
            t["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _save_todos(todos, session_id)
            return {"success": True, "data": {"id": task_id, "status": status, "todos": todos}}
    return {"success": False, "error": {"code": "NOT_FOUND", "message": f"任务不存在: {task_id}"}}


@router.get("/skills")
async def list_skills():
    """技能管理列表（含 enabled/source/metadata）"""
    from modules.thinking.skills import skill_manager
    return {"success": True, "data": {"skills": skill_manager.to_listing()}}


@router.get("/skills/forced")
async def get_forced_skill():
    """获取全局强制技能配置（非空时所有对话必须使用该技能，不可切换/停用）"""
    from config.settings import settings
    skill_id = settings.get_forced_skill()
    skill = None
    if skill_id:
        from modules.thinking.skills import skill_manager
        s = skill_manager.get_skill(skill_id)
        if s:
            skill = {"id": s.id, "name": s.name, "description": s.description[:120]}
    return {"success": True, "data": {"forced_skill": skill_id, "skill": skill}}


@router.put("/skills/forced")
async def set_forced_skill(body: dict = None):
    """设置/清除全局强制技能（body: {"skill_id": "xxx"} 或 {"skill_id": ""} 清除）"""
    body = body or {}
    from config.settings import settings
    skill_id = str(body.get("skill_id") or "").strip()
    if skill_id:
        from modules.thinking.skills import skill_manager
        s = skill_manager.get_skill(skill_id)
        if not s:
            return {"success": False, "error": {"code": "SKILL_NOT_FOUND", "message": f"技能不存在: {skill_id}"}}
        if not s.enabled:
            return {"success": False, "error": {"code": "SKILL_DISABLED", "message": f"技能已禁用: {skill_id}"}}
    settings.set_forced_skill(skill_id)
    skill = None
    if skill_id:
        from modules.thinking.skills import skill_manager
        s = skill_manager.get_skill(skill_id)
        skill = {"id": s.id, "name": s.name} if s else None
    return {"success": True, "data": {"forced_skill": skill_id, "skill": skill}}


@router.get("/skills/{skill_id}")
async def get_skill_detail(skill_id: str):
    """技能详情（含原文 raw_content 供编辑回填）"""
    from modules.thinking.skills import skill_manager
    skill = skill_manager.get_skill(skill_id)
    if not skill:
        return {"success": False, "error": {"code": "SKILL_NOT_FOUND", "message": f"技能不存在: {skill_id}"}}
    return {"success": True, "data": {
        "id": skill.id, "name": skill.name, "description": skill.description,
        "keywords": list(skill.keywords), "source": skill.source,
        "enabled": skill.enabled, "metadata": skill.metadata,
        "tool_rules": skill.tool_rules, "trigger": skill.trigger,
        "raw_content": skill.raw_content, "path": skill.path,
    }}


@router.post("/skills")
async def create_skill(body: dict = None):
    """创建新技能（写 skills/<id>/SKILL.md）"""
    body = body or {}
    from modules.thinking.skills import skill_manager
    ok, msg = skill_manager.create_skill(
        skill_id=str(body.get("id") or ""),
        name=str(body.get("name") or ""),
        description=str(body.get("description") or ""),
        keywords=body.get("keywords") or [],
        trigger=body.get("trigger"),
        tool_rules=body.get("tool_rules"),
    )
    if not ok:
        return {"success": False, "error": {"code": "SKILL_CREATE_ERROR", "message": msg}}
    return {"success": True, "data": {"message": msg}}


@router.put("/skills/{skill_id}")
async def update_skill(skill_id: str, body: dict = None):
    """更新技能（改 frontmatter/正文并重载）"""
    body = body or {}
    from modules.thinking.skills import skill_manager
    ok, msg = skill_manager.update_skill(
        skill_id,
        name=body.get("name") if "name" in body else None,
        description=body.get("description") if "description" in body else None,
        keywords=body.get("keywords") if "keywords" in body else None,
        trigger=body.get("trigger") if "trigger" in body else None,
        tool_rules=body.get("tool_rules") if "tool_rules" in body else None,
    )
    if not ok:
        return {"success": False, "error": {"code": "SKILL_UPDATE_ERROR", "message": msg}}
    return {"success": True, "data": {"message": msg}}


@router.put("/skills/{skill_id}/enabled")
async def set_skill_enabled(skill_id: str, body: dict = None):
    """启用/禁用技能"""
    body = body or {}
    from modules.thinking.skills import skill_manager
    ok, msg = skill_manager.set_enabled(skill_id, bool(body.get("enabled", True)))
    if not ok:
        return {"success": False, "error": {"code": "SKILL_STATE_ERROR", "message": msg}}
    return {"success": True, "data": {"id": skill_id, "enabled": bool(body.get("enabled", True))}}


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: str):
    """删除技能（内置技能受保护）"""
    from modules.thinking.skills import skill_manager
    ok, msg = skill_manager.delete_skill(skill_id)
    if not ok:
        return {"success": False, "error": {"code": "SKILL_DELETE_ERROR", "message": msg}}
    return {"success": True, "data": {"message": msg}}


@router.post("/skills/reload")
async def reload_skills():
    """重载全部技能"""
    from modules.thinking.skills import skill_manager
    n = skill_manager.reload()
    return {"success": True, "data": {"count": n}}


@router.get("/config/role-skills/{role}")
async def get_role_skills(role: str):
    """读取角色可见技能白名单"""
    from config.settings import settings
    return {"success": True, "data": {"role": role, "skills": settings.get_role_skills(role)}}


@router.put("/config/role-skills/{role}")
async def update_role_skills(role: str, body: dict = None):
    """写入角色可见技能白名单（[] 或含 * 表示全部）"""
    body = body or {}
    from config.settings import settings
    ids = body.get("skills") or []
    if not isinstance(ids, list):
        return {"success": False, "error": {"code": "VALIDATION_ERROR", "message": "skills 需为数组"}}
    settings.set_role_skills(role, ids)
    return {"success": True, "data": {"role": role, "skills": settings.get_role_skills(role)}}


@router.post("/orchestration/preview")
async def orchestration_preview(body: dict = None):
    """预览指定角色的实际 system prompt（应用人设/system_override 覆盖）"""
    from config.prompts.composer import PromptComposer, PromptRequest
    from config.settings import settings as _cfg
    body = body or {}
    role = str(body.get("role") or "orchestrator")
    tier = str(body.get("tier") or "large")
    try:
        composer = PromptRequest(
            tier=tier,
            role=role,
            mode=_cfg.effective_execution_mode,
        )
        prompt = PromptComposer().build_system(composer)
        return {"success": True, "data": {"role": role, "tier": tier, "prompt": prompt}}
    except Exception as e:
        return {"success": False, "error": {"code": "PREVIEW_ERROR", "message": str(e)}}


@router.post("/open-folder")
async def open_folder(body: dict = None):
    """打开模型/数据文件夹（Finder）——桌宠模型 / 语音模型 等（与设置项一一对应）"""
    import subprocess
    from pathlib import Path
    folder = (body or {}).get("folder", "") if isinstance(body, dict) else ""
    root = Path(__file__).resolve().parents[2]
    paths = {
        "pet": root / "frontend" / "pet" / "models",
        "voice": Path.home() / ".cache" / "whisper",
        "vision": root / "data" / "models" / "vision",
        "data": root / "data",
    }
    target = paths.get(folder)
    if not target:
        return {"success": False, "error": {"code": "BAD_FOLDER", "message": f"未知目录: {folder}"}}
    try:
        target.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["open", str(target)])
        return {"success": True, "data": {"folder": str(target)}}
    except Exception as e:
        return {"success": False, "error": {"code": "OPEN_FAILED", "message": str(e)}}


@router.get("/vision-models")
async def list_vision_models():
    """扫描本地视觉模型文件夹，返回可用模型列表"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    vision_dir = root / "data" / "models" / "vision"
    vision_dir.mkdir(parents=True, exist_ok=True)

    models = []
    for item in sorted(vision_dir.iterdir()):
        if not item.is_dir():
            continue
        # 检查是否是有效的模型目录（包含 config.json 或 preprocessor_config.json）
        cfg = item / "config.json"
        pre_cfg = item / "preprocessor_config.json"
        if cfg.exists() or pre_cfg.exists():
            # 尝试读取模型类型信息
            model_type = ""
            if cfg.exists():
                try:
                    import json
                    with open(cfg) as f:
                        c = json.load(f)
                    model_type = c.get("model_type", "")
                except Exception:
                    pass
            models.append({
                "name": item.name,
                "path": str(item),
                "model_type": model_type,
            })

    return {"success": True, "data": {"models": models, "dir": str(vision_dir)}}


@router.post("/install-voice-deps")
async def install_voice_deps():
    """一键安装语音识别依赖

    安装：SpeechRecognition, pyaudio, openai-whisper, pynput
    返回安装状态和输出信息
    """
    import subprocess
    import sys

    packages = [
        ("SpeechRecognition", "speech_recognition"),
        ("pyaudio", "pyaudio"),
        ("openai-whisper", "whisper"),
        ("pynput", "pynput"),
    ]

    results = []
    all_success = True

    for pkg_name, import_name in packages:
        try:
            # 先检查是否已安装
            __import__(import_name)
            results.append({"package": pkg_name, "status": "already_installed"})
            continue
        except ImportError:
            pass

        # 尝试安装
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", pkg_name, "-q"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode == 0:
                # 验证安装
                try:
                    __import__(import_name)
                    results.append({"package": pkg_name, "status": "installed"})
                except ImportError:
                    results.append({"package": pkg_name, "status": "install_failed", "reason": "import 失败"})
                    all_success = False
            else:
                results.append({"package": pkg_name, "status": "install_failed", "reason": proc.stderr[:200]})
                all_success = False
        except Exception as e:
            results.append({"package": pkg_name, "status": "error", "reason": str(e)})
            all_success = False

    return {
        "success": all_success,
        "data": {
            "results": results,
            "message": "所有依赖安装成功" if all_success else "部分依赖安装失败，请查看日志"
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


@router.get("/memory/events")
async def list_events(
    limit: int = Query(50, description="返回条数"),
    type: str = Query("", description="按类型过滤: fact/strategy/thought/emotion"),
    keyword: str = Query("", description="按关键词过滤"),
):
    """查看记忆库中的事件列表"""
    from modules.memory.event_store import EventStore
    store = EventStore.get_instance()
    events = store.list_events(limit=limit)

    # 过滤
    if type:
        events = [e for e in events if e.type == type]
    if keyword:
        kw = keyword.lower()
        events = [e for e in events if kw in e.fact.lower() or any(kw in k.lower() for k in e.keywords)]

    items = []
    for ev in events:
        items.append({
            "id": ev.id,
            "type": ev.type,
            "fact": ev.fact[:150],
            "thought": ev.thought[:100] if ev.thought else "",
            "lesson": ev.lesson[:100] if ev.lesson else "",
            "keywords": ev.keywords,
            "importance": ev.importance,
            "time": ev.time,
            "session_id": ev.session_id[:16] if ev.session_id else "",
            "causal_node_ids": ev.causal_node_ids or [],
        })

    return {
        "success": True,
        "data": {
            "total": store.count_events(),
            "returned": len(items),
            "events": items,
        }
    }


@router.post("/memory/events")
async def create_event(
    fact: str = Query(..., description="事件事实"),
    keywords: str = Query("", description="关键词，逗号分隔"),
    importance: float = Query(0.5, description="重要性 0-1"),
    event_type: str = Query("fact", description="类型: fact/strategy/thought/emotion"),
    thought: str = Query("", description="思考"),
    lesson: str = Query("", description="经验教训"),
):
    """创建新事件"""
    from modules.memory.event_store import EventStore, MemoryEvent
    store = EventStore.get_instance()
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()] if keywords else []
    ev = MemoryEvent(
        fact=fact, keywords=kw_list, importance=importance,
        type=event_type, thought=thought, lesson=lesson,
    )
    eid = store.save_event(ev)
    return {"success": True, "data": {"id": eid, "fact": fact[:100]}}


@router.get("/memory/events/{event_id}")
async def get_event(event_id: str = Path(...)):
    """获取单个事件详情"""
    from modules.memory.event_store import EventStore
    store = EventStore.get_instance()
    ev = store.get_event(event_id)
    if not ev:
        raise AppError(ErrorCode.NOT_FOUND, f"事件 {event_id} 不存在")
    return {
        "success": True,
        "data": {
            "id": ev.id, "type": ev.type, "fact": ev.fact,
            "thought": ev.thought, "lesson": ev.lesson,
            "keywords": ev.keywords, "importance": ev.importance,
            "time": ev.time, "session_id": ev.session_id,
            "causal_node_ids": ev.causal_node_ids or [],
            "access_count": ev.access_count, "mention_count": ev.mention_count,
        }
    }


@router.put("/memory/events/{event_id}")
async def update_event(
    event_id: str = Path(...),
    fact: str = Query(None),
    keywords: str = Query(None),
    importance: float = Query(None),
    event_type: str = Query(None),
):
    """更新事件"""
    from modules.memory.event_store import EventStore
    store = EventStore.get_instance()
    ev = store.get_event(event_id)
    if not ev:
        raise AppError(ErrorCode.NOT_FOUND, f"事件 {event_id} 不存在")
    if fact is not None: ev.fact = fact
    if keywords is not None: ev.keywords = [k.strip() for k in keywords.split(",") if k.strip()]
    if importance is not None: ev.importance = importance
    if event_type is not None: ev.type = event_type
    store.save_event(ev)
    return {"success": True, "data": {"id": event_id}}


@router.delete("/memory/events/{event_id}")
async def delete_event(event_id: str = Path(...)):
    """删除事件"""
    from modules.memory.event_store import EventStore
    store = EventStore.get_instance()
    ok = store.delete_event(event_id)
    if not ok:
        raise AppError(ErrorCode.NOT_FOUND, f"事件 {event_id} 不存在")
    return {"success": True, "data": {"deleted": event_id}}


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
        "data": {"message": "旧版 MemoryManager 已废弃", "tool": tool_name}
    }


@router.post("/memory/tool-skills/{tool_name}/failure")
async def record_tool_failure(
    tool_name: str = Path(..., description="工具名称")
):
    """记录工具使用失败（旧版 MemoryManager 已废弃）"""
    return {
        "success": True,
        "data": {"message": "旧版 MemoryManager 已废弃", "tool": tool_name}
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
        "data": {"message": "事件记忆已清空"}
    }


@router.get("/causal-graph")
async def get_causal_graph(
    time_window: str = Query("", description="时间窗口过滤，如 30d/7d/24h"),
):
    """获取因果图数据（用于可视化）- O(1) 索引查询"""
    from modules.memory.causal_graph import CausalGraph
    from modules.memory.event_store import EventStore
    graph = CausalGraph.get_instance()

    nodes = graph.list_nodes(limit=200)
    nodes_data = []
    for n in nodes:
        nodes_data.append({
            "id": n.id,
            "label": n.label,
            "type": n.node_type,
            "confidence": round(n.confidence, 3),
            "event_count": n.event_count,
            "keywords": n.keywords,
        })

    # 优化：使用 list_all_edges O(1) 索引查询，非 O(N²) 遍历
    edges = graph.list_all_edges(time_window=time_window or None)
    edges_data = []
    for e in edges:
        edges_data.append({
            "id": e.id,
            "from": e.from_id,
            "to": e.to_id,
            "relation": e.relation,
            "confidence": round(e.confidence, 3),
            "label": e.label or "",
        })

    # 统计
    event_store = EventStore.get_instance()
    total_events = event_store.count_events()
    linked_events = 0
    try:
        events = event_store.list_events(limit=500)
        linked_events = sum(1 for e in events if e.causal_node_ids)
    except Exception:
        pass

    # 使用优化的统计查询
    edge_stats = graph.get_edge_stats()

    return {
        "success": True,
        "data": {
            "nodes": nodes_data,
            "edges": edges_data,
            "stats": {
                "total_nodes": len(nodes_data),
                "total_edges": len(edges_data),
                "total_events": total_events,
                "linked_events": linked_events,
                "root_nodes": sum(1 for n in nodes_data if n["type"] == "root"),
                "cause_nodes": sum(1 for n in nodes_data if n["type"] == "cause"),
                "effect_nodes": sum(1 for n in nodes_data if n["type"] == "effect"),
                "edge_stats": edge_stats,
            }
        }
    }


@router.get("/causal-graph/metrics")
async def get_causal_graph_metrics():
    """获取因果图监控指标（Prometheus 格式）"""
    from modules.memory.causal_graph import CausalGraph
    graph = CausalGraph.get_instance()
    return {
        "success": True,
        "data": {
            "metrics": graph.get_metrics(),
            "prometheus": graph.get_metrics_prometheus(),
        }
    }


@router.get("/causal-graph/{node_id}")
async def get_causal_node_detail(node_id: str = Path(..., description="节点 ID")):
    """获取单个因果节点的详情（包括关联事件和因果链）"""
    from modules.memory.causal_graph import CausalGraph
    from modules.memory.event_store import EventStore

    graph = CausalGraph.get_instance()
    node = graph.get_node(node_id)
    if not node:
        raise AppError(ErrorCode.NOT_FOUND, f"节点 {node_id} 不存在")

    # 前驱和后继
    predecessors = graph.get_predecessors(node_id)
    successors = graph.get_successors(node_id)

    # 关联事件
    store = EventStore.get_instance()
    linked_events = []
    events = store.list_events(limit=200)
    for ev in events:
        if node_id in (ev.causal_node_ids or []):
            linked_events.append({
                "id": ev.id,
                "fact": ev.fact[:100],
                "importance": ev.importance,
                "type": ev.type,
            })

    return {
        "success": True,
        "data": {
            "node": {
                "id": node.id,
                "label": node.label,
                "type": node.node_type,
                "confidence": node.confidence,
                "event_count": node.event_count,
                "keywords": node.keywords,
            },
            "predecessors": [{"id": p.id, "label": p.label} for p in predecessors],
            "successors": [{"id": s.id, "label": s.label} for s in successors],
            "linked_events": linked_events,
        }
    }


@router.get("/causal-graph/tree/{node_id}")
async def get_causal_tree_from_node(
    node_id: str = Path(..., description="锚点节点 ID"),
    depth: int = Query(3, description="遍历深度"),
):
    """从指定节点展开因果树（用于树形可视化）"""
    from modules.memory.causal_graph import CausalGraph
    from modules.memory.causal_tree import CausalTree

    graph = CausalGraph.get_instance()
    tree = CausalTree(graph)

    node = graph.get_node(node_id)
    if not node:
        raise AppError(ErrorCode.NOT_FOUND, f"节点 {node_id} 不存在")

    # 上溯（溯源链）
    up_chains = tree.trace_up(node_id, max_depth=depth)
    # 下钻（预测链）
    down_chains = tree.trace_down(node_id, max_depth=depth)

    def chain_to_dict(chain):
        # 过滤掉只包含锚点自身的自环链
        if len(chain.nodes) <= 1 and chain.nodes[0].id == node_id:
            return None
        return {
            "nodes": [{"id": n.id, "label": n.label, "type": n.node_type} for n in chain.nodes],
            "edges": [{"relation": e.relation, "confidence": e.confidence} for e in chain.edges],
            "confidence": round(chain.confidence, 3),
            "direction": chain.direction,
        }

    return {
        "success": True,
        "data": {
            "anchor": {"id": node.id, "label": node.label, "type": node.node_type},
            "trace_up": [c for c in (chain_to_dict(ch) for ch in up_chains) if c],
            "trace_down": [c for c in (chain_to_dict(ch) for ch in down_chains) if c],
        }
    }


@router.get("/causal-graph/what-if/{node_id}")
async def get_causal_what_if(
    node_id: str = Path(..., description="锚点节点 ID"),
    target_node_id: str = Query(..., description="假设连接的目标节点 ID"),
    relation: str = Query("causes", description="假设关系类型: causes/prevents/requires"),
    confidence: float = Query(0.5, description="假设边的置信度"),
    depth: int = Query(3, description="遍历深度"),
):
    """反事实推理：假设两个节点之间存在因果边"""
    from modules.memory.causal_graph import CausalGraph, CausalEdge
    from modules.memory.causal_tree import CausalTree

    graph = CausalGraph.get_instance()
    tree = CausalTree(graph)

    node = graph.get_node(node_id)
    target = graph.get_node(target_node_id)
    if not node:
        raise AppError(ErrorCode.NOT_FOUND, f"节点 {node_id} 不存在")
    if not target:
        raise AppError(ErrorCode.NOT_FOUND, f"目标节点 {target_node_id} 不存在")

    hypothetical_edge = CausalEdge(
        from_id=node_id, to_id=target_node_id,
        relation=relation, confidence=float(confidence),
    )

    chains = tree.what_if(
        node_id=node_id,
        hypothetical_edge=hypothetical_edge,
        max_depth=depth,
    )

    def chain_to_dict(chain):
        return {
            "nodes": [{"id": n.id, "label": n.label, "type": n.node_type} for n in chain.nodes],
            "edges": [{"relation": e.relation, "confidence": e.confidence} for e in chain.edges],
            "confidence": round(chain.confidence, 3),
            "direction": chain.direction,
        }

    return {
        "success": True,
        "data": {
            "anchor": {"id": node.id, "label": node.label},
            "target": {"id": target.id, "label": target.label},
            "hypothetical_edge": {
                "relation": relation,
                "confidence": confidence,
            },
            "chains": [chain_to_dict(c) for c in chains],
        }
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
        if getattr(ps, "file_perception", None) and hasattr(ps.file_perception, "watch_paths"):
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
        logger.exception("获取感知模块信息失败: %s", e)
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
    except Exception:
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
    except Exception:
        raise AppError(ErrorCode.INTERNAL_ERROR, "管理操作失败")


@router.post("/perception/clear")
async def clear_perception():
    """清空调知池（已迁移：新架构无独立注意力池）"""
    try:
        return {
            "success": True,
            "data": {"message": "注意力池功能已迁移至新架构，无需手动清空"}
        }
    except Exception:
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
    except Exception:
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
    except Exception:
        raise AppError(ErrorCode.INTERNAL_ERROR, "管理操作失败")


# ==============================================================================
# 9. 思维模块 (Thinking)
# ==============================================================================

@router.get("/thinking")
async def get_thinking_status():
    """获取思维模块状态"""
    try:
        from modules.thinking.model_factory import get_model_factory

        factory = get_model_factory()
        big_ok = factory.get_client("large") is not None
        medium_ok = factory.get_client("supervisor") is not None
        small_ok = factory.get_client("expert") is not None

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
# 11. 安全模块 (Security)
# ==============================================================================

@router.get("/security")
async def get_security_status():
    """获取安全模块状态"""
    try:
        from modules.security_system.audit_logger import SecurityAuditLogger
        audit = SecurityAuditLogger()
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
        from modules.thinking.multi_model_orchestrator import get_active_sessions

        sessions = []
        for session in get_active_sessions():
            bb = session.get("blackboard")
            dialog = bb.read_dialog(limit=dialog_limit) if bb else []
            sessions.append({
                "session_id": session.get("session_id", ""),
                "state": session.get("state", "?"),
                "is_active": session.get("is_active", False),
                "turn_id": session.get("turn_id", ""),
                "dialog_size": len(dialog),
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
        from modules.thinking.multi_model_orchestrator import get_active_sessions
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
        from modules.thinking.multi_model_orchestrator import get_active_sessions

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
        from modules.thinking.multi_model_orchestrator import get_active_sessions
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
