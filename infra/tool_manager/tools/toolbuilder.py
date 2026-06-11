"""ToolBuilder 工具注册 — 注册 4 个工具到 ToolRegistry

| 工具名 | risk_level | category | 说明 |
|--------|-----------|----------|------|
| save_recipe | MEDIUM | mutation | 保存已执行的 UI 操作序列为可复用工具 |
| delete_learned_tool | MEDIUM | mutation | 删除已学工具 |
| list_learned_tools | LOW | query | 列出已学工具 |
| execute_tool_recipe | MEDIUM | mutation | 直接执行 recipe（调试用）|
"""
import json
from typing import Dict, List, Any

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("toolbuilder_tools")

_STEP_ACTIONS_HELP = (
    "steps 中的每个元素是 action/args/description 三个字段。\n"
    "支持的 action: mouse_click, mouse_double_click, mouse_right_click, "
    "mouse_move, mouse_drag, mouse_scroll, "
    "keyboard_type, keyboard_press, keyboard_hotkey, keyboard_release, "
    "click_element, double_click_element, right_click_element, type_into。\n"
    "type_into 的 args 需要 label 和 text；click_element 等需要 label。\n"
    'args 中的 {{变量名}} 会被替换为调用时传入的参数。'
)


@ToolRegistry.register(
    name="save_recipe",
    description=(
        "保存已执行的 UI 操作序列为可复用的工具。"
        "在学习模式下执行完操作后调用此工具保存成果，会生成 recipe + 插件包 + Skill。"
    ),
    params={
        "tool_name": "工具名（如 chrome_search），将用于后续调用",
        "app_name": "应用名（如 Chrome、微信）",
        "description": "工具描述，模型看到的内容",
        "steps": {
            "type": "array",
            "description": _STEP_ACTIONS_HELP,
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "操作类型"},
                    "args": {"type": "object", "description": "操作参数"},
                    "description": {"type": "string", "description": "步骤说明"},
                },
                "required": ["action"],
            },
        },
        "params_schema": {
            "type": "object",
            "description": "可选，参数模板。如 {'type':'object','properties':{'query':{'type':'string'}}}",
        },
    },
    source="builtin",
    risk_level="MEDIUM",
    category="mutation",
    tags=["toolbuilder", "automation", "learning"],
    core=True,
)
async def save_recipe(
    tool_name: str,
    app_name: str,
    description: str,
    steps: List[Dict[str, Any]],
    params_schema: str = "",
) -> Dict:
    """保存已执行的 UI 操作序列为可复用工具"""
    if not tool_name:
        return {"status": "error", "message": "tool_name 不能为空"}
    if not app_name:
        return {"status": "error", "message": "app_name 不能为空"}
    if not steps:
        return {"status": "error", "message": "steps 不能为空"}
    if not isinstance(steps, list) or len(steps) < 1:
        return {"status": "error", "message": "steps 必须是非空数组"}

    # 校验每个 step
    from modules.toolbuilder.recipe_engine import _RECIPE_ALLOWED_ACTIONS
    for i, step in enumerate(steps):
        action = step.get("action", "")
        if not action:
            return {"status": "error", "message": f"steps[{i}] 缺少 action"}
        if action not in _RECIPE_ALLOWED_ACTIONS:
            return {"status": "error", "message": f"steps[{i}] 不支持的动作: {action}。支持的: {', '.join(sorted(_RECIPE_ALLOWED_ACTIONS))}"}

    try:
        from modules.toolbuilder.plugin_builder import PluginBuilder
        from modules.toolbuilder.skill_generator import SkillGenerator

        params = json.loads(params_schema) if params_schema else {}
        if not isinstance(params, dict):
            params = {}

        # 生成插件包（含 recipe.json）
        plugin_path = PluginBuilder.create_plugin(
            tool_name, app_name, steps, params, description
        )

        # 更新 Skill
        SkillGenerator.generate_or_update(app_name)

        # 退出学习模式
        try:
            from config.settings import settings as _cfg
            if _cfg.effective_execution_mode == "learn":
                object.__setattr__(_cfg, "EXECUTION_MODE", "edit")
        except Exception:
            pass

        return {
            "status": "success",
            "tool_name": tool_name,
            "app_name": app_name,
            "plugin_path": str(plugin_path),
            "steps_count": len(steps),
            "message": f"工具 {tool_name} 已保存！共 {len(steps)} 步，立即可用。",
        }
    except Exception as e:
        return {"status": "error", "message": f"保存失败: {e}"}


@ToolRegistry.register(
    name="delete_learned_tool",
    description="删除已学的 UI 自动化工具（工具失效时调用）",
    params={
        "tool_name": "要删除的工具名",
        "app_name": "应用名（可选，不提供则搜索所有应用）",
    },
    source="builtin",
    risk_level="MEDIUM",
    category="mutation",
    tags=["toolbuilder", "automation"],
    core=True,
)
async def delete_learned_tool(tool_name: str, app_name: str = "") -> Dict:
    """删除已学工具"""
    if not tool_name:
        return {"status": "error", "message": "tool_name 不能为空"}

    try:
        from modules.toolbuilder.plugin_builder import PluginBuilder
        from modules.toolbuilder.skill_generator import SkillGenerator

        # 删除插件包
        deleted = PluginBuilder.delete_plugin(tool_name, app_name)
        if not deleted:
            return {"status": "error", "message": f"未找到工具 {tool_name}"}

        # 更新 Skill
        if app_name:
            SkillGenerator.remove_tool(app_name, tool_name)

        # 尝试热加载（插件系统已移除，跳过）
        try:
            logger.debug("插件系统已移除，跳过热加载")
        except Exception as e:
            logger.warning(f"热加载失败: {e}")

        return {
            "status": "success",
            "tool_name": tool_name,
            "message": f"工具 {tool_name} 已删除",
        }
    except Exception as e:
        return {"status": "error", "message": f"删除失败: {e}"}


@ToolRegistry.register(
    name="list_learned_tools",
    description="列出所有已学的 UI 自动化工具",
    params={
        "app_name": "按应用名筛选（可选）",
    },
    source="builtin",
    risk_level="LOW",
    category="query",
    tags=["toolbuilder", "automation"],
    core=True,
)
async def list_learned_tools(app_name: str = "") -> Dict:
    """列出已学工具"""
    try:
        from modules.toolbuilder.recipe_engine import RecipeEngine

        tools = RecipeEngine.list_all()
        if app_name:
            tools = [t for t in tools if t["app_name"] == app_name]

        return {
            "status": "success",
            "tools": tools,
            "count": len(tools),
            "message": f"共 {len(tools)} 个已学工具",
        }
    except Exception as e:
        return {"status": "error", "message": f"列出工具失败: {e}"}


@ToolRegistry.register(
    name="execute_tool_recipe",
    description="直接执行已学工具的 recipe（调试用）",
    params={
        "tool_name": "工具名",
        "params_json": "参数 JSON 字符串（如 '{\"query\": \"Python 教程\"}'）",
        "app_name": "应用名（可选）",
    },
    source="builtin",
    risk_level="MEDIUM",
    category="mutation",
    tags=["toolbuilder", "automation", "debug"],
    core=True,
)
async def execute_tool_recipe(
    tool_name: str,
    params_json: str = "{}",
    app_name: str = "",
) -> Dict:
    """直接执行 recipe"""
    if not tool_name:
        return {"status": "error", "message": "tool_name 不能为空"}

    try:
        params = json.loads(params_json) if params_json else {}
    except json.JSONDecodeError:
        return {"status": "error", "message": f"params_json 解析失败: {params_json}"}

    try:
        from modules.toolbuilder.recipe_engine import RecipeEngine
        result = RecipeEngine.execute(tool_name, params, app_name)
        return result
    except Exception as e:
        return {"status": "error", "message": f"执行失败: {e}"}
