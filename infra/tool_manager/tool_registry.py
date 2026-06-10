"""
工具注册中心 - 所有工具的集中注册表

支持：
1. 装饰器注册
2. 插件动态注册
3. 自动发现和加载
4. XML schema 生成 (Claude 风格工具定义)
"""
from typing import Callable, Dict, Any, Optional, List, Union
from dataclasses import dataclass, field
from datetime import datetime
import inspect
import threading
from utils.logger import setup_logger

logger = setup_logger("tool_registry")


@dataclass
class ParamSchema:
    """参数定义 — 支持类型标注"""
    description: str = ""
    type: str = "string"  # string / number / boolean / array / object
    required: bool = False


@dataclass
class ToolInfo:
    """工具信息"""
    name: str
    func: Callable
    description: str = ""
    params: Dict[str, Union[str, ParamSchema]] = field(default_factory=dict)
    source: str = "builtin"  # builtin / plugin / dynamic / security
    plugin_name: str = ""
    registered_at: str = ""
    risk_level: str = "LOW"   # LOW / MEDIUM / HIGH / CRITICAL
    category: str = "query"   # query / mutation / admin — 用于角色权限过滤
    tags: List[str] = field(default_factory=list)  # 工具分组标签，如 ["file_rw", "git_dev"]
    priority: int = 0  # 优先级: 负数=低频（可简化描述），0=正常，正数=常用（详细描述）
    core: bool = False  # 核心工具：在模型 tools 数组中展示完整 schema；非核心仅按名称列出，需通过 query_tool_details 查询

    def __post_init__(self):
        if not self.registered_at:
            self.registered_at = datetime.now().isoformat()

    @property
    def is_plugin_tool(self) -> bool:
        """是否为插件工具（需要权限检查）"""
        return self.source == "plugin"

    @property
    def is_security_tool(self) -> bool:
        """是否为安全内置工具（不需要权限检查）"""
        return self.source == "security"

    @property
    def is_builtin_tool(self) -> bool:
        """是否为内置工具"""
        return self.source == "builtin"

    @property
    def permissions(self) -> List[str]:
        """从 category 推导所需权限"""
        perm_map = {
            "query": ["read"],
            "mutation": ["write"],
            "admin": ["admin"],
        }
        return perm_map.get(self.category, ["read"])

    # ------------------------------------------------------------------
    # XML Schema 生成 (Claude 风格工具定义)
    # ------------------------------------------------------------------

    def _param_description(self, name: str) -> str:
        """获取参数描述文本"""
        spec = self.params.get(name)
        if spec is None:
            return ""
        if isinstance(spec, ParamSchema):
            return spec.description
        return str(spec)

    def _param_type(self, name: str) -> str:
        """获取参数类型"""
        spec = self.params.get(name)
        if isinstance(spec, ParamSchema):
            return spec.type
        return "string"

    def _required_params_from_signature(self) -> List[str]:
        """从函数签名推断必填参数，兼容旧的字符串 params 注册方式。"""
        required = []
        try:
            sig = inspect.signature(self.func)
        except Exception as e:
            logger.warning(f"函数签名解析失败: {e}")
            return required
        for pname, param in sig.parameters.items():
            if pname.startswith("_") or pname == "kwargs":
                continue
            if param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue
            if param.default is inspect.Parameter.empty:
                required.append(pname)
        return required

    # Python 类型 → JSON Schema 类型映射
    _TYPE_MAP = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }

    def _infer_type_from_signature(self, pname: str) -> Optional[str]:
        """从函数签名的类型注解推断 JSON Schema 类型"""
        if not self.func:
            return None
        try:
            sig = inspect.signature(self.func)
            param = sig.parameters.get(pname)
            if not param or param.annotation is inspect.Parameter.empty:
                return None

            annotation = param.annotation
            # Optional[X] → 提取 X
            origin = getattr(annotation, '__origin__', None)
            args = getattr(annotation, '__args__', ())
            if len(args) == 2 and type(None) in args:
                annotation = args[0] if args[1] is type(None) else args[1]

            return self._TYPE_MAP.get(annotation)
        except Exception as e:
            logger.debug(f"参数类型推断失败，回退 None: {e}")
            return None

    def to_json_schema(self) -> dict:
        """生成 JSON Schema 描述 (用于 API tools 参数)

        类型推断优先级：ParamSchema.type > 函数签名注解 > 默认 string
        """
        properties = {}
        required = []
        for pname, pspec in self.params.items():
            ptype = self._param_type(pname)
            # 纯字符串 params 没有类型信息时，从函数签名推断
            if ptype == "string" and not isinstance(pspec, ParamSchema):
                inferred = self._infer_type_from_signature(pname)
                if inferred:
                    ptype = inferred
            schema_type = "string" if ptype in ("string", "text", "str") else ptype
            desc = self._param_description(pname)
            prop = {"type": schema_type}
            if desc:
                prop["description"] = desc
            properties[pname] = prop
            if isinstance(pspec, ParamSchema) and pspec.required:
                required.append(pname)

        for pname in self._required_params_from_signature():
            if pname in properties and pname not in required:
                required.append(pname)

        schema = {
            "type": "object",
            "properties": properties,
        }
        if required:
            schema["required"] = required
        return schema


class ToolRegistry:
    """
    工具注册中心（类级单例模式）

    统一管理所有可用工具，支持自动发现和动态注册。

    注意：_tools 和 _tools_lock 是**类变量**，所有实例共享同一份数据。
    这是有意设计——ToolRegistry 作为全局注册表，不需要多个实例。
    任何通过 @register 装饰器或 register() 方法注册的工具都会写入同一个 _tools 字典。
    """

    _tools: Dict[str, ToolInfo] = {}  # 类级单例：所有实例共享
    _tools_lock = threading.RLock()  # CONC-5: Protect concurrent access to _tools
    _logger = setup_logger("tool_registry")
    
    @classmethod
    def register(
        cls,
        name: str = None,
        description: str = "",
        params: Dict[str, str] = None,
        source: str = "builtin",
        plugin_name: str = "",
        risk_level: str = "LOW",
        category: str = "query",
        tags: List[str] = None,
        priority: int = 0,
        core: bool = False,
    ):
        """
        工具注册装饰器

        Example:
            @ToolRegistry.register("add", description="加法计算", tags=["math"])
            def add(a: int, b: int):
                return a + b
        """
        def decorator(func: Callable) -> Callable:
            tool_name = name or func.__name__

            # CONC-5: Protect tool registration with lock
            with cls._tools_lock:
                cls._tools[tool_name] = ToolInfo(
                    name=tool_name,
                    func=func,
                    description=description or func.__doc__ or "",
                    params=params or {},
                    source=source,
                    plugin_name=plugin_name,
                    risk_level=risk_level,
                    category=category,
                    tags=tags or [],
                    priority=priority,
                    core=core,
                )

                cls._logger.debug(f"注册工具: {tool_name} (来源: {source}, 风险: {risk_level}, 类别: {category}, 标签: {tags or []}, 优先级: {priority})")
            return func

        return decorator

    @classmethod
    def register_tool(
        cls,
        name: str,
        func: Callable,
        description: str = "",
        params: Dict[str, str] = None,
        source: str = "dynamic",
        plugin_name: str = "",
        risk_level: str = "LOW",
        category: str = "query",
        tags: List[str] = None,
        priority: int = 0,
        core: bool = False,
    ) -> bool:
        """
        直接注册工具（用于插件动态注册）

        Example:
            ToolRegistry.register_tool(
                name="my_tool",
                func=my_function,
                description="我的工具",
                source="plugin",
                plugin_name="my_plugin",
                tags=["file_rw"]
            )
        """
        # CONC-5: Protect with lock
        with cls._tools_lock:
            if name in cls._tools:
                cls._logger.warning(f"工具 {name} 已存在，将被覆盖")

            cls._tools[name] = ToolInfo(
                name=name,
                func=func,
                description=description or func.__doc__ or "",
                params=params or {},
                source=source,
                plugin_name=plugin_name,
                risk_level=risk_level,
                category=category,
                tags=tags or [],
                priority=priority,
                core=core,
            )

            cls._logger.debug(f"注册工具: {name} (来源: {source}, 插件: {plugin_name}, 标签: {tags or []}, 优先级: {priority})")
        return True
    
    @classmethod
    def unregister(cls, name: str) -> bool:
        """注销工具"""
        # CONC-5: Protect with lock
        with cls._tools_lock:
            if name in cls._tools:
                tool = cls._tools[name]
                del cls._tools[name]
                cls._logger.info(f"注销工具: {name} (来源: {tool.source})")
                return True
        return False
    
    @classmethod
    def unregister_by_plugin(cls, plugin_name: str) -> int:
        """注销指定插件的所有工具"""
        # CONC-5: Protect with lock
        with cls._tools_lock:
            count = 0
            to_remove = []

            for name, tool in cls._tools.items():
                if tool.plugin_name == plugin_name:
                    to_remove.append(name)

            for name in to_remove:
                del cls._tools[name]
                count += 1

            if count > 0:
                cls._logger.info(f"卸载插件 {plugin_name} 的 {count} 个工具")

        return count
    
    @classmethod
    def get_tool(cls, name: str) -> Optional[ToolInfo]:
        """获取工具"""
        return cls._tools.get(name)
    
    @classmethod
    def get_func(cls, name: str) -> Optional[Callable]:
        """获取工具函数"""
        tool = cls._tools.get(name)
        return tool.func if tool else None
    
    @classmethod
    def list_tools(cls, source: str = None) -> Dict[str, Dict[str, Any]]:
        """列出所有已注册工具"""
        result = {}

        # CONC-5: Protect read to avoid concurrent modification issues
        with cls._tools_lock:
            for name, tool in cls._tools.items():
                if source and tool.source != source:
                    continue

                result[name] = {
                    "description": tool.description,
                    "params": tool.params,
                    "source": tool.source,
                    "plugin_name": tool.plugin_name,
                    "risk_level": tool.risk_level,
                    "category": tool.category,
                    "tags": list(tool.tags),
                    "registered_at": tool.registered_at
                }

        return result
    
    @classmethod
    def list_by_source(cls) -> Dict[str, List[str]]:
        """按来源分组列出工具"""
        result = {
            "builtin": [],
            "plugin": [],
            "dynamic": []
        }
        
        for name, tool in cls._tools.items():
            if tool.source in result:
                result[tool.source].append(name)
        
        return result
    
    @classmethod
    def get_plugins(cls) -> List[str]:
        """获取已注册工具的插件列表"""
        plugins = set()
        for tool in cls._tools.values():
            if tool.plugin_name:
                plugins.add(tool.plugin_name)
        return list(plugins)
    
    @classmethod
    def _get_filtered_tools(cls, tool_whitelist: List[str] = None) -> List[ToolInfo]:
        """按白名单过滤工具，支持 tag: 前缀

        Examples:
            ["read_file", "tag:git_dev"] → read_file + 所有 git_dev 标签的工具
        """
        if tool_whitelist is None or "*" in (tool_whitelist or []):
            return list(cls._tools.values())

        result = []
        tag_filters = set()
        name_filters = set()

        for item in tool_whitelist:
            if item.startswith("tag:"):
                tag = item[4:]  # 移除 "tag:" 前缀
                tag_filters.add(tag)
            else:
                name_filters.add(item)

        # 添加名字匹配的工具
        for name, info in cls._tools.items():
            if name in name_filters:
                result.append(info)
            elif tag_filters:
                # 检查工具标签是否匹配任何 tag_filters
                if any(tag in info.tags for tag in tag_filters):
                    result.append(info)

        return result

    @classmethod
    def get_tools_for_api(cls, tool_whitelist: List[str] = None, sort_by_priority: bool = True) -> List[Dict]:
        """将所有已注册工具转换为 API tools 参数格式

        用于 Qwen/OpenAI 兼容的原生工具调用 API。

        Args:
            tool_whitelist: 工具白名单，None 或 ["*"] 表示全部
            sort_by_priority: 是否按优先级排序（常用工具靠前，缓存友好）

        Returns:
            API 工具列表：[{"type": "function", "function": {...}}, ...]
        """
        filtered = cls._get_filtered_tools(tool_whitelist)

        # 按优先级排序: 高优先级工具靠前（负数 → 0 → 正数）
        # 这样让常用工具的描述在 API schema 开头，缓存更友好
        if sort_by_priority:
            filtered = sorted(filtered, key=lambda t: (-t.priority, t.name))

        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.to_json_schema(),
                },
            }
            for t in filtered
        ]

    @classmethod
    def get_core_tools_for_api(cls, tool_whitelist: List[str] = None, sort_by_priority: bool = True) -> List[Dict]:
        """仅返回核心工具的完整 schema（core=True 或在白名单中的工具）。

        非核心工具不返回 schema，模型需通过 query_tool_details 查询后才能调用。

        Returns:
            API 工具列表：[{"type": "function", "function": {...}}, ...]
        """
        filtered = cls._get_filtered_tools(tool_whitelist)

        if sort_by_priority:
            filtered = sorted(filtered, key=lambda t: (-t.priority, t.name))

        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.to_json_schema(),
                },
            }
            for t in filtered
            if t.core
        ]

    @classmethod
    def list_non_core_tools(cls, tool_whitelist: List[str] = None) -> List[Dict[str, str]]:
        """列出非核心工具的名称和简短描述（用于 prompt 展示）。

        Returns:
            [{"name": "tool_name", "description": "简短描述"}, ...]
        """
        filtered = cls._get_filtered_tools(tool_whitelist)
        return [
            {"name": t.name, "description": t.description}
            for t in filtered
            if not t.core
        ]

    @classmethod
    def clear_dynamic(cls) -> int:
        """清空动态注册的工具（保留内置）"""
        count = 0
        to_remove = []
        
        for name, tool in cls._tools.items():
            if tool.source == "dynamic":
                to_remove.append(name)
        
        for name in to_remove:
            del cls._tools[name]
            count += 1
        
        cls._logger.info(f"清空 {count} 个动态工具")
        return count


def register_tool(
    name: str,
    func: Callable,
    description: str = "",
    params: Dict[str, str] = None,
    plugin_name: str = ""
):
    """快捷函数：注册工具"""
    return ToolRegistry.register_tool(
        name=name,
        func=func,
        description=description,
        params=params,
        source="plugin" if plugin_name else "dynamic",
        plugin_name=plugin_name
    )
