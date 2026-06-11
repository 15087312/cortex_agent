"""
Provider/Executor 实现 — 合并本地 ToolRegistry + 远程 MCP server

CombinedToolProvider:
  - list_tools(): 合并 ToolRegistry + MCP 的工具

CombinedToolExecutor:
  - execute(): 本地工具走本地函数，MCP 工具走 transport
  权限检查通过 MCPToolService 层（self.permission.check）完成。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from utils.logger import setup_logger
from .ports import ToolExecutorPort, ToolPermissionPort, ToolProviderPort
from .server_manager import MCPServerManager
from .types import ToolCallRequest, ToolCallResult, ToolSpec

logger = setup_logger("mcp_combined")


class ToolManagerPermissionAdapter(ToolPermissionPort):
    """通过 ToolManager 执行权限检查"""

    def check(self, request: ToolCallRequest, tool: Optional[ToolSpec]) -> Dict[str, str | bool]:
        try:
            from infra.tool_manager.tool_manager import tool_manager
            if tool_manager is None:
                return {"allowed": True, "reason": ""}
            return tool_manager._check_tool_permission(
                request.tool_name,
                request.caller_role,
                request.caller_model_id,
            )
        except Exception:
            return {"allowed": False, "reason": "权限检查异常，拒绝"}


class CombinedToolProvider(ToolProviderPort):
    """合并本地 ToolRegistry + 远程 MCP server 的工具"""

    def __init__(self, server_manager: MCPServerManager):
        self._server_manager = server_manager

    def list_tools(self, source: Optional[str] = None) -> Dict[str, ToolSpec]:
        """列出所有工具"""
        from infra.tool_manager.tool_registry import ToolRegistry

        tools = {}

        # 1. 本地工具（ToolRegistry.list_tools 返回 Dict[str, dict]）
        for name, info in ToolRegistry.list_tools().items():
            tools[name] = ToolSpec(
                name=info.get("name", name),
                description=info.get("description", ""),
                parameters=info.get("params", {"type": "object", "properties": {}}),
                source=info.get("source", "builtin"),
                server_name="legacy",
                native_name=name,
                plugin_name=info.get("plugin_name", ""),
                risk_level=info.get("risk_level", "LOW"),
                category=info.get("category", "query"),
                registered_at=info.get("registered_at", ""),
            )

        # 2. MCP 远程工具
        for name, mcp_tool in self._server_manager.get_all_tools().items():
            tools[name] = ToolSpec(
                name=mcp_tool.name,
                description=mcp_tool.description,
                parameters=mcp_tool.input_schema,
                source="mcp",
                server_name=mcp_tool.server_name,
                native_name=mcp_tool.name,
                risk_level="MEDIUM",  # 外部工具默认 MEDIUM，由管理员通过配置指定
                category="mcp",
            )

        if source:
            tools = {n: t for n, t in tools.items() if t.source == source}

        return tools

    def get_tool(self, tool_name: str) -> Optional[ToolSpec]:
        """获取单个工具定义"""
        return self.list_tools().get(tool_name)

    def get_tools_for_api(self, tool_whitelist: Optional[List[str]] = None,
                          core_only: bool = False) -> List[Dict]:
        """返回给模型的 tools 数组（本地 + MCP 远程工具）"""
        from infra.tool_manager.tool_registry import ToolRegistry

        # 1. 本地核心工具
        tools = ToolRegistry.get_core_tools_for_api(tool_whitelist or []) if core_only else []

        if not core_only:
            # 手工构造非 core_only 列表
            local_tools = ToolRegistry.list_tools()
            if tool_whitelist and "*" not in tool_whitelist:
                allowed = set(tool_whitelist)
                local_tools = {n: t for n, t in local_tools.items() if n in allowed}

            for name, info in local_tools.items():
                params = info.get("params", {})
                properties = {}
                required = []
                for pname, pschema in params.items():
                    if isinstance(pschema, dict):
                        properties[pname] = pschema
                        if pschema.get("required"):
                            required.append(pname)
                    else:
                        properties[pname] = {"type": "string", "description": str(pschema)}
                tools.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": info.get("description", ""),
                        "parameters": {"type": "object", "properties": properties, "required": required}
                        if properties else {"type": "object", "properties": {}},
                    },
                })

        # 2. MCP 远程工具（跳过与本地工具同名的，避免冲突）
        existing_names = {t["function"]["name"] for t in tools}
        for mcp_name, mcp_tool in self._server_manager.get_all_tools().items():
            if mcp_name in existing_names:
                continue
            if tool_whitelist and "*" not in tool_whitelist:
                if mcp_name not in tool_whitelist:
                    continue
            tools.append({
                "type": "function",
                "function": {
                    "name": mcp_name,
                    "description": mcp_tool.description,
                    "parameters": mcp_tool.input_schema or {"type": "object", "properties": {}},
                },
            })

        return tools


class CombinedToolExecutor(ToolExecutorPort):
    """路由执行：本地工具走本地函数，MCP 工具走 transport"""

    def __init__(self, server_manager: MCPServerManager):
        self._server_manager = server_manager

    def execute(self, request: ToolCallRequest) -> ToolCallResult:
        """执行工具调用"""
        start = time.time()

        # 检查是否是 MCP 工具
        mcp_tool = self._server_manager.get_tool(request.tool_name)
        if mcp_tool:
            return self._execute_mcp(mcp_tool, request, start)

        # 本地工具
        return self._execute_local(request, start)

    def _execute_local(self, request: ToolCallRequest, start: float) -> ToolCallResult:
        """执行本地工具（同步/异步函数自动处理）"""
        import asyncio
        import concurrent.futures
        import inspect
        from infra.tool_manager.tool_registry import ToolRegistry

        func = ToolRegistry.get_func(request.tool_name)
        if not func:
            return ToolCallResult(
                success=False,
                error=f"工具不存在: {request.tool_name}",
                tool_name=request.tool_name,
                latency_ms=(time.time() - start) * 1000,
            )

        try:
            if inspect.iscoroutinefunction(func):
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, func(**request.params))
                    result = future.result(timeout=120)
            else:
                result = func(**request.params)
            latency = (time.time() - start) * 1000
            return ToolCallResult(
                success=True,
                result=result,
                tool_name=request.tool_name,
                latency_ms=latency,
            )
        except Exception as e:
            latency = (time.time() - start) * 1000
            logger.error(f"[MCP] 本地工具执行失败 {request.tool_name}: {e}")
            return ToolCallResult(
                success=False,
                error=str(e),
                tool_name=request.tool_name,
                latency_ms=latency,
            )

    def _execute_mcp(self, mcp_tool, request: ToolCallRequest, start: float) -> ToolCallResult:
        """执行 MCP 远程工具"""
        import asyncio
        import concurrent.futures

        async def _call():
            return await self._server_manager.call_tool(request.tool_name, request.params)

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _call())
                result = future.result(timeout=30)
        except Exception as e:
            latency_ms = (time.time() - start) * 1000
            return ToolCallResult(
                success=False,
                error=str(e),
                tool_name=request.tool_name,
                source="mcp",
                latency_ms=latency_ms,
            )

        latency_ms = (time.time() - start) * 1000
        is_error = result.get("isError", False)

        # 解析 MCP 返回内容
        content_text = ""
        for item in result.get("content", []):
            if item.get("type") == "text":
                content_text += item.get("text", "")

        return ToolCallResult(
            success=not is_error,
            result=content_text,
            error=content_text if is_error else None,
            tool_name=request.tool_name,
            source="mcp",
            latency_ms=latency_ms,
        )
