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
    """管理所有 MCP server 连接的生命周期（含自动重连）"""

    def __init__(self, servers: List[MCPServerConfig]):
        self._transports: Dict[str, MCPStdioTransport | MCPSseTransport] = {}
        self._tools_index: Dict[str, MCPToolDef] = {}  # tool_name → tool_def
        self._tool_to_server: Dict[str, str] = {}  # tool_name → server_name
        # 自动重连监控：每个 server 一个后台 asyncio 任务 + 停止事件
        self._watch_tasks: Dict[str, asyncio.Task] = {}
        self._stop_events: Dict[str, asyncio.Event] = {}
        self._configs: Dict[str, MCPServerConfig] = {}
        for cfg in servers:
            if not cfg.enabled:
                continue
            self._configs[cfg.name] = cfg
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
                cfg = self._configs.get(name)
                if cfg:
                    self._start_reconnect_watch(name, cfg)
        if count:
            logger.info(f"[MCP] {count}/{len(self._transports)} server(s) 已连接, {len(self._tools_index)} tools")
        return count

    # ── 自动重连（断线指数退避，等价 dsh startConnection）──

    def _start_reconnect_watch(self, name: str, cfg) -> None:
        """连接成功后启动断线监控任务（cfg.reconnect 时）。"""
        if not cfg.reconnect:
            return
        if name in self._watch_tasks and not self._watch_tasks[name].done():
            return  # 已有监控
        self._stop_events[name] = asyncio.Event()
        self._watch_tasks[name] = asyncio.create_task(
            self._watch_connection(name, cfg)
        )
        logger.info(f"[MCP] 已启动断线自动重连监控: {name}")

    async def _watch_connection(self, name: str, cfg) -> None:
        """后台监控：周期性检查连接，断线则指数退避重连 + 刷新工具索引。"""
        stop = self._stop_events.get(name)
        if stop is None:
            return
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=cfg.reconnect_interval)
                break  # 收到停止信号
            except asyncio.TimeoutError:
                pass  # 轮询间隔到，检查连接
            t = self._transports.get(name)
            if t is None:
                break
            if not t.is_connected:
                if await self._reconnect_with_backoff(name, cfg):
                    await self._refresh_tools(name)
                else:
                    logger.warning(f"[MCP] {name} 重连失败（{cfg.reconnect_max_retries} 次），等待下轮")

    async def _reconnect_with_backoff(self, name: str, cfg) -> bool:
        """指数退避重连：base_delay × 2^(attempt-1)。"""
        t = self._transports.get(name)
        if t is None:
            return False
        for attempt in range(1, cfg.reconnect_max_retries + 1):
            stop = self._stop_events.get(name)
            if stop is not None and stop.is_set():
                return False
            try:
                ok = await t.connect()
                if ok:
                    return True
            except Exception as e:
                logger.debug(f"[MCP] {name} 重连异常: {e}")
            if attempt < cfg.reconnect_max_retries:
                await asyncio.sleep(cfg.reconnect_base_delay * (2 ** (attempt - 1)))
        return False

    async def _refresh_tools(self, name: str) -> None:
        """重连成功后刷新工具索引：先摘旧工具，再重新 list_tools。"""
        for tname in [t for t, s in self._tool_to_server.items() if s == name]:
            self._tool_to_server.pop(tname, None)
            self._tools_index.pop(tname, None)
        t = self._transports.get(name)
        if t:
            try:
                tools = await t.list_tools()
                for tool in tools:
                    self._tools_index[tool.name] = tool
                    self._tool_to_server[tool.name] = name
                logger.info(f"[MCP] {name} 重连成功，工具已刷新 ({len(tools)})")
            except Exception as e:
                logger.warning(f"[MCP] {name} 重连后 list_tools 失败: {e}")

    async def _stop_reconnect_watch(self, name: str) -> None:
        """停止指定 server 的重连监控（remove/关闭时调用，防后台任务遗留）。"""
        stop = self._stop_events.pop(name, None)
        if stop:
            stop.set()
        task = self._watch_tasks.pop(name, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _stop_all_reconnect_watches(self) -> None:
        for name in list(self._watch_tasks):
            await self._stop_reconnect_watch(name)

    async def add_server(self, name: str, command: str,
                         args: list = None, env: dict = None,
                         url: str = "",
                         *, reconnect: bool = False,
                         reconnect_interval: float = 5.0,
                         reconnect_max_retries: int = 5,
                         reconnect_base_delay: float = 0.5) -> bool:
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
            # 动态添加也支持自动重连
            cfg = MCPServerConfig(
                name=name, command=command or "", args=args or [], env=env or {},
                enabled=True, reconnect=reconnect,
                reconnect_interval=reconnect_interval,
                reconnect_max_retries=reconnect_max_retries,
                reconnect_base_delay=reconnect_base_delay,
            )
            self._configs[name] = cfg
            self._start_reconnect_watch(name, cfg)
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

    async def remove_server(self, name: str) -> bool:
        """移除单个 MCP server（独立热卸载）。

        逆序清理：
          1. 先摘除该 server 的工具（模型立刻看不到，避免调用半死工具）
          2. 关闭连接（transport.close 内部终止子进程）
          3. 清理索引
        用于运行时停用单个 MCP server，不影响其他 server。
        """
        if name not in self._transports:
            logger.warning(f"[MCP] server {name} 不存在，无法移除")
            return False

        # 1. 摘除该 server 的工具（列表推导避免迭代中修改 dict）
        for tname in [t for t, s in self._tool_to_server.items() if s == name]:
            self._tool_to_server.pop(tname, None)
            self._tools_index.pop(tname, None)

        # 2. 停止自动重连监控（防后台任务遗留）
        await self._stop_reconnect_watch(name)

        # 3. 关闭连接并清理索引
        transport = self._transports.pop(name, None)
        if transport:
            await transport.close()
        self._configs.pop(name, None)
        logger.info(f"[MCP] 已移除 server: {name}")
        return True

    async def replace_server(self, name: str, command: str = None,
                             args: list = None, env: dict = None,
                             url: str = "") -> bool:
        """热替换 server：先卸载旧的（摘工具+断开），再按新配置添加。

        等价于 dsh mcp-client 的 HMR（dispose 旧实例 + 创建新实例）。
        """
        await self.remove_server(name)
        return await self.add_server(name, command, args, env, url)

    async def shutdown(self):
        """关闭所有连接（先停止全部重连监控，避免后台任务遗留）"""
        await self._stop_all_reconnect_watches()
        for name, transport in self._transports.items():
            await transport.close()
        self._transports.clear()
        self._tools_index.clear()
        self._tool_to_server.clear()
        self._configs.clear()
        logger.info("[MCP] 所有 server 连接已关闭")
