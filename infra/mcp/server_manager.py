"""
MCP Server 生命周期管理器

从 MCP_SERVERS 配置读取 server 定义，启动连接，维护状态，支持健康检查。
"""
from __future__ import annotations

import asyncio
from typing import Dict, List, Optional
from .transport import MCPStdioTransport, MCPSseTransport, MCPToolDef
from .types import MCPServerConfig
from utils.logger import setup_logger

logger = setup_logger("mcp_server_manager")


class MCPServerManager:
    """管理所有 MCP server 连接的生命周期"""

    def __init__(self, servers: List[MCPServerConfig]):
        self._transports: Dict[str, MCPStdioTransport | MCPSseTransport] = {}
        self._tools_index: Dict[str, MCPToolDef] = {}  # tool_name → tool_def
        self._tool_to_server: Dict[str, str] = {}  # tool_name → server_name
        for cfg in servers:
            if not cfg.enabled:
                continue
            if cfg.command:
                transport = MCPStdioTransport(
                    server_name=cfg.name,
                    command=cfg.command,
                    args=cfg.args,
                    env=cfg.env,
                    timeout=cfg.timeout_seconds,
                )
            else:
                # 无 command 但有 url 时使用 SSE（由调用方通过 env 传入）
                url = cfg.env.get("url", "")
                transport = MCPSseTransport(
                    server_name=cfg.name,
                    url=url,
                    timeout=cfg.timeout_seconds,
                ) if url else None
            if transport:
                self._transports[cfg.name] = transport

    async def start_all(self) -> int:
        """启动所有已配置的 MCP server 连接"""
        count = 0
        for name, transport in self._transports.items():
            ok = await transport.connect()
            if ok:
                tools = await transport.list_tools()
                for tool in tools:
                    self._tools_index[tool.name] = tool
                    self._tool_to_server[tool.name] = name
                count += 1
        if count:
            logger.info(f"[MCP] {count}/{len(self._transports)} server(s) 已连接, {len(self._tools_index)} tools")
        return count

    async def add_server(self, name: str, command: str,
                         args: list = None, env: dict = None,
                         url: str = "") -> bool:
        """动态添加并连接新的 MCP server"""
        if name in self._transports:
            logger.warning(f"[MCP] server {name} 已存在，跳过")
            return False

        if command:
            from .transport import MCPStdioTransport
            transport = MCPStdioTransport(
                server_name=name, command=command,
                args=args or [], env=env or {},
            )
        elif url:
            from .transport import MCPSseTransport
            transport = MCPSseTransport(server_name=name, url=url)
        else:
            logger.error(f"[MCP] 添加 server {name} 失败: 需要 command 或 url")
            return False

        self._transports[name] = transport
        ok = await transport.connect()
        if ok:
            tools = await transport.list_tools()
            for tool in tools:
                self._tools_index[tool.name] = tool
                self._tool_to_server[tool.name] = name
            logger.info(f"[MCP] 动态添加 server: {name} ({len(tools)} tools)")
        else:
            self._transports.pop(name, None)
        return ok

    def get_all_tools(self) -> Dict[str, MCPToolDef]:
        """获取所有 MCP server 暴露的工具"""
        return dict(self._tools_index)

    def get_tool(self, name: str) -> Optional[MCPToolDef]:
        """按名查找工具"""
        return self._tools_index.get(name)

    def get_server_for_tool(self, tool_name: str) -> Optional[str]:
        """返回工具对应的 server 名"""
        return self._tool_to_server.get(tool_name)

    async def call_tool(self, tool_name: str, arguments: Dict = None) -> Dict:
        """调用指定工具"""
        server_name = self._tool_to_server.get(tool_name)
        if not server_name:
            return {"isError": True, "content": [{"type": "text", "text": f"工具 {tool_name} 不属于任何 MCP server"}]}
        transport = self._transports.get(server_name)
        if not transport:
            return {"isError": True, "content": [{"type": "text", "text": f"MCP server {server_name} 不在运行"}]}
        return await transport.call_tool(tool_name, arguments)

    def get_server_status(self) -> List[Dict]:
        """获取所有 server 状态"""
        return [
            {
                "name": name,
                "connected": t.is_connected,
                "tools_count": sum(1 for ti in self._tools_index.values()
                                   if self._tool_to_server.get(ti.name) == name),
            }
            for name, t in self._transports.items()
        ]

    async def shutdown(self):
        """关闭所有连接"""
        for name, transport in self._transports.items():
            await transport.close()
        self._transports.clear()
        self._tools_index.clear()
        self._tool_to_server.clear()
        logger.info("[MCP] 所有 server 连接已关闭")
