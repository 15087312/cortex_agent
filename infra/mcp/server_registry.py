"""MCP server configuration registry.

This module is intentionally lightweight for the first migration step. It owns
server config parsing/lifecycle metadata, while concrete MCP transport adapters
can be added behind the same interface later.
"""
from __future__ import annotations

import json
import os
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

        args = list(cfg.get("args", []) or [])

        # ── 修正 MCP server 脚本的相对路径 ──
        # screen_monitor_server.py 等本地脚本使用相对于项目根目录的路径，
        # 但 MCP 子进程的 CWD 不一定等于项目根目录。若 args 中的路径是
        # 相对路径且指向项目内的已知脚本，则解析为绝对路径。
        # 警告：禁止在 .env 中只写相对路径而不在此处解析
        config = MCPServerConfig(
            name=name,
            command=cfg.get("command", ""),
            args=args,
            env=dict(cfg.get("env", {}) or {}),
            enabled=bool(cfg.get("enabled", True)),
            timeout_seconds=float(cfg.get("timeout_seconds", 30.0)),
        )

        # 对 python 类本地脚本，将 args 中的相对路径转为绝对路径
        _resolved = _resolve_mcp_script_path(config)
        if _resolved is not None:
            config = _resolved

        servers.append(config)
    return servers


def _resolve_mcp_script_path(config: MCPServerConfig) -> Optional[MCPServerConfig]:
    """将 MCP server args 中的相对脚本路径解析为项目根目录的绝对路径。

    screen_monitor_server.py / screen_diff_server.py 等脚本使用相对于
    项目根目录的路径（如 infra/mcp/servers/xxx.py）。MCP 子进程的 CWD
    可能不在项目根目录，导致 FileNotFoundError。
    """
    if not config.args or not config.args[0]:
        return None

    arg0 = config.args[0]
    # 只处理相对路径（不以 / 开头）
    if os.path.isabs(arg0):
        return None

    # 尝试定位项目根目录（向上查找 pyproject.toml）
    _candidates = [
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),  # infra/mcp/..
        os.getcwd(),
    ]
    for _cwd in _candidates:
        _abs = os.path.join(_cwd, arg0)
        if os.path.isfile(_abs):
            if _abs != arg0:
                new_args = [_abs] + list(config.args[1:])
                return MCPServerConfig(
                    name=config.name,
                    command=config.command,
                    args=new_args,
                    env=config.env,
                    enabled=config.enabled,
                    timeout_seconds=config.timeout_seconds,
                )
    return None
