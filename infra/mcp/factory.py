"""Factory functions for MCP tool services."""
from __future__ import annotations

import threading
from typing import Optional

from utils.logger import setup_logger

from .legacy_adapter import LegacyToolExecutorAdapter, LegacyToolPermissionAdapter, LegacyToolProviderAdapter
from .tool_service import MCPToolService

logger = setup_logger("mcp_factory")

_service: Optional[MCPToolService] = None
_service_lock = threading.Lock()


def build_legacy_mcp_tool_service(tool_manager=None) -> MCPToolService:
    """Build an MCP-shaped service backed by the existing legacy registry."""
    provider = LegacyToolProviderAdapter()
    permission = LegacyToolPermissionAdapter(tool_manager) if tool_manager is not None else None
    executor = LegacyToolExecutorAdapter(permission=None)
    return MCPToolService(
        provider=provider,
        executor=executor,
        permission=permission,
    )


def get_mcp_tool_service(tool_manager=None) -> MCPToolService:
    """Return the default MCP tool service singleton."""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = build_legacy_mcp_tool_service(tool_manager=tool_manager)
    return _service


def reset_mcp_tool_service() -> None:
    """Reset the singleton, primarily for tests."""
    global _service
    with _service_lock:
        _service = None
