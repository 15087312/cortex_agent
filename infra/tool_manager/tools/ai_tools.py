"""
AI 自创工具系统 — 大模型可以创建、管理自己的工具

功能：
- create_tool: 提交 Python 函数代码，动态注册为可用工具
- list_my_tools: 列出所有自创工具
- delete_tool: 删除自创工具
- edit_tool: 更新已有自创工具

持久化：
  自创工具保存在 data/ai_tools.json，系统启动时自动恢复。
  重启后工具仍在，除非使用 delete_tool 主动删除。
"""
import ast
import json
import time
import threading
from pathlib import Path
from typing import Dict, Any, Optional

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("ai_tools")

# 持久化存储路径
_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"
_AI_TOOLS_FILE = _DATA_DIR / "ai_tools.json"

# 确保持久化目录存在（启动时创建一次）
_DATA_DIR.mkdir(parents=True, exist_ok=True)

# 内存锁（保护持久化读写）
_ai_tools_lock = threading.Lock()

# 限制 exec 的可用内置函数（白名单模式，只允许安全函数）
_SAFE_BUILTINS: Dict[str, Any] = {
    "abs": abs, "all": all, "any": any, "bool": bool,
    "dict": dict, "enumerate": enumerate, "filter": filter,
    "float": float, "format": format, "frozenset": frozenset,
    "int": int, "isinstance": isinstance, "issubclass": issubclass,
    "len": len, "list": list, "map": map, "max": max, "min": min,
    "next": next, "pow": pow, "range": range, "reversed": reversed,
    "round": round, "set": set, "slice": slice, "sorted": sorted,
    "str": str, "sum": sum, "tuple": tuple, "type": type, "zip": zip,
    "True": True, "False": False, "None": None,
    "print": print,  # 允许打印（输出会被捕获）
    "Exception": Exception, "ValueError": ValueError,
    "TypeError": TypeError, "KeyError": KeyError,
    "IndexError": IndexError, "AttributeError": AttributeError,
}


# ── 持久化 ──────────────────────────────────────────────────────────

def _load_persisted() -> Dict[str, Dict[str, Any]]:
    """从磁盘加载所有持久化的自创工具记录"""
    p = _AI_TOOLS_FILE
    if not p.exists():
        return {}
    try:
        with _ai_tools_lock:
            data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data
    except Exception as e:
        logger.warning(f"[ai_tools] 持久化文件读取失败: {e}")
        return {}


def _save_persisted(tools: Dict[str, Dict[str, Any]]) -> None:
    """保存自创工具记录到磁盘"""
    try:
        with _ai_tools_lock:
            _AI_TOOLS_FILE.write_text(
                json.dumps(tools, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    except Exception as e:
        logger.warning(f"[ai_tools] 持久化文件写入失败: {e}")


def _add_persisted(name: str, record: Dict[str, Any]) -> None:
    """添加一条持久化记录"""
    tools = _load_persisted()
    tools[name] = record
    _save_persisted(tools)


def _remove_persisted(name: str) -> bool:
    """删除一条持久化记录"""
    tools = _load_persisted()
    if name in tools:
        del tools[name]
        _save_persisted(tools)
        return True
    return False


def _update_persisted(name: str, record: Dict[str, Any]) -> None:
    """更新一条持久化记录"""
    tools = _load_persisted()
    tools[name] = record
    _save_persisted(tools)


# ── 代码验证 ──────────────────────────────────────────────────────────

def _validate_tool_code(code: str, expected_func_name: str) -> Optional[str]:
    """验证工具 Python 代码的合法性

    检查：
    1. 语法正确
    2. 代码中包含预期的函数定义
    3. 无危险模式（import、eval、exec、open 等）

    Returns:
        None 表示通过，字符串表示拒绝原因
    """
    if not code or not code.strip():
        return "代码不能为空"

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"语法错误: {e}"

    # 检查是否包含了预期的函数定义
    has_func = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == expected_func_name:
            has_func = True
            break
    if not has_func:
        return f"代码中必须包含函数定义: def {expected_func_name}(...)"

    # 检查危险节点
    for node in ast.walk(tree):
        # 禁止 import（内置函数已白名单化）
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return "禁止使用 import/from（工具可使用内置函数和数据操作）"
        # 禁止 exec/eval/compile
        if isinstance(node, ast.Call):
            func_name = ""
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr
            if func_name in ("exec", "eval", "compile", "__import__"):
                return f"禁止使用 {func_name}()"
            # 禁止 open()（文件系统操作）
            if func_name == "open":
                return "禁止使用 open()（可改用系统工具处理文件）"
            # 禁止 subprocess / os.system 等
            if func_name in ("system", "popen", "Popen", "run", "call"):
                if isinstance(node.func, ast.Attribute):
                    if isinstance(node.func.value, ast.Name):
                        if node.func.value.id in ("os", "subprocess"):
                            return f"禁止使用 {node.func.value.id}.{func_name}()"

    return None  # 验证通过


# ── 工具注册/恢复 ──────────────────────────────────────────────────────

def _create_and_register(
    tool_name: str,
    description: str,
    code: str,
    params: Dict[str, str] = None,
) -> Dict[str, Any]:
    """编译 Python 代码，创建函数，注册到 ToolRegistry

    Args:
        tool_name: 工具名
        description: 工具描述
        code: Python 函数代码
        params: 参数字典 {name: description}

    Returns:
        {"success": True/False, "error": 错误信息(可选)}
    """
    params = params or {}

    # 构建命名空间
    namespace = {"__builtins__": _SAFE_BUILTINS}

    try:
        # 编译代码
        compiled = compile(code, f"<ai_tool_{tool_name}>", "exec")
        exec(compiled, namespace)
    except Exception as e:
        return {"success": False, "error": f"代码执行失败: {e}"}

    # 从命名空间中提取函数
    func = namespace.get(tool_name)
    if func is None:
        return {"success": False, "error": f"未找到函数定义: {tool_name}"}

    if not callable(func):
        return {"success": False, "error": f"「{tool_name}」不是可调用的函数"}

    # 注册到 ToolRegistry
    try:
        ToolRegistry.register_tool(
            name=tool_name,
            func=func,
            description=description,
            params=params,
            source="dynamic",
            risk_level="MEDIUM",  # 自创工具默认 MEDIUM，需 LLM 审查
            category="query",
            tags=["ai_tool"],
            priority=1,  # 自创工具优先显示
        )
    except Exception as e:
        return {"success": False, "error": f"工具注册失败: {e}"}

    return {"success": True}


# ── 启动时恢复自创工具 ──────────────────────────────────────────────

def restore_ai_tools() -> int:
    """从持久化存储恢复所有已注册的自创工具

    由系统启动时调用（如 api/main.py lifespan）。
    返回恢复的工具数量。
    """
    records = _load_persisted()
    if not records:
        return 0

    restored = 0
    for name, record in records.items():
        code = record.get("code", "")
        description = record.get("description", "")
        params = record.get("params", {})

        if not code:
            logger.warning(f"[ai_tools] 跳过恢复「{name}」: 缺少代码")
            continue

        result = _create_and_register(name, description, code, params)
        if result["success"]:
            restored += 1
            logger.info(f"[ai_tools] 恢复自创工具: {name}")
        else:
            logger.warning(f"[ai_tools] 恢复自创工具失败「{name}」: {result.get('error')}")

    logger.info(f"[ai_tools] 启动恢复完成: {restored}/{len(records)} 个工具")
    return restored


# ── 工具入口 ──────────────────────────────────────────────────────────

@ToolRegistry.register(
    "create_tool",
    description=(
        "创建一个新的自定义工具。提交 Python 函数代码，系统编译后注册为可用工具。"
        "创建后立即生效，重启后不丢失。"
        "\n\n"
        "【使用说明】\n"
        "1. 编写一个 Python 函数，函数名 = 工具名\n"
        "2. 函数应接受明确的参数，使用类型注解\n"
        "3. 函数应返回字符串或可被 str() 转换的值\n"
        "4. 函数内不能使用 import/exec/eval/open\n"
        "5. 内置函数（len/str/int/max/min/round/print 等）可直接使用\n"
        "\n"
        "【示例】\n"
        "create_tool(\n"
        "  tool_name='weather_summary',\n"
        "  description='天气数据格式化',\n"
        "  code='''def weather_summary(temp: str, condition: str) -> str:\\n"
        "    return f\"温度{temp}°C，天气{condition}\"''',\n"
        "  params={\"temp\": \"温度\", \"condition\": \"天气状况\"}\n"
        ")"
    ),
    params={
        "tool_name": "工具名称（也是 Python 函数名，字母数字下划线）",
        "description": "工具描述（模型会看到此描述）",
        "code": "Python 函数代码。函数名必须与 tool_name 一致，不能使用 import/exec/eval/open",
        "params": (
            "可选，参数描述字典。格式：{\"参数名\": \"参数描述\"}。"
            "不传则自动从函数签名推断。"
        ),
    },
    risk_level="MEDIUM",
    category="mutation",
    tags=["ai_tool", "learning"],
    core=True,
)
def create_tool(
    tool_name: str,
    description: str,
    code: str,
    params: Optional[str] = None,
) -> str:
    """创建一个新的自定义工具

    Args:
        tool_name: 工具名（也是函数名）
        description: 工具描述
        code: Python 函数代码
        params: JSON 字符串格式的参数描述，或直接传入 dict

    Returns:
        执行结果信息
    """
    if not tool_name or not tool_name.strip():
        return "❌ 错误: tool_name 不能为空"
    if not description or not description.strip():
        return "❌ 错误: description 不能为空"

    tool_name = tool_name.strip()
    # 验证工具名格式
    if not tool_name.isidentifier():
        return (
            f"❌ 错误: tool_name「{tool_name}」不是有效的 Python 标识符。"
            "请使用字母、数字和下划线。"
        )

    # 不能覆盖系统内置工具
    existing = ToolRegistry.get_tool(tool_name)
    if existing and existing.source != "dynamic":
        return (
            f"❌ 错误: 工具名「{tool_name}」已被系统内置工具占用，无法覆盖。"
            f"请换一个名称。"
        )

    # 解析 params 参数
    params_dict: Dict[str, str] = {}
    if params:
        if isinstance(params, str):
            try:
                parsed = json.loads(params)
                if isinstance(parsed, dict):
                    params_dict = {str(k): str(v) for k, v in parsed.items()}
            except (json.JSONDecodeError, TypeError):
                return "❌ 错误: params 不是有效的 JSON 对象字符串"
        elif isinstance(params, dict):
            params_dict = {str(k): str(v) for k, v in params.items()}

    # 验证代码
    validation_error = _validate_tool_code(code, tool_name)
    if validation_error:
        return f"❌ 代码验证失败: {validation_error}"

    # 编译并注册
    result = _create_and_register(tool_name, description, code, params_dict)
    if not result["success"]:
        return f"❌ 注册失败: {result['error']}"

    # 持久化
    _add_persisted(tool_name, {
        "description": description,
        "code": code,
        "params": params_dict,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })

    # 生成参数列表文本
    param_hint = ""
    if params_dict:
        keys = list(params_dict.keys())
        param_hint = f" 参数: {', '.join(keys)}"

    return (
        f"✅ 工具「{tool_name}」创建成功！已注册为可用工具。{param_hint}\n"
        f"描述: {description}\n"
        f"提示: 使用 tools_search(keyword='{tool_name}') 查看工具详情。"
    )


@ToolRegistry.register(
    "list_my_tools",
    description="列出所有 AI 自创的自定义工具。返回每个工具的名称、描述、参数和创建时间。",
    params={},
    risk_level="LOW",
    category="query",
    tags=["ai_tool"],
    core=True,
)
def list_my_tools() -> str:
    """列出所有 AI 自创工具"""
    all_tools = ToolRegistry.list_tools()
    ai_tools = {
        name: info
        for name, info in all_tools.items()
        if info.get("source") == "dynamic" and "ai_tool" in info.get("tags", [])
    }

    if not ai_tools:
        return "📭 尚未创建任何自定义工具。使用 create_tool 创建你的第一个工具。"

    lines = [f"📦 自创工具 ({len(ai_tools)} 个):", ""]
    for name, info in sorted(ai_tools.items()):
        desc = info.get("description", "")
        params_dict = info.get("params", {})
        params_str = ", ".join(params_dict.keys()) if params_dict else "(无参数)"
        registered_at = info.get("registered_at", "")
        lines.append(f"  🔧 {name}")
        lines.append(f"    描述: {desc}")
        lines.append(f"    参数: {params_str}")
        if registered_at:
            lines.append(f"    创建于: {registered_at}")
        lines.append("")

    return "\n".join(lines)


@ToolRegistry.register(
    "delete_tool",
    description="删除一个 AI 自创的自定义工具。只允许删除由 create_tool 创建的工具。",
    params={
        "tool_name": "要删除的工具名称",
    },
    risk_level="MEDIUM",
    category="mutation",
    tags=["ai_tool"],
    core=True,
)
def delete_tool(tool_name: str) -> str:
    """删除一个 AI 自创工具"""
    if not tool_name or not tool_name.strip():
        return "❌ 错误: tool_name 不能为空"

    tool_name = tool_name.strip()

    # 检查工具是否存在
    tool_info = ToolRegistry.get_tool(tool_name)
    if not tool_info:
        return f"❌ 错误: 工具「{tool_name}」不存在"

    # 只允许删除 ai_tool 标签的动态工具
    if tool_info.source != "dynamic" or "ai_tool" not in tool_info.tags:
        return (
            f"❌ 错误: 「{tool_name}」不是自创工具，无法删除。"
            f"只能删除由 create_tool 创建的工具。"
        )

    # 从 ToolRegistry 注销
    ToolRegistry.unregister(tool_name)

    # 从持久化存储移除
    _remove_persisted(tool_name)

    return f"✅ 工具「{tool_name}」已成功删除，已从持久化存储中移除。"


@ToolRegistry.register(
    "edit_tool",
    description=(
        "编辑一个已有的 AI 自创工具。可修改描述、代码和参数。"
        "先通过 list_my_tools 查看现有工具，再使用此工具修改。"
    ),
    params={
        "tool_name": "要编辑的工具名称",
        "description": "可选，新的工具描述",
        "code": "可选，新的 Python 函数代码",
        "params": "可选，新的参数描述字典（JSON 格式）",
    },
    risk_level="MEDIUM",
    category="mutation",
    tags=["ai_tool"],
    core=True,
)
def edit_tool(
    tool_name: str,
    description: Optional[str] = None,
    code: Optional[str] = None,
    params: Optional[str] = None,
) -> str:
    """编辑一个已有的 AI 自创工具"""
    if not tool_name or not tool_name.strip():
        return "❌ 错误: tool_name 不能为空"

    tool_name = tool_name.strip()

    # 检查工具是否存在
    tool_info = ToolRegistry.get_tool(tool_name)
    if not tool_info:
        return f"❌ 错误: 工具「{tool_name}」不存在"

    # 只允许编辑自创工具
    if tool_info.source != "dynamic" or "ai_tool" not in tool_info.tags:
        return (
            f"❌ 错误: 「{tool_name}」不是自创工具，无法编辑。"
            f"只能编辑由 create_tool 创建的工具。"
        )

    # 从持久化中读取旧的记录
    records = _load_persisted()
    old_record = records.get(tool_name, {})
    new_description = description if description is not None else tool_info.description
    new_code = code if code is not None else old_record.get("code", "")
    params if params is not None else old_record.get("params", {})

    # 解析 params
    params_dict: Dict[str, str] = {}
    if params is not None:
        if isinstance(params, str):
            try:
                parsed = json.loads(params)
                if isinstance(parsed, dict):
                    params_dict = {str(k): str(v) for k, v in parsed.items()}
            except (json.JSONDecodeError, TypeError):
                return "❌ 错误: params 不是有效的 JSON 对象字符串"
        elif isinstance(params, dict):
            params_dict = {str(k): str(v) for k, v in params.items()}
    else:
        params_dict = old_record.get("params", {})

    # 如果提供了新代码，验证并重新注册
    if code is not None:
        # 验证代码
        validation_error = _validate_tool_code(new_code, tool_name)
        if validation_error:
            return f"❌ 代码验证失败: {validation_error}"

        # 先注销旧的
        ToolRegistry.unregister(tool_name)

        # 重新注册
        result = _create_and_register(tool_name, new_description, new_code, params_dict)
        if not result["success"]:
            return f"❌ 重新注册失败: {result['error']}"
    else:
        # 只更新描述/参数，不需要重新编译代码
        old_func = tool_info.func
        ToolRegistry.register_tool(
            name=tool_name,
            func=old_func,
            description=new_description,
            params=params_dict,
            source="dynamic",
            risk_level="MEDIUM",
            category="query",
            tags=["ai_tool"],
            priority=1,
        )

    # 更新持久化
    _update_persisted(tool_name, {
        "description": new_description,
        "code": new_code,
        "params": params_dict,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })

    changes = []
    if description is not None:
        changes.append("描述")
    if code is not None:
        changes.append("代码")
    if params is not None:
        changes.append("参数")

    return f"✅ 工具「{tool_name}」更新成功（已修改: {', '.join(changes)}）"
