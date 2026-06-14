"""Factory functions for MCP tool services — 连接真实 MCP server"""
from __future__ import annotations

import asyncio
import threading
from typing import Optional

from config.settings import settings
from utils.logger import setup_logger

from .combined_provider import CombinedToolExecutor, CombinedToolProvider
from .server_manager import MCPServerManager
from .tool_service import MCPToolService

logger = setup_logger("mcp_factory")

_service: Optional[MCPToolService] = None
_manager: Optional[MCPServerManager] = None
_service_lock = threading.Lock()
_manager_lock = threading.Lock()


def get_server_manager() -> MCPServerManager:
    """获取全局 MCPServerManager 单例"""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                from .server_registry import parse_mcp_servers
                servers = parse_mcp_servers(settings.MCP_SERVERS)
                _manager = MCPServerManager(servers)
    return _manager


def get_mcp_tool_service() -> MCPToolService:
    """获取全局 MCPToolService 单例（连接本地 ToolRegistry + 远程 MCP server）"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                server_mgr = get_server_manager()
                provider = CombinedToolProvider(server_mgr)
                executor = CombinedToolExecutor(server_mgr)
                from .tool_service import AllowAllToolPermission
                _service = MCPToolService(
                    provider=provider,
                    executor=executor,
                    permission=AllowAllToolPermission(),
                )
                # 异步启动 MCP server 连接
                try:
                    try:
                        asyncio.create_task(server_mgr.start_all())
                    except RuntimeError:
                        asyncio.run(server_mgr.start_all())
                except Exception as e:
                    logger.warning(f"[MCP] 启动 server 连接失败 (非致命): {e}")
    return _service


def shutdown_mcp():
    """关闭所有 MCP 连接（服务关闭时调用）"""
    global _service, _manager
    try:
        if _manager:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_manager.shutdown())
            loop.close()
    except Exception as e:
        logger.debug(f"[MCP] 关闭异常 (非致命): {e}")
    _service = None
    _manager = None


def reset_mcp_tool_service() -> None:
    """测试用：重置单例"""
    global _service
    with _service_lock:
        _service = None
