"""
MCP 协议工具 — 发现、调用已注册的 MCP server 工具

依赖的 MCP server 通过 MCP_SERVERS 环境变量配置。
"""
from typing import Dict, Any, Optional

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("mcp_tools")


@ToolRegistry.register("mcp_discover", description="发现所有可用的 MCP 服务器和其提供的工具。返回已连接的外部 MCP 服务器列表及各服务器的工具。", params={}, risk_level="LOW", category="query")
def mcp_discover() -> Dict[str, Any]:
    """发现已连接的 MCP 服务器"""
    try:
        from infra.mcp.factory import get_server_manager
        mgr = get_server_manager()
        status = mgr.get_server_status()
        tools = mgr.get_all_tools()

        servers = []
        for s in status:
            server_tools = [name for name, t in tools.items() if t.server_name == s["name"]]
            servers.append({
                "name": s["name"],
                "connected": s["connected"],
                "tools": sorted(server_tools),
                "count": len(server_tools),
            })

        return {
            "success": True,
            "servers": servers,
            "total_servers": len(servers),
            "total_tools": len(tools),
        }
    except Exception as e:
        logger.warning(f"MCP 发现失败: {e}")
        return {"success": True, "servers": [], "total_servers": 0, "total_tools": 0}


@ToolRegistry.register("mcp_call_tool", description="调用 MCP 服务器提供的远程工具。需要先通过 mcp_discover 查看可用的 MCP 服务器和工具列表。", params={
    "tool": "要调用的工具名",
    "params": "可选，工具参数（JSON 格式字符串）",
}, risk_level="MEDIUM", category="admin")
def mcp_call_tool(tool: str, params: Optional[str] = None) -> Dict[str, Any]:
    """调用 MCP 远程工具"""
    if not tool:
        return {"success": False, "error": "工具名不能为空"}
    try:
        from infra.mcp.factory import get_server_manager
        import json

        mgr = get_server_manager()
        args = {}
        if params:
            try:
                args = json.loads(params)
            except json.JSONDecodeError:
                return {"success": False, "error": "params 不是有效的 JSON"}

        result = mgr.call_tool(tool, args)

        # 格式化返回内容
        content_text = ""
        for item in result.get("content", []):
            if item.get("type") == "text":
                content_text += item.get("text", "")

        is_error = result.get("isError", False)
        return {
            "success": not is_error,
            "result": content_text,
            "error": content_text if is_error else "",
        }
    except Exception as e:
        return {"success": False, "error": f"MCP 调用失败: {e}"}


@ToolRegistry.register("mcp_server_status", description="查看所有已配置的 MCP 服务器连接状态。", params={}, risk_level="LOW", category="query")
def mcp_server_status() -> Dict[str, Any]:
    """查看 MCP 服务器连接状态"""
    try:
        from infra.mcp.factory import get_server_manager
        mgr = get_server_manager()
        status = mgr.get_server_status()
        return {
            "success": True,
            "servers": status,
            "total": len(status),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@ToolRegistry.register("mcp_register_server", description="动态安装并启动一个新的 MCP 服务器。输入命令后自动安装（如 npx 下载 npm 包）并连接，连接后工具立即可用。需要指定服务器名和启动命令。", params={
    "name": "服务器名称，如 filesystem、sqlite",
    "command": "启动命令，如 npx -y @modelcontextprotocol/server-filesystem ./",
}, risk_level="MEDIUM", category="admin")
async def mcp_register_server(name: str, command: str) -> Dict[str, Any]:
    """动态注册一个新的 MCP server"""
    import shlex
    parts = shlex.split(command)
    if not parts:
        return {"success": False, "error": "command 不能为空"}

    try:
        from infra.mcp.factory import get_server_manager
        mgr = get_server_manager()

        cmd = parts[0]
        args = parts[1:]

        # 通过 add_server 异步连接
        import asyncio
        ok = await mgr.add_server(name=name, command=cmd, args=args)

        if ok:
            tools = mgr.get_all_tools()
            server_tools = [t for t_name, t in tools.items() if mgr.get_server_for_tool(t_name) == name]
            return {
                "success": True,
                "message": f"MCP server「{name}」已启动并连接",
                "tools_count": len(server_tools),
                "hint": f"使用 mcp_discover 查看可用工具，使用 mcp_call_tool 调用",
            }
        else:
            return {"success": False, "error": f"MCP server「{name}」连接失败，请检查命令是否正确"}
    except Exception as e:
        return {"success": False, "error": str(e)}
