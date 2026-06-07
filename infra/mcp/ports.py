"""Abstract ports for MCP-backed tool integration."""
from __future__ import annotations

from typing import Dict, List, Optional, Protocol

from .types import ToolCallRequest, ToolCallResult, ToolSpec


class ToolProviderPort(Protocol):
    """Lists tool definitions from one or more backends."""

    def list_tools(self, source: Optional[str] = None) -> Dict[str, ToolSpec]:
        """Return tools by normalized tool name."""
        ...

    def get_tool(self, tool_name: str) -> Optional[ToolSpec]:
        """Return a single normalized tool definition."""
        ...

    def get_tools_for_api(self, tool_whitelist: Optional[List[str]] = None, core_only: bool = False) -> List[Dict]:
        """Return model-provider compatible tool schemas."""
        ...


class ToolExecutorPort(Protocol):
    """Executes normalized tool calls."""

    def execute(self, request: ToolCallRequest) -> ToolCallResult:
        """Execute one tool call."""
        ...


class ToolPermissionPort(Protocol):
    """Checks whether a caller can execute a tool."""

    def check(self, request: ToolCallRequest, tool: Optional[ToolSpec]) -> Dict[str, str | bool]:
        """Return {allowed: bool, reason: str}."""
        ...


class ToolEventSinkPort(Protocol):
    """Records tool execution events."""

    def record(self, request: ToolCallRequest, result: ToolCallResult) -> None:
        """Record a tool execution event."""
        ...
