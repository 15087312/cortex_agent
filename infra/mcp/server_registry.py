"""MCP server configuration registry.

This module is intentionally lightweight for the first migration step. It owns
server config parsing/lifecycle metadata, while concrete MCP transport adapters
can be added behind the same interface later.
"""
from __future__ import annotations

import json
from typing import Dict, Iterable, List, Optional

from utils.logger import setup_logger

from .types import MCPServerConfig

logger = setup_logger("mcp_server_registry")


class MCPServerRegistry:
    """Stores configured MCP servers by name."""

    def __init__(self, servers: Optional[Iterable[MCPServerConfig]] = None):
        self._servers: Dict[str, MCPServerConfig] = {}
        for server in servers or []:
            self.register(server)

    def register(self, server: MCPServerConfig) -> None:
        if not server.name:
            raise ValueError("MCP server name is required")
        self._servers[server.name] = server

    def get(self, name: str) -> Optional[MCPServerConfig]:
        return self._servers.get(name)

    def list(self, enabled_only: bool = False) -> List[MCPServerConfig]:
        servers = list(self._servers.values())
        if enabled_only:
            servers = [server for server in servers if server.enabled]
        return servers

    def status(self) -> Dict[str, Dict]:
        return {
            name: {
                "enabled": server.enabled,
                "command": server.command,
                "args": server.args,
                "timeout_seconds": server.timeout_seconds,
            }
            for name, server in self._servers.items()
        }


def parse_mcp_servers(raw: str) -> List[MCPServerConfig]:
    """Parse MCP server config from JSON string.

    Expected shape:
    {
      "filesystem": {"command": "npx", "args": ["..."], "enabled": true}
    }
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"MCP_SERVERS JSON 解析失败: {e}")
        return []

    servers = []
    for name, cfg in (data or {}).items():
        if not isinstance(cfg, dict):
            continue
        servers.append(
            MCPServerConfig(
                name=name,
                command=cfg.get("command", ""),
                args=list(cfg.get("args", []) or []),
                env=dict(cfg.get("env", {}) or {}),
                enabled=bool(cfg.get("enabled", True)),
                timeout_seconds=float(cfg.get("timeout_seconds", 30.0)),
            )
        )
    return servers
