"""
AI 自创工具工厂 — 让模型能够动态创建和注册自己的工具

设计目标：
  模型通过调用 create_tool 等工具，可以在运行时创建新的工具函数，
  注册到 ToolRegistry，立即可用。这是 AI 自我进化的核心机制。

存储位置：
  data/learned_tools/{tool_name}/tool.json   — 轻量单文件存储
  不生成插件包的 5 个文件（plugin.yaml、tool_impl.py 等）

与 learn 模式的关系：
  learn 模式生成 UI 自动化 recipe，调用本系统注册
  create_tool 更通用，可以创建任意 Python 工具
"""
import json
import os
import inspect
import textwrap
from pathlib import Path
from typing import Any, Dict, Optional

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("create_tool")

_LEARNED_TOOLS_DIR = None


def _get_learned_dir() -> Path:
    global _LEARNED_TOOLS_DIR
    if _LEARNED_TOOLS_DIR is None:
        _LEARNED_TOOLS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "plugins" / "learned"
    return _LEARNED_TOOLS_DIR


def _sanitize_name(name: str) -> str:
    """清理名称（只保留字母数字下划线）"""
    import re
    name = re.sub(r'[^a-zA-Z0-9_\u4e00-\u9fff]', '_', name)
    return re.sub(r'_+', '_', name).strip('_').lower()


def _load_learned_tools() -> Dict[str, Dict]:
    """加载所有自创工具索引"""
    index_path = _get_learned_dir() / "_index.json"
    if index_path.exists():
        try:
            return json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_learned_tool(tool_name: str, metadata: Dict):
    """保存自创工具定义到磁盘"""
    learned_dir = _get_learned_dir()
    tool_dir = learned_dir / tool_name
    tool_dir.mkdir(parents=True, exist_ok=True)

    (tool_dir / "tool.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 更新索引
    index = _load_learned_tools()
    index[tool_name] = {
        "name": metadata.get("name", tool_name),
        "description": metadata.get("description", ""),
        "created_at": metadata.get("created_at", ""),
    }
    (learned_dir / "_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _import_tool_from_source(tool_name: str, source_code: str) -> Optional[callable]:
    """从源代码字符串编译并提取函数"""
    import ast
    import sys

    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        logger.error(f"代码语法错误: {e}")
        return None

    # 找到第一个函数定义
    func_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            func_node = node
            break

    if not func_node:
        logger.error("代码中没有找到函数定义")
        return None

    # 编译并执行
    compiled = compile(tree, f"<{tool_name}>", "exec")
    namespace = {}
    exec(compiled, namespace)

    func = namespace.get(func_node.name)
    if not func:
        logger.error(f"编译后找不到函数 {func_node.name}")
        return None

    return func


@ToolRegistry.register(
    "create_tool",
    description=(
        "创建并注册一个新的工具函数。提供 Python 源代码（需包含一个函数定义），"
        "系统会编译并注册到工具列表中，立即可用。"
        "之后可用 list_my_tools 查看、delete_tool 删除。"
    ),
    params={
        "tool_name": "工具名（如 my_search），将用于调用",
        "description": "工具描述，模型看到的内容",
        "source_code": "Python 源代码，必须包含一个函数定义。函数可接收 **kwargs",
        "risk_level": "可选，风险等级：LOW/MEDIUM/HIGH，默认 LOW",
        "category": "可选，分类：query/mutation/admin/perception，默认 query",
    },
    risk_level="MEDIUM",
    category="admin",
    core=True,
    tags=["learning"],
)
async def create_tool(
    tool_name: str,
    description: str,
    source_code: str,
    risk_level: str = "LOW",
    category: str = "query",
) -> Dict[str, Any]:
    """创建并注册一个新工具"""
    safe_name = _sanitize_name(tool_name)
    if not safe_name:
        return {"success": False, "error": "tool_name 不能为空"}

    if ToolRegistry.get_tool(safe_name):
        return {"success": False, "error": f"工具「{safe_name}」已存在，请换一个名字或先删除"}

    if risk_level.upper() not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        return {"success": False, "error": f"无效的 risk_level: {risk_level}，可选 LOW/MEDIUM/HIGH/CRITICAL"}

    # 编译源代码
    func = _import_tool_from_source(safe_name, source_code)
    if not func:
        return {"success": False, "error": "源代码编译失败，请检查语法或确保包含一个函数定义"}

    # 注册到 ToolRegistry
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    ToolRegistry.register(
        safe_name,
        description=description,
        params={"**kwargs": "工具参数"},
        risk_level=risk_level.upper(),
        category=category,
        core=False,
    )(func)

    # 持久化到磁盘
    metadata = {
        "name": safe_name,
        "description": description,
        "source_code": source_code,
        "risk_level": risk_level.upper(),
        "category": category,
        "created_at": now,
        "updated_at": now,
    }
    _save_learned_tool(safe_name, metadata)

    logger.info(f"自创工具已注册: {safe_name}")
    return {
        "success": True,
        "tool_name": safe_name,
        "message": f"工具「{safe_name}」已创建并注册，立即可用。使用 list_my_tools 查看。",
    }


@ToolRegistry.register(
    "list_my_tools",
    description="列出所有 AI 自创的工具。包括工具名、描述、风险等级。",
    params={},
    risk_level="LOW",
    category="query",
    core=True,
)
def list_my_tools() -> Dict[str, Any]:
    """列出所有自创工具"""
    index = _load_learned_tools()
    if not index:
        return {"success": True, "tools": [], "count": 0, "message": "暂无自创工具"}

    tools = []
    for name, meta in index.items():
        tool = ToolRegistry.get_tool(name)
        tools.append({
            "name": name,
            "description": meta.get("description", ""),
            "risk_level": tool.risk_level if tool else "unknown",
            "category": tool.category if tool else "unknown",
            "created_at": meta.get("created_at", ""),
        })

    return {"success": True, "tools": tools, "count": len(tools)}


@ToolRegistry.register(
    "delete_tool",
    description="删除一个 AI 自创的工具。从 ToolRegistry 和磁盘中移除。",
    params={
        "tool_name": "要删除的工具名",
    },
    risk_level="MEDIUM",
    category="admin",
    core=True,
    tags=["learning"],
)
async def delete_tool(tool_name: str) -> Dict[str, Any]:
    """删除自创工具"""
    safe_name = _sanitize_name(tool_name)
    if not safe_name:
        return {"success": False, "error": "tool_name 不能为空"}

    # 从 ToolRegistry 移除
    ToolRegistry._tools.pop(safe_name, None)

    # 从磁盘移除（自创工具路径）
    tool_dir = _get_learned_dir() / safe_name
    if tool_dir.exists():
        import shutil
        shutil.rmtree(tool_dir)

    # 从磁盘移除（已学 UI 工具路径 — data/plugins/learned_*）
    try:
        from pathlib import Path
        project_root = Path(__file__).parent.parent.parent.parent
        plugins_dir = project_root / "data" / "plugins"
        for d in plugins_dir.iterdir():
            if d.is_dir() and d.name.startswith("learned_") and d.name.endswith(f"_{safe_name}"):
                shutil.rmtree(d)
                break
    except Exception:
        pass

    # 更新索引
    index = _load_learned_tools()
    index.pop(safe_name, None)
    (_get_learned_dir() / "_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 清理旧的 save_recipe 索引 data/plugins/_index.json
    try:
        old_index_path = Path(__file__).parent.parent.parent.parent / "data" / "plugins" / "_index.json"
        if old_index_path.exists():
            old_index = json.loads(old_index_path.read_text(encoding="utf-8"))
            tools_dict = old_index.get("tools", {})
            changed = False
            for key in list(tools_dict.keys()):
                if tools_dict[key].get("tool_name") == safe_name:
                    del tools_dict[key]
                    changed = True
            if changed:
                old_index["tools"] = tools_dict
                old_index_path.write_text(json.dumps(old_index, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    logger.info(f"自创工具已删除: {safe_name}")
    return {"success": True, "message": f"工具「{safe_name}」已删除"}


@ToolRegistry.register(
    "edit_tool",
    description="修改一个已存在的自创工具的源代码。需要提供完整的工具名和新源代码。",
    params={
        "tool_name": "要修改的工具名",
        "source_code": "新的 Python 源代码，必须包含一个函数定义",
        "description": "可选，新的工具描述",
    },
    risk_level="MEDIUM",
    category="admin",
    core=True,
    tags=["learning"],
)
async def edit_tool(tool_name: str, source_code: str, description: str = "") -> Dict[str, Any]:
    """修改已注册的自创工具"""
    safe_name = _sanitize_name(tool_name)
    if not safe_name:
        return {"success": False, "error": "tool_name 不能为空"}

    old_tool = ToolRegistry.get_tool(safe_name)
    if not old_tool:
        return {"success": False, "error": f"工具「{safe_name}」不存在"}

    # 重新编译
    func = _import_tool_from_source(safe_name, source_code)
    if not func:
        return {"success": False, "error": "源代码编译失败"}

    # 更新注册
    ToolRegistry._tools[safe_name].func = func
    if description:
        ToolRegistry._tools[safe_name].description = description

    # 更新磁盘
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    metadata = {
        "name": safe_name,
        "description": description or old_tool.description,
        "source_code": source_code,
        "risk_level": old_tool.risk_level,
        "category": old_tool.category,
        "created_at": "",  # 从磁盘读取保留
        "updated_at": now,
    }
    _save_learned_tool(safe_name, metadata)

    return {"success": True, "message": f"工具「{safe_name}」已更新"}
