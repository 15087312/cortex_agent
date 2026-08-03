"""
MCP 传输层 — 连接外部 MCP server 并通信

支持两种传输方式：
1. stdio: 启动子进程并通过 stdin/stdout 通信（本地 MCP server）
2. SSE: 通过 HTTP SSE 连接远程 MCP server

使用 mcp Python SDK 实现。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, Dict, List
from dataclasses import dataclass, field

from utils.logger import setup_logger

logger = setup_logger("mcp_transport")


@dataclass
class MCPToolDef:
    """MCP server 返回的工具定义"""
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    server_name: str = ""


class MCPStdioTransport:
    """通过 stdio 连接 MCP server（本地子进程）"""

    def __init__(self, server_name: str, command: str, args: List[str] = None,
                 env: Dict[str, str] = None, timeout: float = 30.0):
        self.server_name = server_name
        self._command = command
        self._args = args or []
        self._env = env or {}
        self._timeout = timeout
        self._session = None
        self._stdio_ctx = None
        self._session_ctx = None
        self._tools_cache: List[MCPToolDef] = []
        self._connected = False
        self._loop = None

    def _submit_on_loop(self, coro_factory) -> Any:
        """把 session 调用提交回其绑定的常驻事件循环。

        MCP ClientSession 的读循环/响应 Future 绑定在 connect() 时的事件循环，
        若在其它线程的新 loop 里 await 会跨事件循环挂起。这里用
        run_coroutine_threadsafe 把调用送回原 loop，并同步等待结果。
        """
        if self._loop is None:
            raise RuntimeError(f"MCP server {self.server_name} 未记录事件循环")
        if self._loop.is_closed():
            raise RuntimeError(f"MCP server {self.server_name} 事件循环已关闭")
        fut = asyncio.run_coroutine_threadsafe(coro_factory(), self._loop)
        return fut.result(timeout=self._timeout)

    async def connect(self) -> bool:
        """启动子进程并建立 MCP 连接，保持 session 活跃直到 close()"""
        try:
            from mcp.client.stdio import stdio_client
            from mcp import StdioServerParameters, ClientSession

            self._loop = asyncio.get_running_loop()
            params = StdioServerParameters(
                command=self._command,
                args=self._args,
                env=self._env or None,
            )
            # 手动管理 async with，不退出 context → session 持续存活
            self._stdio_ctx = stdio_client(params)
            self._read, self._write = await self._stdio_ctx.__aenter__()

            self._session_ctx = ClientSession(self._read, self._write)
            self._session = await self._session_ctx.__aenter__()

            await self._session.initialize()
            self._connected = True

            # 获取工具列表
            tools_result = await self._session.list_tools()
            self._tools_cache = [
                MCPToolDef(
                    name=t.name,
                    description=t.description or "",
                    input_schema=t.inputSchema or {},
                    server_name=self.server_name,
                )
                for t in tools_result.tools
            ]
            logger.info(
                f"[MCP] 连接成功: {self.server_name} "
                f"({len(self._tools_cache)} tools)"
            )
            return True
        except Exception as e:
            logger.error(f"[MCP] 连接失败 {self.server_name}: {e}")
            await self.close()
            return False

    async def list_tools(self) -> List[MCPToolDef]:
        """获取工具列表（优先从缓存返回）"""
        if self._tools_cache:
            return self._tools_cache
        if not self._session:
            logger.warning(f"[MCP] {self.server_name} 未连接")
            return []
        try:
            if asyncio.get_running_loop() is self._loop:
                tools_result = await self._session.list_tools()
            else:
                tools_result = await asyncio.to_thread(
                    self._submit_on_loop,
                    lambda: self._session.list_tools(),
                )
            self._tools_cache = [
                MCPToolDef(
                    name=t.name,
                    description=t.description or "",
                    input_schema=t.inputSchema or {},
                    server_name=self.server_name,
                )
                for t in tools_result.tools
            ]
            return self._tools_cache
        except Exception as e:
            logger.error(f"[MCP] list_tools 失败 {self.server_name}: {e}")
            return self._tools_cache

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any] = None) -> Dict[str, Any]:
        """调用 MCP server 上的工具"""
        if not self._session:
            return {"isError": True, "content": [{"type": "text", "text": f"MCP server {self.server_name} 未连接"}]}
        try:
            if asyncio.get_running_loop() is self._loop:
                result = await self._session.call_tool(tool_name, arguments or {})
            else:
                result = await asyncio.to_thread(
                    self._submit_on_loop,
                    lambda: self._session.call_tool(tool_name, arguments or {}),
                )
            return {
                "isError": getattr(result, "isError", False),
                "content": getattr(result, "content", []),
            }
        except concurrent.futures.TimeoutError:
            logger.error(f"[MCP] call_tool 超时 {self.server_name}/{tool_name}")
            return {"isError": True, "content": [{"type": "text", "text": f"MCP 工具超时 ({self._timeout}s): {tool_name}"}]}
        except Exception as e:
            logger.error(f"[MCP] call_tool 失败 {self.server_name}/{tool_name}: {e}")
            return {"isError": True, "content": [{"type": "text", "text": str(e)}]}

    async def close(self):
        """关闭连接，清理子进程和 session 资源"""
        self._connected = False
        self._tools_cache = []
        try:
            if self._session_ctx:
                if self._loop is None or asyncio.get_running_loop() is self._loop:
                    await self._session_ctx.__aexit__(None, None, None)
                else:
                    await asyncio.to_thread(
                        self._submit_on_loop,
                        lambda: self._session_ctx.__aexit__(None, None, None),
                    )
        except Exception as e:
            logger.debug(f"MCP session 关闭失败 (非致命): {e}")
        try:
            if self._stdio_ctx:
                if self._loop is None or asyncio.get_running_loop() is self._loop:
                    await self._stdio_ctx.__aexit__(None, None, None)
                else:
                    await asyncio.to_thread(
                        self._submit_on_loop,
                        lambda: self._stdio_ctx.__aexit__(None, None, None),
                    )
        except Exception as e:
            logger.debug(f"MCP stdio 关闭失败 (非致命): {e}")
        self._session = None
        self._session_ctx = None
        self._stdio_ctx = None
        logger.info(f"[MCP] 已断开: {self.server_name}")

    @property
    def is_connected(self) -> bool:
        return self._connected


class MCPSseTransport:
    """通过 SSE 连接远程 MCP server"""

    def __init__(self, server_name: str, url: str, timeout: float = 30.0):
        self.server_name = server_name
        self._url = url
        self._timeout = timeout
        self._session = None
        self._sse_ctx = None
        self._session_ctx = None
        self._tools_cache: List[MCPToolDef] = []
        self._connected = False

    def _submit_on_loop(self, coro_factory) -> Any:
        if self._loop is None:
            raise RuntimeError(f"MCP server {self.server_name} 未记录事件循环")
        if self._loop.is_closed():
            raise RuntimeError(f"MCP server {self.server_name} 事件循环已关闭")
        fut = asyncio.run_coroutine_threadsafe(coro_factory(), self._loop)
        return fut.result(timeout=self._timeout)

    async def connect(self) -> bool:
        """连接远程 SSE MCP server"""
        try:
            from mcp.client.sse import sse_client
            from mcp import ClientSession

            self._loop = asyncio.get_running_loop()
            self._sse_ctx = sse_client(url=self._url)
            self._read, self._write = await self._sse_ctx.__aenter__()

            self._session_ctx = ClientSession(self._read, self._write)
            self._session = await self._session_ctx.__aenter__()

            await self._session.initialize()
            self._connected = True

            tools_result = await self._session.list_tools()
            self._tools_cache = [
                MCPToolDef(
                    name=t.name,
                    description=t.description or "",
                    input_schema=t.inputSchema or {},
                    server_name=self.server_name,
                )
                for t in tools_result.tools
            ]
            logger.info(
                f"[MCP-SSE] 连接成功: {self.server_name} "
                f"({len(self._tools_cache)} tools)"
            )
            return True
        except Exception as e:
            logger.error(f"[MCP-SSE] 连接失败 {self.server_name}: {e}")
            await self.close()
            return False

    async def list_tools(self) -> List[MCPToolDef]:
        if self._tools_cache:
            return self._tools_cache
        if not self._session:
            return []
        try:
            if asyncio.get_running_loop() is self._loop:
                tools_result = await self._session.list_tools()
            else:
                tools_result = await asyncio.to_thread(
                    self._submit_on_loop,
                    lambda: self._session.list_tools(),
                )
            self._tools_cache = [
                MCPToolDef(
                    name=t.name,
                    description=t.description or "",
                    input_schema=t.inputSchema or {},
                    server_name=self.server_name,
                )
                for t in tools_result.tools
            ]
            return self._tools_cache
        except Exception as e:
            logger.error(f"[MCP-SSE] list_tools 失败 {self.server_name}: {e}")
            return self._tools_cache

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any] = None) -> Dict[str, Any]:
        if not self._session:
            return {"isError": True, "content": [{"type": "text", "text": f"MCP server {self.server_name} 未连接"}]}
        try:
            if asyncio.get_running_loop() is self._loop:
                result = await self._session.call_tool(tool_name, arguments or {})
            else:
                result = await asyncio.to_thread(
                    self._submit_on_loop,
                    lambda: self._session.call_tool(tool_name, arguments or {}),
                )
            return {
                "isError": getattr(result, "isError", False),
                "content": getattr(result, "content", []),
            }
        except concurrent.futures.TimeoutError:
            logger.error(f"[MCP-SSE] call_tool 超时 {self.server_name}/{tool_name}")
            return {"isError": True, "content": [{"type": "text", "text": f"MCP 工具超时 ({self._timeout}s): {tool_name}"}]}
        except Exception as e:
            logger.error(f"[MCP-SSE] call_tool 失败 {self.server_name}/{tool_name}: {e}")
            return {"isError": True, "content": [{"type": "text", "text": str(e)}]}

    async def close(self):
        self._connected = False
        self._session = None
        self._tools_cache = []
        try:
            if self._session_ctx:
                if self._loop is None or asyncio.get_running_loop() is self._loop:
                    await self._session_ctx.__aexit__(None, None, None)
                else:
                    await asyncio.to_thread(
                        self._submit_on_loop,
                        lambda: self._session_ctx.__aexit__(None, None, None),
                    )
        except Exception as e:
            logger.debug(f"MCP-SSE session 关闭失败 (非致命): {e}")
        try:
            if self._sse_ctx:
                if self._loop is None or asyncio.get_running_loop() is self._loop:
                    await self._sse_ctx.__aexit__(None, None, None)
                else:
                    await asyncio.to_thread(
                        self._submit_on_loop,
                        lambda: self._sse_ctx.__aexit__(None, None, None),
                    )
        except Exception as e:
            logger.debug(f"MCP-SSE 连接关闭失败 (非致命): {e}")
        self._session_ctx = None
        self._sse_ctx = None

    @property
    def is_connected(self) -> bool:
        return self._connected
