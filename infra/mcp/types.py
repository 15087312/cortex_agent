"""Shared data types for MCP-backed tools."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class MCPServerConfig:
    """Configuration for one MCP server."""

    name: str
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class ToolSpec:
    """Normalized tool definition independent of a concrete backend."""

    name: str
    description: str = ""
    parameters: Dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    source: str = "mcp"
    server_name: str = ""
    native_name: str = ""
    plugin_name: str = ""
    risk_level: str = "LOW"
    category: str = "query"
    registered_at: str = ""

    def to_api_tool(self) -> Dict[str, Any]:
        """Return OpenAI/Qwen-compatible function tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }

    def to_listing(self) -> Dict[str, Any]:
        """Return ToolManager-compatible listing metadata."""
        return {
            "description": self.description,
            "params": self.parameters,
            "source": self.source,
            "plugin_name": self.plugin_name,
            "registered_at": self.registered_at,
            "server_name": self.server_name,
            "native_name": self.native_name,
            "risk_level": self.risk_level,
            "category": self.category,
        }


@dataclass(frozen=True)
class ToolCallRequest:
    """Normalized tool call request."""

    tool_name: str
    params: Dict[str, Any] = field(default_factory=dict)
    caller_role: str = "expert"
    caller_model_id: str = ""
    timeout: float = 30.0
    source: str = "mcp"


@dataclass(frozen=True)
class ToolCallResult:
    """Normalized tool call result."""

    success: bool
    result: Any = None
    error: Optional[str] = None
    tool_name: str = ""
    source: str = "mcp"
    latency_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_legacy_dict(self) -> Dict[str, Any]:
        """Return the existing ToolManager result shape."""
        return {
            "success": self.success,
            "result": self.result,
            "error": self.error,
        }
