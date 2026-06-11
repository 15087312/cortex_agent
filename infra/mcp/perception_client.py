"""
MCP 感知客户端 — 通过 MCP 协议获取外部资源数据

封装 MCP ClientSession，提供：
- 连接指定 MCP server（stdio/SSE）
- 列出可用资源（list_resources）
- 读取资源内容（read_resource）
- 订阅资源变更（subscribe_resource）
- 资源变更回调 → 注入感知系统事件
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field

from utils.logger import setup_logger

logger = setup_logger("mcp_perception_client")


@dataclass
class MCPResource:
    """MCP server 暴露的资源"""
    uri: str
    mime_type: str = ""
    description: str = ""
    server_name: str = ""


class MCPPerceptionClient:
    """连接 MCP server 获取上下文资源

    生命周期：connect() → 交互 → close()
    回调：on_resource_update(uri, content) 在资源变更时调用
    """

    def __init__(self, server_name: str,
                 command: str = "", args: List[str] = None,
                 url: str = "",
                 env: Dict[str, str] = None,
                 timeout: float = 30.0):
        self.server_name = server_name
        self._command = command
        self._args = args or []
        self._url = url
        self._env = env or {}
        self._timeout = timeout

        self._session = None
        self._ctx_managers = []  # 用于清理
        self._connected = False
        self._resources_cache: List[MCPResource] = []
        self._on_update: Optional[Callable[[str, str], None]] = None
        self._subscribed_uris: set = set()

    def set_on_update(self, callback: Callable[[str, str], None]):
        """设置资源更新回调"""
        self._on_update = callback

    async def connect(self) -> bool:
        """连接 MCP server（stdio 优先，回退 SSE）"""
        try:
            if self._command:
                return await self._connect_stdio()
            elif self._url:
                return await self._connect_sse()
            else:
                logger.warning(f"[MCP感知] {self.server_name} 未指定 command 或 url")
                return False
        except Exception as e:
            logger.error(f"[MCP感知] 连接失败 {self.server_name}: {e}")
            await self.close()
            return False

    async def _connect_stdio(self) -> bool:
        from mcp.client.stdio import stdio_client
        from mcp import StdioServerParameters, ClientSession

        params = StdioServerParameters(
            command=self._command, args=self._args, env=self._env or None,
        )
        ctx = stdio_client(params)
        self._ctx_managers.append(ctx)
        read, write = await ctx.__aenter__()

        sess_ctx = ClientSession(read, write)
        self._ctx_managers.append(sess_ctx)
        self._session = await sess_ctx.__aenter__()

        await self._session.initialize()
        self._connected = True
        await self._cache_resources()
        logger.info(f"[MCP感知] {self.server_name} stdio 已连接 ({len(self._resources_cache)} resources)")
        return True

    async def _connect_sse(self) -> bool:
        from mcp.client.sse import sse_client
        from mcp import ClientSession

        ctx = sse_client(url=self._url)
        self._ctx_managers.append(ctx)
        read, write = await ctx.__aenter__()

        sess_ctx = ClientSession(read, write)
        self._ctx_managers.append(sess_ctx)
        self._session = await sess_ctx.__aenter__()

        await self._session.initialize()
        self._connected = True
        await self._cache_resources()
        logger.info(f"[MCP感知] {self.server_name} SSE 已连接 ({len(self._resources_cache)} resources)")
        return True

    async def _cache_resources(self):
        """缓存 server 的资源列表"""
        if not self._session:
            return
        try:
            result = await self._session.list_resources()
            self._resources_cache = [
                MCPResource(
                    uri=r.uri,
                    mime_type=getattr(r, "mimeType", "") or "",
                    description=getattr(r, "description", "") or "",
                    server_name=self.server_name,
                )
                for r in getattr(result, "resources", [])
            ]
        except Exception as e:
            logger.debug(f"[MCP感知] list_resources 失败 (非致命): {e}")

    async def list_resources(self) -> List[MCPResource]:
        """列出可用资源"""
        if self._resources_cache:
            return self._resources_cache
        await self._cache_resources()
        return self._resources_cache

    async def read_resource(self, uri: str) -> Optional[str]:
        """读取资源内容"""
        if not self._session:
            return None
        try:
            result = await self._session.read_resource(uri)
            # 返回文本内容
            texts = []
            for content in getattr(result, "contents", []):
                if hasattr(content, "text"):
                    texts.append(content.text)
            return "\n".join(texts) if texts else None
        except Exception as e:
            logger.error(f"[MCP感知] read_resource 失败 {uri}: {e}")
            return None

    async def subscribe_resource(self, uri: str) -> bool:
        """订阅资源变更"""
        if not self._session or uri in self._subscribed_uris:
            return False
        try:
            await self._session.subscribe_resource(uri)
            self._subscribed_uris.add(uri)
            logger.info(f"[MCP感知] 已订阅: {uri}")

            # 启动监听任务（使用 server 的 session 接收变更通知）
            asyncio.create_task(self._listen_resource_updates(uri))
            return True
        except Exception as e:
            logger.debug(f"[MCP感知] subscribe_resource 失败 (非致命): {e}")
            return False

    async def _listen_resource_updates(self, uri: str):
        """监听资源变更"""
        try:
            async for update in self._session.inbox():
                if self._on_update:
                    content = await self.read_resource(uri)
                    if content is not None:
                        self._on_update(uri, content)
        except Exception as e:
            logger.debug(f"[MCP感知] 监听 {uri} 结束: {e}")

    async def close(self):
        """断开连接"""
        self._connected = False
        self._session = None
        self._resources_cache = []
        self._subscribed_uris.clear()
        for ctx in reversed(self._ctx_managers):
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                pass
        self._ctx_managers.clear()
        logger.info(f"[MCP感知] 已断开: {self.server_name}")

    @property
    def is_connected(self) -> bool:
        return self._connected


class MCPPerceptionClientManager:
    """管理多个 MCP 感知客户端的连接与资源聚合"""

    def __init__(self):
        self._clients: Dict[str, MCPPerceptionClient] = {}
        self._resources_index: Dict[str, str] = {}  # uri → server_name
        self._on_resource_update: Optional[Callable[[str, str, str], None]] = None

    def set_on_update(self, callback: Callable[[str, str, str], None]):
        """设置资源更新回调 (server_name, uri, content)"""
        self._on_resource_update = callback

    def add_client(self, client: MCPPerceptionClient):
        """注册客户端"""
        self._clients[client.server_name] = client

        def _on_update(uri: str, content: str):
            if self._on_resource_update:
                self._on_resource_update(client.server_name, uri, content)

        client.set_on_update(_on_update)

    async def connect_all(self) -> int:
        """连接所有已注册客户端"""
        count = 0
        for name, client in self._clients.items():
            ok = await client.connect()
            if ok:
                resources = await client.list_resources()
                for r in resources:
                    self._resources_index[r.uri] = name
                count += 1
        return count

    async def read_resource(self, uri: str) -> Optional[str]:
        """从对应的 client 读取资源"""
        server = self._resources_index.get(uri)
        if not server:
            return None
        client = self._clients.get(server)
        if not client:
            return None
        return await client.read_resource(uri)

    def get_all_uris(self) -> List[str]:
        return list(self._resources_index.keys())

    def get_status(self) -> List[Dict]:
        return [
            {"name": name, "connected": c.is_connected}
            for name, c in self._clients.items()
        ]

    async def shutdown(self):
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
        self._resources_index.clear()
