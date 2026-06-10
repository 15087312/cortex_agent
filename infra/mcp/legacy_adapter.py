"""Adapters that expose legacy ToolRegistry through MCP tool ports."""
from __future__ import annotations

import inspect
import time
from typing import Dict, List, Optional

from infra.tool_manager.tool_registry import ParamSchema, ToolRegistry

from .ports import ToolExecutorPort, ToolPermissionPort, ToolProviderPort
from .types import ToolCallRequest, ToolCallResult, ToolSpec


class LegacyToolProviderAdapter(ToolProviderPort):
    """Tool provider backed by the existing in-process ToolRegistry."""

    def list_tools(self, source: Optional[str] = None) -> Dict[str, ToolSpec]:
        tools = {}
        for name in ToolRegistry.list_tools(source=source).keys():
            spec = self.get_tool(name)
            if spec:
                tools[name] = spec
        return tools

    def get_tool(self, tool_name: str) -> Optional[ToolSpec]:
        tool = ToolRegistry.get_tool(tool_name)
        if not tool:
            return None
        return ToolSpec(
            name=tool.name,
            description=tool.description,
            parameters=tool.to_json_schema(),
            source=tool.source,
            server_name="legacy",
            native_name=tool.name,
            plugin_name=tool.plugin_name,
            risk_level=tool.risk_level,
            category=tool.category,
            registered_at=tool.registered_at,
        )

    def get_tools_for_api(self, tool_whitelist: Optional[List[str]] = None, core_only: bool = False) -> List[Dict]:
        tools = self.list_tools()
        if tool_whitelist and "*" not in tool_whitelist:
            allowed = set(tool_whitelist)
            tools = {name: spec for name, spec in tools.items() if name in allowed}
        if core_only:
            tools = {
                name: spec for name, spec in tools.items()
                if ToolRegistry.get_tool(name) and ToolRegistry.get_tool(name).core
            }
        return [spec.to_api_tool() for spec in tools.values()]


class LegacyToolPermissionAdapter(ToolPermissionPort):
    """Permission adapter that delegates to existing ToolManager checks."""

    def __init__(self, tool_manager):
        self._tool_manager = tool_manager

    def check(self, request: ToolCallRequest, tool: Optional[ToolSpec]) -> Dict[str, str | bool]:
        return self._tool_manager._check_tool_permission(
            request.tool_name,
            request.caller_role,
            request.caller_model_id,
        )


class LegacyToolExecutorAdapter(ToolExecutorPort):
    """Executor adapter for the existing in-process callable registry."""

    def __init__(self, permission: Optional[ToolPermissionPort] = None, max_retries: int = 3):
        self._permission = permission
        self._max_retries = max_retries

    def execute(self, request: ToolCallRequest) -> ToolCallResult:
        tool = ToolRegistry.get_tool(request.tool_name)
        if not tool:
            return ToolCallResult(
                success=False,
                result=None,
                error=f"工具不存在: {request.tool_name}",
                tool_name=request.tool_name,
                source=request.source,
            )

        if self._permission:
            perm = self._permission.check(request, LegacyToolProviderAdapter().get_tool(request.tool_name))
            if not perm.get("allowed"):
                return ToolCallResult(
                    success=False,
                    result=None,
                    error=f"权限拒绝: {perm.get('reason', '')}",
                    tool_name=request.tool_name,
                    source=request.source,
                )

        start = time.time()
        try:
            if inspect.iscoroutinefunction(tool.func):
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                    # 在事件循环线程内，直接 await 不可行（sync 函数）
                    # 用 nest_asyncio 或 run_coroutine_threadsafe 会死锁
                    # 最佳方案：用 asyncio.ensure_future + 等待
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(asyncio.run, tool.func(**(request.params or {})))
                        result = future.result(timeout=120)
                except RuntimeError:
                    result = asyncio.run(tool.func(**(request.params or {})))
            else:
                result = tool.func(**(request.params or {}))
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


def legacy_params_to_schema(params: Dict[str, str | ParamSchema]) -> Dict:
    """Convert old param specs to JSON schema without needing a ToolInfo instance."""
    properties = {}
    required = []
    for name, spec in (params or {}).items():
        if isinstance(spec, ParamSchema):
            schema_type = "string" if spec.type in ("string", "text", "str") else spec.type
            properties[name] = {"type": schema_type}
            if spec.description:
                properties[name]["description"] = spec.description
            if spec.required:
                required.append(name)
        else:
            properties[name] = {"type": "string", "description": str(spec)}
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema
