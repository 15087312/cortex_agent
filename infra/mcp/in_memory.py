"""In-memory MCP-compatible adapters for tests and local composition."""
from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

from .ports import ToolExecutorPort, ToolProviderPort
from .types import ToolCallRequest, ToolCallResult, ToolSpec


class InMemoryMCPToolProvider(ToolProviderPort):
    """Simple provider for MCP-normalized tools."""

    def __init__(self, tools: Optional[Dict[str, ToolSpec]] = None):
        self._tools = dict(tools or {})

    def register(self, tool: ToolSpec) -> None:
        self._tools[tool.name] = tool

    def list_tools(self, source: Optional[str] = None) -> Dict[str, ToolSpec]:
        if source is None:
            return dict(self._tools)
        return {name: tool for name, tool in self._tools.items() if tool.source == source}

    def get_tool(self, tool_name: str) -> Optional[ToolSpec]:
        return self._tools.get(tool_name)

    def get_tools_for_api(self, tool_whitelist: Optional[List[str]] = None, core_only: bool = False) -> List[Dict]:
        tools = self.list_tools()
        if tool_whitelist and "*" not in tool_whitelist:
            allowed = set(tool_whitelist)
            tools = {name: tool for name, tool in tools.items() if name in allowed}
        return [tool.to_api_tool() for tool in tools.values()]


class InMemoryMCPToolExecutor(ToolExecutorPort):
    """Executes registered Python callables using MCP-normalized requests."""

    def __init__(self, funcs: Optional[Dict[str, Callable]] = None):
        self._funcs = dict(funcs or {})

    def register(self, tool_name: str, func: Callable) -> None:
        self._funcs[tool_name] = func

    def execute(self, request: ToolCallRequest) -> ToolCallResult:
        start = time.time()
        func = self._funcs.get(request.tool_name)
        if not func:
            return ToolCallResult(
                success=False,
                result=None,
                error=f"MCP 工具不存在: {request.tool_name}",
                tool_name=request.tool_name,
                source=request.source,
                latency_ms=(time.time() - start) * 1000,
            )
        try:
            result = func(**(request.params or {}))
            return ToolCallResult(
                success=True,
                result=result,
                error=None,
                tool_name=request.tool_name,
                source=request.source,
                latency_ms=(time.time() - start) * 1000,
            )
        except TypeError as e:
            return ToolCallResult(
                success=False,
                result=None,
                error=f"参数错误: {e}",
                tool_name=request.tool_name,
                source=request.source,
                latency_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return ToolCallResult(
                success=False,
                result=None,
                error=str(e),
                tool_name=request.tool_name,
                source=request.source,
                latency_ms=(time.time() - start) * 1000,
            )
