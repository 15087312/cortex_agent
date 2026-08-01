"""
Provider/Executor 实现 — 合并本地 ToolRegistry + 远程 MCP server
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import time
from typing import Any, Dict, List, Optional

from utils.logger import setup_logger
from .ports import ToolExecutorPort, ToolProviderPort
from .server_manager import MCPServerManager
from .types import ToolCallRequest, ToolCallResult, ToolSpec

logger = setup_logger("mcp_combined")

# 全局共享线程池 — 用于执行异步工具函数
_ASYNC_TOOL_POOL: Optional[concurrent.futures.ThreadPoolExecutor] = None


def _get_async_pool() -> concurrent.futures.ThreadPoolExecutor:
    """获取全局共享线程池（最多 4 个并发异步工具）"""
    global _ASYNC_TOOL_POOL
    if _ASYNC_TOOL_POOL is None:
        _ASYNC_TOOL_POOL = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="async-tool"
        )
    return _ASYNC_TOOL_POOL


def _run_async_in_thread(func, kwargs) -> Any:
    """在独立线程 + 专用事件循环中运行异步工具函数。

    替代 asyncio.run() + ThreadPoolExecutor(max_workers=1) 模式。
    Python 3.12+ 的 loop.shutdown_default_executor(timeout=N) 允许设置超时，
    避免 MLX 等推理框架的内部线程不响应时死等 → PyThreadState_Get crash。
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(func(**kwargs))
    finally:
        # 官方推荐清理顺序（Python 3.12+）：
        #   1. shutdown_asyncgens()
        #   2. shutdown_default_executor(timeout=N) ← N 防止死等
        #   3. loop.close()
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception as e:
            logger.debug(f"shutdown_asyncgens 失败 (非致命): {e}")
        try:
            loop.run_until_complete(loop.shutdown_default_executor(timeout=5.0))
        except Exception as e:
            logger.debug(f"shutdown_default_executor 失败 (非致命): {e}")
        try:
            loop.close()
        except Exception as e:
            logger.debug(f"loop.close 失败 (非致命): {e}")
        asyncio.set_event_loop(None)


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
        """执行工具调用

        优先级：本地工具 > MCP 远程工具
        本地工具更可靠（无网络/npx 依赖），同名时优先使用本地实现。
        """
        start = time.time()

        # 优先本地工具（更快、更可靠）
        from infra.tool_manager.tool_registry import ToolRegistry
        if ToolRegistry.get_func(request.tool_name):
            return self._execute_local(request, start)

        # 回退：MCP 远程工具
        mcp_tool = self._server_manager.get_tool(request.tool_name)
        if mcp_tool:
            return self._execute_mcp(mcp_tool, request, start)

        # 不存在
        return ToolCallResult(
            success=False,
            error=f"工具不存在: {request.tool_name}",
            tool_name=request.tool_name,
            source=request.source,
            latency_ms=(time.time() - start) * 1000,
        )

    def _execute_local(self, request: ToolCallRequest, start: float) -> ToolCallResult:
        """执行本地工具（async 函数在当前事件循环执行，sync 函数走线程池）"""
        from infra.tool_manager.tool_registry import ToolRegistry
        import asyncio as _asyncio

        func = ToolRegistry.get_func(request.tool_name)
        if not func:
            return ToolCallResult(
                success=False,
                error=f"工具不存在: {request.tool_name}",
                tool_name=request.tool_name,
                latency_ms=(time.time() - start) * 1000,
            )

        try:
            if _asyncio.iscoroutinefunction(func):
                # async 工具函数不能在已有事件循环中直接 asyncio.run()
                # 在共享线程池中调用 _run_async_in_thread，确保新事件循环
                # 且带 timeout 安全清理
                pool = _get_async_pool()
                future = pool.submit(_run_async_in_thread, func, request.params)
                result = future.result(timeout=600)
            else:
                pool = _get_async_pool()
                future = pool.submit(func, **request.params)
                result = future.result(timeout=120)
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
        async def _call():
            return await self._server_manager.call_tool(request.tool_name, request.params)

        try:
            pool = _get_async_pool()
            future = pool.submit(_run_async_in_thread, _call, {})
            result = future.result(timeout=30)
        except concurrent.futures.TimeoutError:
            latency_ms = (time.time() - start) * 1000
            return ToolCallResult(
                success=False,
                error=f"MCP 工具超时 (30s): {request.tool_name}",
                tool_name=request.tool_name,
                source="mcp",
                latency_ms=latency_ms,
            )
        except Exception as e:
            latency_ms = (time.time() - start) * 1000
            return ToolCallResult(
                success=False,
                error=str(e) or f"MCP 工具执行异常: {request.tool_name}",
                tool_name=request.tool_name,
                source="mcp",
                latency_ms=latency_ms,
            )

        latency_ms = (time.time() - start) * 1000
        is_error = result.get("isError", False)

        # 解析 MCP 返回内容
        # content 项可能是 dict（错误回退路径）或 mcp SDK 的 TextContent 对象
        content_text = ""
        for item in result.get("content", []):
            if isinstance(item, dict):
                if item.get("type") == "text":
                    content_text += item.get("text", "")
            else:
                if getattr(item, "type", "") == "text":
                    content_text += getattr(item, "text", "") or ""

        # 错误信息提取：优先 content_text，其次 MCP error 字段，最后回退到原始结果摘要
        error_text = None
        if is_error:
            text = (content_text or "").strip()
            if text:
                error_text = text
            else:
                # 尝试从 MCP 错误字段提取
                text = (result.get("error", "") or "").strip()
                if text:
                    if isinstance(text, str):
                        error_text = text
                    else:
                        error_text = str(text)
                else:
                    # 回退：尝试提取有用的错误描述
                    error_text = result.get("message", "") or str(result)

        return ToolCallResult(
            success=not is_error,
            result=content_text,
            error=error_text,
            tool_name=request.tool_name,
            source="mcp",
            latency_ms=latency_ms,
        )
