"""
工具 API - 工具管理和调用接口
"""
from fastapi import APIRouter, HTTPException, Body, Path, Query, Header, Depends
from typing import Dict, Any, Optional

from infra.tool_manager import tool_manager, ToolRegistry
from infra.tool_manager.service_registry import get_capability
from api.errors import AppError, ErrorCode

logger = __import__("utils.logger", fromlist=["setup_logger"]).setup_logger("tool_api")

# 统一认证：使用 X-API-Key
from api.auth import require_api_key


async def _security_gate_check(tool_name: str, params: Dict[str, Any], caller_role: str) -> None:
    """执行模式安全门检查，拦截则抛出 HTTPException"""
    factory = get_capability("tool_security_gate")
    if factory is None:
        # fail-closed：安全门未注册时默认拒绝，不绕过安全检查
        logger.warning(f"[ToolAPI] 安全门未注册，默认拦截: tool={tool_name}")
        raise HTTPException(status_code=503, detail="安全门未初始化")
    gate = factory()
    allowed, reason = await gate.check(
        tool_name=tool_name,
        tool_params=params,
        caller_tier=caller_role,
        caller_model_id=f"api:{caller_role}",
        caller_role=caller_role,
        # 无会话上下文：该路径触发的用户审批不会被 WS 断开清理（reject_session_reviews）
        # 自动拒绝——HTTP API 已要求 X-API-Key 认证，属可接受边界。
    )
    if not allowed:
        logger.warning(f"[ToolAPI] 安全门控拦截: tool={tool_name} role={caller_role} reason={reason}")
        raise HTTPException(status_code=403, detail=f"安全门控拦截: {reason}")


def require_tool_auth(x_api_key: str = Header(None), caller_role: str = Header(default="expert")) -> tuple:
    """统一认证 + 提取调用者角色"""
    require_api_key(x_api_key)

    # Validate caller_role is from a limited set of allowed roles
    allowed_roles = {"expert", "supervisor", "commander", "system"}
    if caller_role not in allowed_roles:
        caller_role = "expert"

    return caller_role


# 只读端点（GET）由中间件白名单控制（/tools/、/tools/events、/tools/info/ 等免鉴权）；
# 写操作端点单独挂 require_tool_auth，避免无 key 时只读页面被 401 拦截。
router = APIRouter(prefix="/tools", tags=["工具"])


@router.get("/")
async def list_tools(source: str = Query(None, description="来源过滤: builtin/plugin/dynamic")):
    """列出所有可用工具"""
    tools = tool_manager.list_available_tools(source=source)
    
    return {
        "success": True,
        "data": {
            "tools": tools,
            "count": len(tools),
            "by_source": tool_manager.list_by_source()
        }
    }


@router.get("/status")
async def get_tool_status():
    """获取工具管理器状态"""
    return {
        "success": True,
        "data": tool_manager.get_status()
    }




@router.post("/call", dependencies=[Depends(require_tool_auth)])
async def call_tool(
    tool_name: str = Body(..., description="工具名称"),
    params: Dict[str, Any] = Body(default={}, description="工具参数"),
    caller_role: str = Header(default="expert", description="调用者角色")
):
    """调用工具（经过安全门检查）"""
    await _security_gate_check(tool_name, params, caller_role)
    result = await tool_manager.call_tool(tool_name, params, caller_role=caller_role)
    return {"success": True, "data": result}


@router.post("/call-sync", dependencies=[Depends(require_tool_auth)])
async def call_tool_sync(
    tool_name: str = Body(..., description="工具名称"),
    params: Dict[str, Any] = Body(default={}, description="工具参数"),
    caller_role: str = Header(default="expert", description="调用者角色")
):
    """同步调用工具（经过安全门检查）"""
    await _security_gate_check(tool_name, params, caller_role)
    result = tool_manager.call_tool_sync(tool_name, params, caller_role=caller_role)
    return {"success": True, "data": result}


@router.post("/call-json", dependencies=[Depends(require_tool_auth)])
async def call_from_json(json_str: str = Body(..., description="JSON格式的工具调用")):
    """从JSON调用工具（经过安全门检查）"""
    try:
        import json
        parsed = json.loads(json_str) if isinstance(json_str, str) else json_str
        tool_name = parsed.get("tool_name") or parsed.get("name", "")
        params = parsed.get("params") or parsed.get("arguments", {})
        caller_role = parsed.get("caller_role", "expert")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON format")
    await _security_gate_check(tool_name, params, caller_role)
    result = tool_manager.call_tool_sync(tool_name, params, caller_role=caller_role)
    return {"success": True, "data": result}


@router.get("/events")
async def get_tool_events(
    limit: int = Query(50, ge=1, le=1000, description="返回数量"),
    tool_name: str = Query(None, description="按工具名过滤"),
    success: Optional[bool] = Query(None, description="按成功/失败过滤"),
    since: float = Query(None, description="起始时间戳")
):
    """获取工具调用历史"""
    events = tool_manager.get_tool_events(
        limit=limit,
        tool_name=tool_name,
        success=success,
        since=since
    )
    return {
        "success": True,
        "data": {
            "events": events,
            "count": len(events)
        }
    }


@router.get("/events/stats")
async def get_tool_event_stats():
    """获取工具调用统计"""
    return {
        "success": True,
        "data": tool_manager.get_tool_event_stats()
    }


@router.delete("/events", dependencies=[Depends(require_tool_auth)])
async def clear_tool_events():
    """清空工具调用历史"""
    cleared = tool_manager.clear_tool_events()
    return {
        "success": True,
        "message": f"已清空 {cleared} 条工具调用记录",
        "data": {"cleared": cleared}
    }


@router.put("/enabled/{tool_name}")
async def set_tool_enabled(tool_name: str, body: dict = None):
    """运行时启用/禁用工具（持久化）。安全工具不可禁用。"""
    body = body or {}
    enabled = bool(body.get("enabled", True))
    ok, msg = ToolRegistry.set_tool_enabled(tool_name, enabled)
    if not ok:
        return {"success": False, "error": {"code": "TOOL_STATE_ERROR", "message": msg}}
    return {"success": True, "data": {"name": tool_name, "enabled": enabled}}


@router.get("/source/{tool_name}")
async def get_tool_source(tool_name: str):
    """返回工具源码（内置只读；AI 自创工具 editable 可改）"""
    import inspect
    tool = ToolRegistry.get_tool(tool_name)
    if not tool:
        return {"success": False, "error": {"code": "TOOL_NOT_FOUND", "message": f"工具不存在: {tool_name}"}}
    try:
        src = inspect.getsource(tool.func)
    except (OSError, TypeError):
        src = "# 该工具源码不可用（可能是内置/插件闭包）"
    editable = tool.source == "dynamic" and "ai_tool" in tool.tags
    return {"success": True, "data": {"name": tool_name, "source": src, "editable": editable}}


@router.get("/ai")
async def list_ai_tools():
    """列出所有 AI 自创工具（dynamic + ai_tool 标签），含源码 code 供编辑回填"""
    all_tools = ToolRegistry.list_tools()
    ai_tools = {
        name: info
        for name, info in all_tools.items()
        if info.get("source") == "dynamic" and "ai_tool" in info.get("tags", [])
    }
    try:
        from pathlib import Path
        import json
        p = Path(__file__).resolve().parents[2] / "data" / "ai_tools.json"
        if p.exists():
            persisted = json.loads(p.read_text(encoding="utf-8")) or {}
            for name in ai_tools:
                rec = persisted.get(name)
                if rec and isinstance(rec, dict):
                    ai_tools[name]["code"] = rec.get("code", "")
    except Exception:
        pass
    return {"success": True, "data": {"tools": ai_tools, "count": len(ai_tools)}}


@router.post("/ai")
async def create_ai_tool(body: dict):
    """创建 AI 自定义工具（提交 Python 函数代码动态注册）"""
    from infra.tool_manager.tools.ai_tools import create_tool
    result = create_tool(
        tool_name=str(body.get("tool_name") or ""),
        description=str(body.get("description") or ""),
        code=str(body.get("code") or ""),
        params=body.get("params") or "",
    )
    if str(result).startswith("❌"):
        return {"success": False, "error": {"code": "TOOL_CREATE_ERROR", "message": result}}
    return {"success": True, "data": {"message": result}}


@router.put("/ai/{tool_name}")
async def edit_ai_tool(tool_name: str, body: dict):
    """编辑 AI 自定义工具"""
    from infra.tool_manager.tools.ai_tools import edit_tool
    result = edit_tool(
        tool_name=tool_name,
        description=body.get("description"),
        code=body.get("code"),
        params=body.get("params"),
    )
    if str(result).startswith("❌"):
        return {"success": False, "error": {"code": "TOOL_EDIT_ERROR", "message": result}}
    return {"success": True, "data": {"message": result}}


@router.delete("/ai/{tool_name}")
async def delete_ai_tool(tool_name: str):
    """删除 AI 自定义工具"""
    from infra.tool_manager.tools.ai_tools import delete_tool
    result = delete_tool(tool_name=tool_name)
    if str(result).startswith("❌"):
        return {"success": False, "error": {"code": "TOOL_DELETE_ERROR", "message": result}}
    return {"success": True, "data": {"message": result}}


@router.post("/register", dependencies=[Depends(require_tool_auth)])
async def register_tool(
    name: str = Body(...),
    description: str = Body(default=""),
    params: Dict[str, str] = Body(default={})
):
    """手动注册工具"""
    raise AppError(ErrorCode.NOT_IMPLEMENTED, "请使用 @ToolRegistry.register 装饰器")



@router.get("/plugins/loaded")
async def get_loaded_plugins():
    """获取已加载工具的插件列表"""
    plugins = ToolRegistry.get_plugins()
    return {"success": True, "data": {"plugins": plugins, "count": len(plugins)}}


@router.get("/info/{tool_name}")
async def get_tool_info(tool_name: str = Path(description="工具名称")):
    """获取工具详情"""
    info = tool_manager.get_tool_info(tool_name)

    if not info:
        raise AppError(ErrorCode.NOT_FOUND, f"工具不存在: {tool_name}")

    return {"success": True, "data": info}

