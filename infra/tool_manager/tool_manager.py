"""
工具管理器 - 统一执行工具调用

功能：
1. 内置工具加载
2. 动态工具注册
3. 统一执行接口
"""
import json
import re
import inspect
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


_timeseries_db = None
_blackbox = None


def _get_timeseries_db():
    global _timeseries_db
    if _timeseries_db is not None:
        return _timeseries_db
    try:
        from modules.management.interface import get_timeseries_db
        _timeseries_db = get_timeseries_db()
    except Exception:
        _timeseries_db = None
    return _timeseries_db


def _get_blackbox():
    global _blackbox
    if _blackbox is not None:
        return _blackbox
    try:
        from modules.memory.core.blackbox import BlackboxMemory
        _blackbox = BlackboxMemory()
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

    def _use_mcp_for_execution(self, tool_name: str) -> bool:
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
            from monitor_cli import get_monitor
            monitor = get_monitor()
            monitor.record(
                "tool",
                tool_name,
                str(params)[:80],
                str(result if success else error)[:50],
                latency_ms,
                success
            )
        except Exception as e:
            self.logger.warning(f"监控事件记录失败: {e}")

        try:
            tsdb = _get_timeseries_db()
            if tsdb:
                tsdb.write_event(
                    event_type="tool_call",
                    message=f"{tool_name} {'success' if success else 'failed'}",
                    details={
                        "tool": tool_name,
                        "params": params or {},
                        "success": success,
                        "error": error,
                        "latency_ms": round(latency_ms, 2),
                        "source": source,
                        "result_preview": event["result_preview"],
                    },
                    severity="info" if success else "warning"
                )
        except Exception as e:
            self.logger.debug(f"时序数据库记录失败 (非致命): {e}")

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

    def _check_tool_permission(self, tool_name: str, caller_role: str,
                               caller_model_id: str = "") -> Dict[str, Any]:
        """检查工具调用权限 — 基于 ModelPermissions 的角色权限

        返回: {"allowed": bool, "reason": str}
        """
        try:
            tool_info = ToolRegistry.get_tool(tool_name)
        except Exception as e:
            self.logger.warning(f"ToolRegistry 查询异常，拒绝执行 (fail-closed): {e}")
            return {"allowed": False, "reason": f"工具注册表异常: {tool_name}"}

        # 工具类别检查 — 优先使用 ModelPermissions，回退到硬编码规则
        if tool_info:
            permissions = self._get_caller_permissions(caller_model_id, caller_role)
            if permissions is not None:
                # 使用 ModelPermissions 的 allowed_tool_categories
                if not permissions.can_use_tool_category(tool_info.category):
                    return {
                        "allowed": False,
                        "reason": (
                            f"当前模型无权调用 {tool_info.category} 类别工具: {tool_name}。"
                            f"允许的类别: {permissions.allowed_tool_categories}"
                        )
                    }
            else:
                # 回退: 硬编码规则
                if caller_role.startswith("expert") and tool_info.category == "admin":
                    return {
                        "allowed": False,
                        "reason": f"专家模型无权调用 admin 类别工具: {tool_name}"
                    }

        return {"allowed": True, "reason": ""}

    @staticmethod
    def _get_caller_permissions(caller_model_id: str, caller_role: str):
        """从 ModelIdentity 获取调用者的 ModelPermissions

        Args:
            caller_model_id: 调用者的 model_id，用于精确查找
            caller_role: 调用者角色，用于回退查找

        Returns:
            ModelPermissions 或 None（无法获取时返回 None，调用方回退到硬编码规则）
        """
        try:
            from modules.thinking.model_factory import get_model_factory
            from modules.thinking.identity import get_permissions

            factory = get_model_factory()

            # 优先通过 model_id 精确查找
            if caller_model_id:
                instance = factory.get(caller_model_id)
                if instance and hasattr(instance.identity, 'permissions'):
                    return instance.identity.permissions

            # 回退: 通过角色查找同 tier 的任意实例获取其 permissions
            tier = caller_role
            if caller_role.startswith("expert"):
                tier = "expert"
            elif caller_role.startswith("supervisor"):
                tier = "supervisor"

            instances = factory.list_by_tier(tier)
            if instances:
                identity = instances[0].identity
                if hasattr(identity, 'permissions'):
                    return identity.permissions

            # 最后回退: 通过 role 构造 template_key 查找
            if caller_role and caller_role != tier:
                template_key = f"{tier}_{caller_role}" if "_" not in caller_role else caller_role
                return get_permissions(template_key)
        except Exception as e:
            self.logger.debug(f"权限查询异常，回退默认: {e}")
        return None

    def _get_func(self, tool_name: str):
        """获取工具函数 (供外部使用，如 ToolExecutor)"""
        return ToolRegistry.get_func(tool_name)

    def _auto_correct_params(self, tool_name: str, params: Dict[str, Any],
                             error: str) -> Dict[str, Any]:
        """自动修正参数名 — 使用模糊匹配将错误参数名映射到正确参数名

        Args:
            tool_name: 工具名
            params: 当前参数
            error: 原始错误信息

        Returns:
            修正后的参数字典，如果无法修正则返回原参数
        """
        from difflib import get_close_matches

        tool_info = ToolRegistry.get_tool(tool_name)
        if not tool_info or not tool_info.params:
            return params

        valid_params = list(tool_info.params.keys())
        corrected = dict(params)
        changed = False

        for key in list(corrected.keys()):
            if key not in valid_params:
                matches = get_close_matches(key, valid_params, n=1, cutoff=0.6)
                if matches:
                    self.logger.info(
                        f"[参数纠错] {tool_name}: 参数 '{key}' → '{matches[0]}'"
                    )
                    corrected[matches[0]] = corrected.pop(key)
                    changed = True

        return corrected if changed else params

    def _coerce_param_types(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """修正参数类型 — 模型常把 int/float 以字符串形式传入

        根据工具函数签名中的类型注解自动转换。
        """
        import inspect
        func = ToolRegistry.get_func(tool_name)
        if not func:
            return params

        sig = inspect.signature(func)
        coerced = dict(params)
        for name, param in sig.parameters.items():
            if name not in coerced:
                continue
            val = coerced[name]
            annotation = param.annotation
            # 处理 Optional[X] → 提取 X
            origin = getattr(annotation, '__origin__', None)
            if origin is type(None):
                continue
            # Optional[X] 是 Union[X, None]
            args = getattr(annotation, '__args__', ())
            if len(args) == 2 and type(None) in args:
                actual_type = args[0] if args[1] is type(None) else args[1]
            else:
                actual_type = annotation

            if actual_type is int and isinstance(val, str):
                try:
                    coerced[name] = int(val)
                except (ValueError, TypeError):
                    pass
            elif actual_type is float and isinstance(val, str):
                try:
                    coerced[name] = float(val)
                except (ValueError, TypeError):
                    pass

        return coerced

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
        """调用工具（带权限检查）

        Args:
            tool_name: 工具名称
            params: 工具参数
            caller_role: 调用者角色 (large/supervisor/expert/user)
                        默认 "expert" 保持向后兼容
            caller_model_id: 调用者的 model_id，用于 ModelPermissions 精确查找
        """
        params = params or {}

        if self._use_mcp_for_execution(tool_name):
            return self._call_mcp_sync(tool_name, params, caller_role, caller_model_id, source="async")

        func = ToolRegistry.get_func(tool_name)
        if not func:
            error = f"工具不存在: {tool_name}"
            self.logger.warning(error)
            self._record_tool_event(tool_name, params, False, error=error, source="async")
            return {"success": False, "result": None, "error": error}

        # === 参数类型修正（模型常把 int 传成 str） ===
        params = self._coerce_param_types(tool_name, params)

        # === 权限检查 ===
        perm = self._check_tool_permission(tool_name, caller_role, caller_model_id)
        if not perm["allowed"]:
            error = f"权限拒绝: {perm['reason']}"
            self.logger.warning(f"[权限拦截] caller={caller_role} tool={tool_name} -> {perm['reason']}")
            self._record_tool_event(tool_name, params, False, error=error, source="async")
            return {"success": False, "result": None, "error": error}

        start = time.time()
        try:
            if inspect.iscoroutinefunction(func):
                result = await func(**params)
            else:
                result = func(**params)

            latency_ms = (time.time() - start) * 1000
            self._record_tool_event(tool_name, params, True, result=result, latency_ms=latency_ms, source="async")
            self.logger.info(f"工具执行成功: {tool_name}")
            return {"success": True, "result": result, "error": None}

        except TypeError as e:
            # 参数错误 — 尝试自动纠错
            corrected = self._auto_correct_params(tool_name, params, str(e))
            if corrected != params:
                try:
                    if inspect.iscoroutinefunction(func):
                        result = await func(**corrected)
                    else:
                        result = func(**corrected)
                    latency_ms = (time.time() - start) * 1000
                    self._record_tool_event(tool_name, corrected, True,
                                            result=result, latency_ms=latency_ms,
                                            source="async")
                    self.logger.info(f"工具 {tool_name} 参数自动纠错后执行成功")
                    return {"success": True, "result": result, "error": None}
                except Exception as retry_error:
                    self.logger.debug(f"工具 {tool_name} 参数纠错失败: {retry_error}")

            error = f"参数错误: {str(e)}"
            latency_ms = (time.time() - start) * 1000
            self._record_tool_event(tool_name, params, False, error=error, latency_ms=latency_ms, source="async")
            self.logger.error(f"工具 {tool_name} 参数错误: {e}")
            return {"success": False, "result": None, "error": error}

        except Exception as e:
            error = f"执行失败: {str(e)}"
            latency_ms = (time.time() - start) * 1000
            self._record_tool_event(tool_name, params, False, error=error, latency_ms=latency_ms, source="async")
            self.logger.error(f"工具 {tool_name} 执行失败: {e}")
            return {"success": False, "result": None, "error": error}

    def call_tool_sync(self, tool_name: str, params: Dict[str, Any] = None,
                       caller_role: str = "expert", max_retries: int = 3,
                       caller_model_id: str = "") -> Dict[str, Any]:
        """同步调用工具（带权限检查 + 自动重试）

        Args:
            tool_name: 工具名称
            params: 工具参数
            caller_role: 调用者角色
            max_retries: 最大重试次数 (默认3次，指数退避)
            caller_model_id: 调用者的 model_id，用于 ModelPermissions 精确查找
        """
        params = params or {}

        if self._use_mcp_for_execution(tool_name):
            return self._call_mcp_sync(tool_name, params, caller_role, caller_model_id, source="sync")

        func = ToolRegistry.get_func(tool_name)
        if not func:
            error = f"工具不存在: {tool_name}"
            self._record_tool_event(tool_name, params, False, error=error, source="sync")
            return {"success": False, "result": None, "error": error}

        # === 权限检查 ===
        perm = self._check_tool_permission(tool_name, caller_role, caller_model_id)
        if not perm["allowed"]:
            error = f"权限拒绝: {perm['reason']}"
            self.logger.warning(f"[权限拦截] caller={caller_role} tool={tool_name} -> {perm['reason']}")
            self._record_tool_event(tool_name, params, False, error=error, source="sync")
            return {"success": False, "result": None, "error": error}

        def _execute_with_retry(params_dict: Dict, max_retries: int) -> tuple:
            """带指数退避的函数调用"""
            import time as _time
            last_error = None
            for attempt in range(max_retries):
                try:
                    result = func(**params_dict)
                    latency = (_time.time() - start) * 1000
                    return result, latency, None
                except TypeError:
                    raise  # 参数错误不重试，抛出去给 auto_correct
                except Exception as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        wait = 0.5 * (2 ** attempt)  # 0.5s, 1s, 2s
                        self.logger.debug(
                            f"工具 {tool_name} 第 {attempt+1}/{max_retries} 次失败，"
                            f"{wait:.1f}s 后重试: {e}"
                        )
                        _time.sleep(wait)
            return None, (_time.time() - start) * 1000, last_error

        start = time.time()
        try:
            result, latency_ms, error = _execute_with_retry(params, max_retries)
            if error is None:
                self._record_tool_event(tool_name, params, True, result=result, latency_ms=latency_ms, source="sync")
                return {"success": True, "result": result, "error": None}
            raise error  # 所有重试都失败了，抛出最后一个错误
        except TypeError as e:
            # 参数错误 — 尝试自动纠错
            corrected = self._auto_correct_params(tool_name, params, str(e))
            if corrected != params:
                try:
                    result, latency_ms, error = _execute_with_retry(corrected, 1)  # 纠错后只试1次
                    if error is None:
                        self._record_tool_event(tool_name, corrected, True,
                                                result=result, latency_ms=latency_ms,
                                                source="sync")
                        self.logger.info(f"工具 {tool_name} 参数自动纠错后执行成功")
                        return {"success": True, "result": result, "error": None}
                except Exception as retry_error:
                    self.logger.debug(f"工具 {tool_name} 参数纠错失败: {retry_error}")

            error = f"参数错误: {str(e)}"
            latency_ms = (time.time() - start) * 1000
            self._record_tool_event(tool_name, params, False, error=error, latency_ms=latency_ms, source="sync")
            return {"success": False, "result": None, "error": error}
        except Exception as e:
            error = str(e)
            latency_ms = (time.time() - start) * 1000
            self._record_tool_event(tool_name, params, False, error=error, latency_ms=latency_ms, source="sync")
            return {"success": False, "result": None, "error": error}

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
