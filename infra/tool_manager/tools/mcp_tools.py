"""
MCP 协议工具 — 发现、调用、注册 MCP 服务器工具
"""
from typing import Dict, Any, Optional

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("mcp_tools")


@ToolRegistry.register("mcp_discover", description="发现可用的 MCP 服务器和其提供的工具。返回所有已注册的 MCP 工具列表。", params={}, risk_level="LOW", category="query")
def mcp_discover() -> Dict[str, Any]:
    """发现可用 MCP 服务器"""
    try:
        from infra.mcp.factory import get_mcp_tool_service
        service = get_mcp_tool_service()
        if not service:
            return {"success": True, "servers": [], "total_tools": 0}
        tools = service.list_tools()
        servers = {}
        for name, spec in tools.items():
            server_name = getattr(spec, 'source', 'builtin') or 'builtin'
            servers.setdefault(server_name, []).append(name)

        result = []
        for server, tool_list in sorted(servers.items()):
            result.append({"server": server, "tools": sorted(tool_list), "count": len(tool_list)})
        return {"success": True, "servers": result, "total_tools": sum(s["count"] for s in result)}
    except Exception as e:
        logger.warning(f"MCP 发现失败: {e}")
        return {"success": True, "servers": [], "total_tools": 0, "note": "MCP 服务未完全初始化"}


@ToolRegistry.register("mcp_call_tool", description="调用 MCP 服务器提供的工具。需要指定服务器名和工具名。", params={
    "server": "MCP 服务器名称",
    "tool": "要调用的工具名",
    "params": "可选，工具参数（JSON 格式字符串）",
}, risk_level="MEDIUM", category="admin")
def mcp_call_tool(server: str, tool: str, params: Optional[str] = None) -> Dict[str, Any]:
    """调用 MCP 工具"""
    if not tool: return {"error": "工具名不能为空"}
    try:
        from infra.mcp.factory import get_mcp_tool_service
        from infra.mcp.types import ToolCallRequest
        import json

        service = get_mcp_tool_service()
        if not service:
            return {"error": "MCP 服务未初始化"}

        args = {}
        if params:
            try: args = json.loads(params)
            except json.JSONDecodeError: return {"error": "params 不是有效的 JSON"}

        request = ToolCallRequest(tool_name=tool, params=args, caller_role="expert", caller_model_id="", source="mcp_tool")
        result = service.execute(request)
        return {"success": result.success, "result": str(result.result) if result.result is not None else "(空)", "error": result.error if not result.success else ""}
    except Exception as e:
        return {"error": f"MCP 调用失败: {e}"}


@ToolRegistry.register("mcp_register", description="注册一个工具为 MCP 服务。将当前系统的工具对外暴露为 MCP 端点。", params={
    "tool_name": "要注册的工具名",
    "server_name": "MCP 服务器名称",
}, risk_level="MEDIUM", category="admin")
def mcp_register(tool_name: str, server_name: str = "ai_backend") -> Dict[str, Any]:
    """注册工具为 MCP 服务"""
    if not tool_name: return {"error": "工具名不能为空"}
    try:
        from infra.tool_manager.tool_registry import ToolRegistry as TR
        tool = TR.get_tool(tool_name)
        if not tool:
            return {"error": f"工具 '{tool_name}' 不存在"}
        return {"success": True, "tool": tool_name, "server": server_name, "status": "已注册（MCP 服务通过 get_tools_for_api 暴露）"}
    except Exception as e:
        return {"error": str(e)}
