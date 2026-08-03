"""
工具管理器 - 统一执行工具调用

功能：
1. 内置工具加载
2. 动态工具注册
3. 统一执行接口
"""
import json
import re
import time
import threading
from collections import deque
from datetime import datetime
from typing import Any, Dict, Optional, List
from utils.logger import setup_logger
from .tool_registry import ToolRegistry


def extract_json(raw_output: str) -> Dict[str, Any]:
    """从模型输出里提取纯净JSON"""
    if not raw_output:
        return {"tool": "none", "params": {}}

    raw = raw_output.strip()

    # Use JSONDecoder.raw_decode for proper nested JSON handling
    decoder = json.JSONDecoder()
    try:
        # Find first { or [
        start = raw.find('{')
        if start == -1:
            start = raw.find('[')
        if start == -1:
            return {"tool": "none", "params": {}}

        # raw_decode parses exactly one JSON value and returns (obj, end_index)
        obj, _ = decoder.raw_decode(raw[start:])
        return obj
    except (json.JSONDecodeError, ValueError):
        # Fallback: try non-greedy regex for simple cases
        json_match = re.search(r"\{.*?\}", raw, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass
        return {"tool": "none", "params": {}}


_blackbox = None


def _get_blackbox():
    global _blackbox
    if _blackbox is not None:
        return _blackbox
    try:
        # 旧版 BlackboxMemory 已废弃
        _blackbox = None
    except Exception:
        _blackbox = None
    return _blackbox


class ToolManager:
    """
    工具管理器
    
    负责：
    - 加载内置工具
    - 调用注册的工具
    - 统一错误处理
    """
    
    def __init__(self):
        self.logger = setup_logger("tool_manager")
        self._tool_events = deque(maxlen=2000)
        self._event_lock = threading.Lock()
        self._mcp_service = None

        self._load_builtin_tools()

    def _get_mcp_service(self):
        """Lazy MCP-shaped tool service."""
        if self._mcp_service is None:
            from infra.mcp.factory import get_mcp_tool_service
            self._mcp_service = get_mcp_tool_service()
        return self._mcp_service

    # 所有工具执行和查询均通过 MCPToolService 路由
    def _use_mcp_for_lookup(self) -> bool:
        return True

    def _load_builtin_tools(self):
        """加载内置工具 — tools/__init__.py 自动扫描所有模块"""
        from infra.tool_manager.tool_registry import ToolRegistry
        count_before = len(ToolRegistry._tools)
        from . import tools  # noqa: F401 — 触发自动扫描
        count = len(ToolRegistry._tools) - count_before
        self.logger.info(f"内置工具加载完成，新增 {count} 个，共 {len(ToolRegistry._tools)} 个")

    def _record_tool_event(
        self,
        tool_name: str,
        params: Dict[str, Any],
        success: bool,
        result: Any = None,
        error: str = None,
        latency_ms: float = 0.0,
        source: str = "sync"
    ) -> Dict[str, Any]:
        """记录工具调用事件"""
        event = {
            "timestamp": time.time(),
            "datetime": datetime.now().isoformat(),
            "tool": tool_name,
            "params": params or {},
            "success": success,
            "result_preview": str(result)[:200] if result is not None else "",
            "error": error,
            "latency_ms": round(latency_ms, 2),
            "source": source
        }

        with self._event_lock:
            self._tool_events.append(event)

        try:
            blackbox = _get_blackbox()
            if blackbox:
                blackbox.log_module_call(
                    caller="tool_manager",
                    callee=tool_name,
                    action="call_success" if success else "call_failed",
                    details={
                        "params": params or {},
                        "latency_ms": round(latency_ms, 2),
                        "source": source,
                        "error": error,
                    }
                )
        except Exception as e:
            self.logger.debug(f"黑盒日志记录失败 (非致命): {e}")

        return event

    def _call_mcp_sync(self, tool_name: str, params: Dict[str, Any],
                       caller_role: str, caller_model_id: str = "",
                       source: str = "sync", timeout: float = 30) -> Dict[str, Any]:
        """Execute through MCP-shaped service while preserving legacy result shape."""
        from infra.mcp.types import ToolCallRequest

        request = ToolCallRequest(
            tool_name=tool_name,
            params=params or {},
            caller_role=caller_role,
            caller_model_id=caller_model_id,
            timeout=timeout,
            source=source,
        )
        result = self._get_mcp_service().execute(request)
        self._record_tool_event(
            tool_name,
            params or {},
            result.success,
            result=result.result,
            error=result.error,
            latency_ms=result.latency_ms,
            source=source,
        )
        return result.to_legacy_dict()

    async def call_tool(self, tool_name: str, params: Dict[str, Any] = None,
                        caller_role: str = "expert",
                        caller_model_id: str = "") -> Dict[str, Any]:
        """调用工具（统一 MCP 路由 + 安全门检查）

        所有工具执行均通过 MCPToolService（CombinedToolExecutor）统一路由，
        权限审查由外层 ToolSecurityGate 负责（见 api.py 的 _security_gate_check）。

        Args:
            tool_name: 工具名称
            params: 工具参数
            caller_role: 调用者角色 (large/supervisor/expert/user)
            caller_model_id: 调用者的 model_id
        """
        return self._call_mcp_sync(tool_name, params or {}, caller_role, caller_model_id, source="async")

    def call_tool_sync(self, tool_name: str, params: Dict[str, Any] = None,
                       caller_role: str = "expert", max_retries: int = 3,
                       caller_model_id: str = "") -> Dict[str, Any]:
        """同步调用工具（统一 MCP 路由）

        Args:
            tool_name: 工具名称
            params: 工具参数
            caller_role: 调用者角色
            max_retries: 保留参数（MCP 执行器内部处理重试/超时）
            caller_model_id: 调用者的 model_id
        """
        return self._call_mcp_sync(tool_name, params or {}, caller_role, caller_model_id, source="sync")

    def call_from_json(self, json_str: str, caller_role: str = "expert") -> Dict[str, Any]:
        """从JSON字符串调用工具

        Args:
            json_str: JSON 格式的工具调用
            caller_role: 调用者角色
        """
        tool_call = extract_json(json_str)
        tool_name = tool_call.get("tool", "none")
        params = tool_call.get("params", {})

        if tool_name == "none":
            return {"success": True, "tool": "none", "result": None, "error": None}

        result = self.call_tool_sync(tool_name, params, caller_role=caller_role)
        result["tool"] = tool_name
        result["source"] = "json"
        return result

    def get_tool_events(
        self,
        limit: int = 50,
        tool_name: str = None,
        success: Optional[bool] = None,
        since: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """获取工具事件历史"""
        with self._event_lock:
            events = list(self._tool_events)

        if tool_name:
            events = [e for e in events if e.get("tool") == tool_name]
        if success is not None:
            events = [e for e in events if e.get("success") is success]
        if since is not None:
            events = [e for e in events if e.get("timestamp", 0) >= since]

        if limit > 0:
            return events[-limit:]
        return events

    def get_tool_event_stats(self) -> Dict[str, Any]:
        """获取工具事件统计"""
        with self._event_lock:
            events = list(self._tool_events)

        total = len(events)
        success_count = sum(1 for e in events if e.get("success"))
        failed_count = total - success_count

        by_tool: Dict[str, Dict[str, int]] = {}
        for event in events:
            tool = event.get("tool", "unknown")
            if tool not in by_tool:
                by_tool[tool] = {"total": 0, "success": 0, "failed": 0}
            by_tool[tool]["total"] += 1
            if event.get("success"):
                by_tool[tool]["success"] += 1
            else:
                by_tool[tool]["failed"] += 1

        return {
            "total": total,
            "success": success_count,
            "failed": failed_count,
            "by_tool": by_tool,
            "latest": events[-1] if events else None
        }

    def clear_tool_events(self) -> int:
        """清空工具事件历史"""
        with self._event_lock:
            count = len(self._tool_events)
            self._tool_events.clear()
        return count

    def list_available_tools(self, source: str = None) -> Dict[str, Dict[str, Any]]:
        """列出所有可用工具"""
        tools = ToolRegistry.list_tools(source=source)
        if self._use_mcp_for_lookup():
            try:
                service = self._get_mcp_service()
                mcp_tools = service.list_tools(source=source)
                for name, spec in mcp_tools.items():
                    if name not in tools:
                        tools[name] = spec.to_listing()
            except Exception as e:
                self.logger.debug(f"MCP 工具列出来源异常: {e}")
        return tools

    def list_by_source(self) -> Dict[str, List[str]]:
        """按来源分组列出工具"""
        by_source = ToolRegistry.list_by_source()
        if self._use_mcp_for_lookup():
            try:
                service = self._get_mcp_service()
                mcp_tools = service.list_tools()
                mcp_names = [name for name, spec in mcp_tools.items() if spec.source == "mcp"]
                # 把 MCP 工具加入动态来源
                if "mcp" not in by_source:
                    by_source["mcp"] = mcp_names
                else:
                    by_source["mcp"].extend(mcp_names)
            except Exception as e:
                self.logger.debug(f"MCP 工具列表获取失败: {e}")
        return by_source
    
    def get_tool_info(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """获取工具信息"""
        tool = ToolRegistry.get_tool(tool_name)
        if not tool and self._use_mcp_for_lookup():
            try:
                service = self._get_mcp_service()
                spec = service.get_tool(tool_name)
                if spec:
                    return spec.to_listing()
            except Exception as e:
                self.logger.debug(f"MCP 工具详情查询失败: {e}")
        if not tool:
            return None

        return {
            "name": tool.name,
            "description": tool.description,
            "params": tool.params,
            "source": tool.source,
            "plugin_name": tool.plugin_name,
            "registered_at": tool.registered_at
        }
    
    def get_status(self) -> Dict[str, Any]:
        """获取工具管理器状态"""
        by_source = self.list_by_source()
        event_stats = self.get_tool_event_stats()
        all_tools = ToolRegistry.list_tools()

        if self._use_mcp_for_lookup():
            try:
                service = self._get_mcp_service()
                mcp_tools = service.list_tools()
                mcp_tool_names = list(mcp_tools.keys())
            except Exception:
                mcp_tool_names = []
        else:
            mcp_tool_names = []

        return {
            "total_tools": len(all_tools) + len(mcp_tool_names),
            "builtin_count": len(by_source.get("builtin", [])),
            "plugin_count": len(by_source.get("plugin", [])),
            "dynamic_count": len(by_source.get("dynamic", [])),
            "mcp_count": len(mcp_tool_names),
            "all_tools": list(all_tools.keys()) + mcp_tool_names,
            "tool_backend": "mcp",  # 固定 mcp，连接本地 + 远程工具
            "event_stats": {
                "total": event_stats.get("total", 0),
                "success": event_stats.get("success", 0),
                "failed": event_stats.get("failed", 0)
            }
        }


tool_manager = ToolManager()
