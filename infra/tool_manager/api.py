"""
工具 API - 工具管理和调用接口
"""
import os
from fastapi import APIRouter, HTTPException, Body, Path, Query, Header, Depends
from typing import Dict, Any, Optional, List

from infra.tool_manager import tool_manager, ToolRegistry
from api.errors import AppError, ErrorCode

logger = __import__("utils.logger", fromlist=["setup_logger"]).setup_logger("tool_api")

# 统一认证：使用 X-API-Key
from api.auth import require_api_key


async def _security_gate_check(tool_name: str, params: Dict[str, Any], caller_role: str) -> None:
    """执行模式安全门检查，拦截则抛出 HTTPException"""
    from modules.security_system.tool_security_gate import get_tool_security_gate
    gate = get_tool_security_gate()
    allowed, reason = await gate.check(
        tool_name=tool_name,
        tool_params=params,
        caller_tier=caller_role,
        caller_model_id=f"api:{caller_role}",
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


router = APIRouter(prefix="/tools", tags=["工具"], dependencies=[Depends(require_tool_auth)])


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




@router.post("/call")
async def call_tool(
    tool_name: str = Body(..., description="工具名称"),
    params: Dict[str, Any] = Body(default={}, description="工具参数"),
    caller_role: str = Header(default="expert", description="调用者角色")
):
    """调用工具（经过安全门检查）"""
    await _security_gate_check(tool_name, params, caller_role)
    result = await tool_manager.call_tool(tool_name, params, caller_role=caller_role)
    return {"success": True, "data": result}


@router.post("/call-sync")
async def call_tool_sync(
    tool_name: str = Body(..., description="工具名称"),
    params: Dict[str, Any] = Body(default={}, description="工具参数"),
    caller_role: str = Header(default="expert", description="调用者角色")
):
    """同步调用工具（经过安全门检查）"""
    await _security_gate_check(tool_name, params, caller_role)
    result = tool_manager.call_tool_sync(tool_name, params, caller_role=caller_role)
    return {"success": True, "data": result}


@router.post("/call-json")
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


@router.delete("/events")
async def clear_tool_events():
    """清空工具调用历史"""
    cleared = tool_manager.clear_tool_events()
    return {
        "success": True,
        "message": f"已清空 {cleared} 条工具调用记录",
        "data": {"cleared": cleared}
    }


@router.post("/register")
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

