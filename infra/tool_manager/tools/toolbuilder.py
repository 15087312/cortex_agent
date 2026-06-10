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
    name="learn_tool",
    description=(
        "学习新的 UI 自动化操作。打开应用 → 截图 → AI 分析 UI 元素 → 规划动作序列 → "
        "录制执行 → 保存为插件工具。之后可直接调用该工具，无需视觉感知。"
    ),
    params={
        "tool_name": "工具名（如 chrome_search）",
        "app_name": "应用名（如 Chrome）",
        "task_description": "任务描述（如「在地址栏输入关键词搜索」）",
        "params_hint": "参数 schema JSON 字符串（如 '{\"query\": {\"type\": \"string\", \"required\": true}}'）",
    },
    source="builtin",
    risk_level="HIGH",
    category="mutation",
    tags=["toolbuilder", "automation", "learning"],
    core=True,
)
async def learn_tool(
    tool_name: str,
    app_name: str,
    task_description: str,
    params_hint: str = "{}",
) -> Dict:
    """学习新的 UI 自动化操作"""
    try:
        params_schema = json.loads(params_hint) if params_hint else {}
    except json.JSONDecodeError:
        return {"status": "error", "message": f"params_hint JSON 解析失败: {params_hint}"}

    if not tool_name or not app_name:
        return {"status": "error", "message": "tool_name 和 app_name 不能为空"}

    from modules.toolbuilder.recipe_engine import sanitize_name
    safe_tool = sanitize_name(tool_name)
    safe_app = sanitize_name(app_name)

    logger.info(f"开始学习工具: {safe_tool} (app: {safe_app})")

    # 1. 打开应用
    open_func = ToolRegistry.get_func("open_app")
    if open_func:
        result = open_func(app_identifier=app_name)
        if isinstance(result, dict) and result.get("status") == "error":
            return {"status": "error", "message": f"打开应用失败: {result.get('message')}"}
        time.sleep(1.5)

    # 2. 截图
    screenshot = _capture_current_screen()
    if not screenshot:
        return {"status": "error", "message": "截图失败"}

    # 3. OmniParser 分析
    try:
        from modules.perception.detectors.omniparser_detector import OmniParserDetector
        import base64
        detector = OmniParserDetector()
        elements = detector.detect_elements(base64.b64decode(screenshot))
        logger.info(f"OmniParser 检测到 {len(elements)} 个 UI 元素 (后端: {detector.backend}, 精度: {detector.precision})")

        # 精度不足时明确告知
        if detector.precision == OmniParserDetector.PRECISION_LOW:
            return {
                "status": "error",
                "message": f"UI 检测精度不足（后端: {detector.backend}，只能识别文字无法定位元素）。"
                           f"请部署 OmniParser 服务后重试。",
                "backend": detector.backend,
                "precision": detector.precision,
            }
    except Exception as e:
        logger.warning(f"OmniParser 检测失败: {e}")
        elements = []

    # 4. AI 规划动作序列
    try:
        from modules.toolbuilder.action_planner import ActionPlanner
        planner = ActionPlanner()
        steps = await planner.plan(task_description, elements, params_schema)
        if not steps:
            return {"status": "error", "message": "AI 动作规划失败，无法生成步骤"}
        logger.info(f"AI 规划了 {len(steps)} 个步骤")
    except Exception as e:
        return {"status": "error", "message": f"动作规划异常: {e}"}

    # 5. 执行录制（实际控制鼠标键盘）
    from modules.toolbuilder.recipe_engine import _RECIPE_ALLOWED_ACTIONS
    executed_steps = 0
    for step in steps:
        action = step.get("action", "")
        args = step.get("args", {})
        wait_ms = step.get("wait_after_ms", 300)

        # 白名单校验
        if action not in _RECIPE_ALLOWED_ACTIONS:
            return {
                "status": "error",
                "message": f"步骤 {step.get('step_id')}: 动作 {action} 不在允许列表中（只允许 UI 交互动作）",
                "steps_executed": executed_steps,
                "steps_total": len(steps),
                "failed_step": step.get("step_id"),
            }

        func = ToolRegistry.get_func(action)
        if func is None:
            return {
                "status": "error",
                "message": f"步骤 {step.get('step_id')}: 动作 {action} 未注册，学习中止",
                "steps_executed": executed_steps,
                "steps_total": len(steps),
                "failed_step": step.get("step_id"),
            }

        try:
            result = func(**args)
            if isinstance(result, dict) and result.get("status") == "error":
                return {
                    "status": "error",
                    "message": f"步骤 {step.get('step_id')} 执行失败: {result.get('message')}",
                    "steps_executed": executed_steps,
                    "steps_total": len(steps),
                    "failed_step": step.get("step_id"),
                }
        except Exception as e:
            return {
                "status": "error",
                "message": f"步骤 {step.get('step_id')} 异常: {e}",
                "steps_executed": executed_steps,
                "steps_total": len(steps),
                "failed_step": step.get("step_id"),
            }

        executed_steps += 1
        if wait_ms > 0:
            time.sleep(wait_ms / 1000)

    # 6. 生成插件包
    try:
        from modules.toolbuilder.plugin_builder import PluginBuilder
        plugin_path = PluginBuilder.create_plugin(
            tool_name, app_name, steps, params_schema, task_description
        )
        logger.info(f"插件包已生成: {plugin_path}")
    except Exception as e:
        return {"status": "error", "message": f"插件生成失败: {e}"}

    # 7. 热加载插件
    try:
        from modules.plugin_system.api import get_engine
        engine = get_engine()
        engine.discover()
    except Exception as e:
        logger.warning(f"插件热加载失败（非致命）: {e}")

    # 8. 更新 Skill YAML
    try:
        from modules.toolbuilder.skill_generator import SkillGenerator
        SkillGenerator.generate_or_update(app_name)
    except Exception as e:
        logger.warning(f"Skill 生成失败（非致命）: {e}")

    return {
        "status": "success",
        "tool_name": tool_name,
        "app_name": app_name,
        "plugin_path": str(plugin_path),
        "steps_count": len(steps),
        "message": f"工具 {tool_name} 学习完成，共 {len(steps)} 步",
    }


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

        # 尝试卸载插件
        try:
            from modules.plugin_system.api import get_engine
            engine = get_engine()
            engine.discover()
        except Exception as e:
            logger.warning(f"插件卸载热加载失败: {e}")

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
