"""ToolBuilder 工具注册 — 注册 5 个工具到 ToolRegistry

| 工具名 | risk_level | category | 说明 |
|--------|-----------|----------|------|
| learn_tool | HIGH | mutation | 学习新 UI 操作 |
| delete_learned_tool | MEDIUM | mutation | 删除已学工具 |
| list_learned_tools | LOW | query | 列出已学工具 |
| create_app_skill | LOW | mutation | 手动更新 Skill YAML |
| execute_tool_recipe | MEDIUM | mutation | 直接执行 recipe（调试用）|
"""
import json
import time
from typing import Dict

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("toolbuilder_tools")



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
    name="create_app_skill",
    description="手动触发指定应用的 Skill YAML 生成/更新",
    params={
        "app_name": "应用名",
    },
    source="builtin",
    risk_level="LOW",
    category="mutation",
    tags=["toolbuilder", "skill"],
    core=True,
)
async def create_app_skill(app_name: str) -> Dict:
    """手动触发 Skill 生成"""
    if not app_name:
        return {"status": "error", "message": "app_name 不能为空"}

    try:
        from modules.toolbuilder.skill_generator import SkillGenerator
        path = SkillGenerator.generate_or_update(app_name)
        if path:
            return {
                "status": "success",
                "skill_path": str(path),
                "message": f"Skill 已生成: {path}",
            }
        return {
            "status": "success",
            "message": f"应用 {app_name} 无已学工具，未生成 Skill",
        }
    except Exception as e:
        return {"status": "error", "message": f"Skill 生成失败: {e}"}


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


def _capture_current_screen() -> str:
    """截取当前屏幕，返回 base64 编码的 PNG"""
    from utils.screen_capture import capture_screen
    return capture_screen() or ""
