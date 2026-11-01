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
from typing import Any, Dict, List, Optional
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


def _new_http_client(headers: Dict[str, str]):
    """构造带默认头的 HTTP 客户端（mcp 2.x 用 httpx2，老环境退回标准 httpx）"""
    import importlib
    for module_name in ("httpx2", "httpx"):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        try:
            return module.AsyncClient(headers=dict(headers))
        except Exception as e:
            logger.debug(f"[MCP-HTTP] 构造 {module_name} 客户端失败: {e}")
    return None


def _open_streamable_http(url: str, headers: Optional[Dict[str, str]] = None):
    """打开 StreamableHTTP 传输上下文（兼容 mcp 1.x / 2.x 两套 API）。

    - mcp 1.x：`streamablehttp_client(url, headers=...)`，__aenter__ 返回 3 元组
      `(read, write, get_session_id)`；
    - mcp 2.x：改名 `streamable_http_client(url, http_client=...)`，不再有 headers
      形参（头部要挂在自带的 HTTP 客户端上），__aenter__ 只返回 `(read, write)`。

    返回 `(ctx, http_client)`：2.x 不会替我们关闭传入的客户端，需调用方在 close 时关。
    """
    try:
        from mcp.client.streamable_http import streamable_http_client
    except ImportError:  # mcp 1.x
        from mcp.client.streamable_http import streamablehttp_client
        return streamablehttp_client(url=url, headers=headers or None), None

    if not headers:
        return streamable_http_client(url=url), None
    client = _new_http_client(headers)
    if client is None:
        logger.warning("[MCP-HTTP] 未找到 httpx/httpx2，headers 无法附加（鉴权将缺失）")
        return streamable_http_client(url=url), None
    return streamable_http_client(url=url, http_client=client), client


class MCPStdioTransport:
    """通过 stdio 连接 MCP server（本地子进程）"""

    def __init__(self, server_name: str, command: str, args: List[str] = None,
                 env: Dict[str, str] = None, timeout: float = 30.0):
        self.server_name = server_name
        self._command = command
        self._args = args or []
        self._env = env or {}
        self._timeout = timeout
        self._session: Any = None
        self._stdio_ctx: Any = None
        self._session_ctx: Any = None
        self._tools_cache: List[MCPToolDef] = []
        self._connected = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # @link:block StdioSubmit = Guard · Retrieve
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

    # @link:block StdioConnect
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

    # @link:block StdioList
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

    # @link:block StdioCall
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

    # @link:block StdioClose = Guard · Retrieve
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
        self._session: Any = None
        self._sse_ctx: Any = None
        self._session_ctx: Any = None
        self._tools_cache: List[MCPToolDef] = []
        self._connected = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _submit_on_loop(self, coro_factory) -> Any:
        if self._loop is None:
            raise RuntimeError(f"MCP server {self.server_name} 未记录事件循环")
        if self._loop.is_closed():
            raise RuntimeError(f"MCP server {self.server_name} 事件循环已关闭")
        fut = asyncio.run_coroutine_threadsafe(coro_factory(), self._loop)
        return fut.result(timeout=self._timeout)

    # @link:block SseConnect
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

    # @link:block SseList
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

    # @link:block SseCall
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

    # @link:block SseClose = Guard · Retrieve
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


class MCPStreamableHttpTransport:
    """通过 StreamableHTTP 连接远程 MCP server（抄 Cherry：新标准，SSE 后继）

    与 SSE 的区别：单端点双向流，支持 405/404 时自动降级到 SSE。
    """

    def __init__(self, server_name: str, url: str, headers: Dict[str, str] = None,
                 timeout: float = 30.0):
        self.server_name = server_name
        self._url = url
        self._headers = headers or {}
        self._timeout = timeout
        self._session: Any = None
        self._ctx: Any = None
        self._session_ctx: Any = None
        self._http_client: Any = None   # mcp 2.x：headers 挂在自带 httpx 客户端上，close 时要关
        self._tools_cache: List[MCPToolDef] = []
        self._connected = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _submit_on_loop(self, coro_factory) -> Any:
        if self._loop is None:
            raise RuntimeError(f"MCP server {self.server_name} 未记录事件循环")
        if self._loop.is_closed():
            raise RuntimeError(f"MCP server {self.server_name} 事件循环已关闭")
        fut = asyncio.run_coroutine_threadsafe(coro_factory(), self._loop)
        return fut.result(timeout=self._timeout)

    async def connect(self) -> bool:
        """连接 StreamableHTTP MCP server，失败时尝试 SSE 降级"""
        try:
            from mcp import ClientSession

            self._loop = asyncio.get_running_loop()
            self._ctx, self._http_client = _open_streamable_http(self._url, self._headers)
            # 1.x 返回 3 元组（带 get_session_id），2.x 只有 2 元组
            self._read, self._write, *_ = await self._ctx.__aenter__()

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
                f"[MCP-HTTP] {self.server_name} 已连接，工具数={len(self._tools_cache)}"
            )
            return True
        except Exception as e:
            logger.warning(f"[MCP-HTTP] {self.server_name} 连接失败，尝试 SSE 降级: {e}")
            # 抄 Cherry getTransportCandidates：405/404 时自动切 SSE
            try:
                from mcp.client.sse import sse_client
                from mcp import ClientSession

                self._ctx = sse_client(url=self._url, headers=self._headers or None)
                self._read, self._write = await self._ctx.__aenter__()
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
                logger.info(f"[MCP-HTTP→SSE] {self.server_name} 降级成功，工具数={len(self._tools_cache)}")
                return True
            except Exception as e2:
                logger.error(f"[MCP-HTTP] {self.server_name} 连接+降级均失败: {e2}")
                return False

    async def list_tools(self) -> List[MCPToolDef]:
        if not self._connected:
            ok = await self.connect()
            if not ok:
                return []
        return list(self._tools_cache)

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any] = None) -> Dict[str, Any]:
        import concurrent.futures

        def _do():
            async def _call():
                result = await self._session.call_tool(tool_name, arguments or {})
                return {
                    "isError": getattr(result, "isError", False),
                    "content": [
                        {"type": getattr(c, "type", "text"), "text": getattr(c, "text", str(c))}
                        for c in (result.content or [])
                    ],
                }
            return _call()
        try:
            return self._submit_on_loop(_do)
        except concurrent.futures.TimeoutError:
            logger.error(f"[MCP-HTTP] call_tool 超时 {self.server_name}/{tool_name}")
            return {"isError": True, "content": [{"type": "text", "text": f"MCP 工具超时 ({self._timeout}s): {tool_name}"}]}
        except Exception as e:
            logger.error(f"[MCP-HTTP] call_tool 失败 {self.server_name}/{tool_name}: {e}")
            return {"isError": True, "content": [{"type": "text", "text": str(e)}]}

    async def close(self):
        self._connected = False
        self._session = None
        self._tools_cache = []
        try:
            if self._session_ctx:
                await self._session_ctx.__aexit__(None, None, None)
        except Exception as _swallowed_err:
            logger.debug(f"[close] ignored Exception: {_swallowed_err}")
        try:
            if self._ctx:
                await self._ctx.__aexit__(None, None, None)
        except Exception as _swallowed_err:
            logger.debug(f"[close] ignored Exception: {_swallowed_err}")
        try:
            # mcp 2.x 不会关闭外部传入的 http_client → 自己关，避免连接泄漏
            if self._http_client is not None:
                await self._http_client.aclose()
        except Exception as _swallowed_err:
            logger.debug(f"[close] 关闭 http client 失败: {_swallowed_err}")
        self._session_ctx = None
        self._ctx = None
        self._http_client = None

    @property
    def is_connected(self) -> bool:
        return self._connected
