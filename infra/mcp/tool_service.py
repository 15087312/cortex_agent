"""MCP-compatible tool service."""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from utils.logger import setup_logger

from .ports import ToolEventSinkPort, ToolExecutorPort, ToolPermissionPort, ToolProviderPort
from .types import ToolCallRequest, ToolCallResult, ToolSpec

logger = setup_logger("mcp_tool_service")


class NullToolEventSink(ToolEventSinkPort):
    """No-op event sink used when a caller records events elsewhere."""

    def record(self, request: ToolCallRequest, result: ToolCallResult) -> None:
        return None


class AllowAllToolPermission(ToolPermissionPort):
    """Default permission adapter for isolated tests and inactive backends."""

    def check(self, request: ToolCallRequest, tool: Optional[ToolSpec]) -> Dict[str, str | bool]:
        return {"allowed": True, "reason": ""}


class MCPToolService:
    """Facade that exposes provider/executor/permission ports as one service."""

    def __init__(
        self,
        provider: ToolProviderPort,
        executor: ToolExecutorPort,
        permission: Optional[ToolPermissionPort] = None,
        event_sink: Optional[ToolEventSinkPort] = None,
    ):
        self.provider = provider
        self.executor = executor
        self.permission = permission or AllowAllToolPermission()
        self.event_sink = event_sink or NullToolEventSink()

    def list_tools(self, source: Optional[str] = None) -> Dict[str, ToolSpec]:
        return self.provider.list_tools(source=source)

    def get_tool(self, tool_name: str) -> Optional[ToolSpec]:
        return self.provider.get_tool(tool_name)

    def get_tools_for_api(self, tool_whitelist: Optional[List[str]] = None, core_only: bool = False) -> List[Dict]:
        return self.provider.get_tools_for_api(tool_whitelist=tool_whitelist, core_only=core_only)

    def execute(self, request: ToolCallRequest) -> ToolCallResult:
        tool = self.provider.get_tool(request.tool_name)
        if not tool:
            result = ToolCallResult(
                success=False,
                result=None,
                error=f"工具不存在: {request.tool_name}",
                tool_name=request.tool_name,
                source=request.source,
            )
            self.event_sink.record(request, result)
            return result

        perm = self.permission.check(request, tool)
        if not perm.get("allowed"):
            result = ToolCallResult(
                success=False,
                result=None,
                error=f"权限拒绝: {perm.get('reason', '')}",
                tool_name=request.tool_name,
                source=request.source,
            )
            self.event_sink.record(request, result)
            return result

        start = time.time()
        result = self.executor.execute(request)
        if result.latency_ms <= 0:
            result = ToolCallResult(
                success=result.success,
                result=result.result,
                error=result.error,
                tool_name=result.tool_name or request.tool_name,
                source=result.source or request.source,
                latency_ms=(time.time() - start) * 1000,
                metadata=result.metadata,
            )
        self.event_sink.record(request, result)
        return result
