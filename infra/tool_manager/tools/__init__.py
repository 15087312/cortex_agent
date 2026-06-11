"""
内置工具集 — 自动扫描并导入本目录下所有模块，触发 @ToolRegistry.register 装饰器。

设计意图：
  新增工具只需在 tools/ 下创建 .py 文件并使用 @ToolRegistry.register 装饰器，
  无需手动 import，自动发现并注册。

启动时加载策略：
  1. 扫描 infra/tool_manager/tools/ 下所有 .py 模块 → 注册内置工具
  2. 加载 modules/memory/tools/classified_memory_tool → 记忆分类工具
  3. 扫描 data/plugins/learned_*/ → 加载之前学过的 UI 自动化工具
     （每个已学工具注册为一个包装函数，委托给 RecipeEngine.execute()）

  步骤 3 确保重启后已学工具仍然可用，无需重新学习。
"""
import importlib
import pkgutil
from pathlib import Path

_package_dir = Path(__file__).parent

_imported = []
for _module_info in pkgutil.iter_modules([str(_package_dir)]):
    if _module_info.name.startswith("_"):
        continue
    importlib.import_module(f".{_module_info.name}", package=__name__)
    _imported.append(_module_info.name)

# 加载分类记忆工具（位于 modules/memory/tools/）
try:
    importlib.import_module("modules.memory.tools.classified_memory_tool")
except Exception:
    pass

# 加载已学的 UI 自动化工具（data/plugins/learned_*/）
try:
    _load_learned_tools()
except Exception:
    pass

__all__ = _imported


def _load_learned_tools():
    """从 data/plugins/learned_*/ 目录加载已学工具到 ToolRegistry"""
    from pathlib import Path
    from infra.tool_manager.tool_registry import ToolRegistry
    from modules.toolbuilder.recipe_engine import RecipeEngine

    learned_dir = Path(__file__).parent.parent.parent.parent / "data" / "plugins"
    if not learned_dir.exists():
        return

    for plugin_dir in learned_dir.iterdir():
        if not plugin_dir.is_dir() or not plugin_dir.name.startswith("learned_"):
            continue
        # 解析 tool_name 和 app_name
        parts = plugin_dir.name.replace("learned_", "", 1).split("_", 1)
        if len(parts) != 2:
            continue
        app_name_raw, tool_name_raw = parts
        # 反向映射：app_name 可能含下划线，tool_name 也可能含下划线
        # 更可靠的方式是读 recipe.json
        recipe_path = plugin_dir / "recipe.json"
        if not recipe_path.exists():
            continue
        try:
            import json
            recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
            tool_name = recipe.get("tool_name", tool_name_raw)
            app_name = recipe.get("app_name", app_name_raw)
            desc = recipe.get("description", f"已学工具: {app_name} 的自动化操作")
            params_schema = recipe.get("params", {})

            if ToolRegistry.get_tool(tool_name) is not None:
                continue  # 已注册

            def _make_runner(tn, an):
                def run(**kwargs):
                    return RecipeEngine.execute(tn, kwargs, an)
                run.__name__ = tn
                return run

            ToolRegistry.register(
                tool_name,
                description=desc,
                params={k: {"type": "string"} for k in params_schema.get("properties", {}).keys()},
                risk_level="LOW",
                category="mutation",
                core=False,
                tags=["learned"],
            )(_make_runner(tool_name, app_name))
        except Exception:
            continue
