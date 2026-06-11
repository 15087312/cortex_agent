"""ToolBuilder 工具注册 — 注册 4 个工具到 ToolRegistry

设计意图：
  学习模式产出的工具（通过 save_recipe 保存）与内置工具分开管理。
  已学工具注册到 ToolRegistry 时 tags=["learned"]，core=False，
  通过白名单 "tag:learned" 暴露给模型，而不是混在主工具列表里。

  save_recipe 保存后会自动注册一个包装函数，委托给 RecipeEngine.execute()。
  这样已学工具对模型来说和内置工具一样可调用，但维护上是分离的。

存储：
  - recipe.json: 动作序列（核心数据）
  - plugin.yaml: 元数据（向后兼容插件系统）
  - ToolRegistry: 运行时注册（进程内，重启后通过 __init__.py 自动加载）
"""
import json
from typing import Dict, List, Any

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("toolbuilder_tools")

# 学习模式动作录制缓冲区 — 自动记录模型在 learn 模式下的 UI 操作
_learn_recorded_actions: List[Dict[str, Any]] = []


def record_learn_action(action: str, args: dict, description: str = "") -> None:
    """记录一条学习模式下的 UI 操作

    自动过滤：
    - keyboard_type 中纯模板文本（如 {{query}}）不记录，那是变量占位不是实际输入
    """
    # 过滤纯模板输入：keyboard_type 文本全是 {{...}} 变量时不记录
    if action == "keyboard_type":
        text = args.get("text", "")
        if _is_pure_template(text):
            return

    _learn_recorded_actions.append({
        "action": action,
        "args": dict(args),
        "description": description or f"{action}: {json.dumps(args, ensure_ascii=False)[:60]}",
    })


def _is_pure_template(text: str) -> bool:
    """判断文本是否仅包含模板变量 {{...}}"""
    import re
    stripped = text.strip()
    if not stripped:
        return False
    # 去掉所有 {{...}} 后只剩空白则为纯模板
    without_vars = re.sub(r'\{\{[^}]+}}', '', stripped)
    return not without_vars.strip()


def get_learn_recorded_actions() -> List[Dict[str, Any]]:
    """获取当前学习会话中记录的所有操作"""
    return list(_learn_recorded_actions)


def clear_learn_recorded_actions() -> None:
    """清空录制缓冲区（进入学习模式时调用）"""
    _learn_recorded_actions.clear()


_STEP_ACTIONS_HELP = (
    "steps 中的每个元素是 action/args/description 三个字段。\n"
    "支持的 action: mouse_click, mouse_double_click, mouse_right_click, "
    "mouse_move, mouse_drag, mouse_scroll, "
    "keyboard_type, keyboard_press, keyboard_hotkey, keyboard_release, "
    "click_element, double_click_element, right_click_element, type_into。\n"
    "type_into 的 args 需要 label 和 text；click_element 等需要 label。\n"
    "keyboard_type 的 text 请使用真实文本，不要使用模板占位符。"
)


@ToolRegistry.register(
    name="save_recipe",
    description=(
        "保存已执行的 UI 操作序列为可复用的工具。"
        "在学习模式下执行完操作后调用此工具保存成果，会生成 recipe + 插件包 + Skill。"
        "可以不传 steps，系统会自动使用刚才记录的全部操作。"
    ),
    params={
        "tool_name": "工具名（如 chrome_search），将用于后续调用",
        "app_name": "应用名（如 Chrome、微信）",
        "description": "工具描述，模型看到的内容",
        "steps": {
            "type": "array",
            "description": _STEP_ACTIONS_HELP + " 可选，不传则使用系统自动记录的操作序列",
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
            "description": "可选，参数模板。定义工具的可变参数，如{'type':'object','properties':{'搜索内容':{'type':'string'}}}。定义了 params_schema 后，保存时会自动从录制动作中提取模板变量。",
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
    steps: List[Dict[str, Any]] = None,
    params_schema: str = "",
) -> Dict:
    """保存已执行的 UI 操作序列为可复用工具"""
    if not tool_name:
        return {"status": "error", "message": "tool_name 不能为空"}
    if not app_name:
        return {"status": "error", "message": "app_name 不能为空"}

    # 如果没传 steps，使用自动录制的操作
    if not steps:
        recorded = get_learn_recorded_actions()
        if not recorded:
            return {"status": "error", "message": "未检测到操作记录。请先执行一些 UI 操作再保存。"}
        steps = recorded
    elif not isinstance(steps, list) or len(steps) < 1:
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

        # 注册到 ToolRegistry（tagged 为 learned，与内置工具分开管理）
        try:
            from infra.tool_manager.tool_registry import ToolRegistry
            from modules.toolbuilder.recipe_engine import RecipeEngine

            def _make_runner(tn, an):
                def run(**kwargs):
                    return RecipeEngine.execute(tn, kwargs, an)
                run.__name__ = tn
                return run

            if ToolRegistry.get_tool(tool_name) is None:
                ToolRegistry.register(
                    tool_name,
                    description=description or f"已学工具: {app_name} 的自动化操作",
                    params={k: {"type": "string"} for k in params.keys()},
                    risk_level="LOW",
                    category="mutation",
                    core=False,
                    tags=["learned"],
                )(_make_runner(tool_name, app_name))
                logger.info(f"已学工具已注册 (tag=learned): {tool_name}")
        except Exception as e:
            logger.warning(f"注册已学工具失败 (非致命): {e}")

        # 生成/更新 Skill YAML，把此工具注册到 skill 的工具范围
        try:
            _ensure_learned_skill(app_name, tool_name, description, params)
        except Exception as e:
            logger.warning(f"生成 Skill 失败 (非致命): {e}")

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
            "message": f"工具 {tool_name} 已保存！已生成技能 {app_name}_skill，激活后可用。",
        }
    except Exception as e:
        return {"status": "error", "message": f"保存失败: {e}"}


def _ensure_learned_skill(app_name: str, tool_name: str, description: str, params: dict):
    """为已学工具生成或更新 Skill YAML"""
    import yaml
    from pathlib import Path

    project_root = Path(__file__).parent.parent.parent.parent
    skills_learned_dir = project_root / "skills" / "learned"
    skills_learned_dir.mkdir(parents=True, exist_ok=True)

    skill_id = f"{app_name.lower().replace(' ', '_')}_skill"
    skill_path = skills_learned_dir / f"{skill_id}.yaml"

    # 构建 keywords（应用名 + 工具名）
    keywords = [app_name, tool_name]
    if len(app_name) > 2:
        keywords.append(app_name.lower())

    # 构建 Skill
    skill_data = {
        "id": skill_id,
        "name": f"{app_name} 自动化",
        "description": f"{app_name} 应用的已学工具集",
        "keywords": keywords,
        "role": f"{app_name} 操作专家",
        "personality": f"你是 {app_name} 的自动化操作专家。你已学会 {tool_name}。",
        "speaking_style": "直接执行操作，简洁说明结果",
        "expertise": [tool_name],
        "weaknesses": [],
        "rules": [
            {"id": "use_learned_tools", "content": f"执行 {app_name} 操作时优先使用已学工具，不要重复感知", "severity": "must"},
            {"id": "tool_failure_relearn", "content": "工具执行失败时，先调用 delete_tool 删除再重新学习", "severity": "must"},
        ],
        "workflow": [
            {"step": 1, "name": "识别意图", "description": f"识别用户在 {app_name} 中的操作意图", "output": "操作意图 + 工具名"},
            {"step": 2, "name": "调用工具", "description": f"直接调用 {tool_name} 执行操作", "output": "执行结果"},
            {"step": 3, "name": "失败处理", "description": "工具失败时删除并重新学习", "output": "重新学习"},
        ],
        "tool_rules": {
            "allow_tags": ["learned"],
        },
        "metadata": {
            "learned_tools": [{"name": tool_name, "description": description or tool_name, "params": list(params.keys())}],
            "generated_at": __import__("datetime").datetime.now().isoformat(),
            "auto_generated": True,
        },
    }

    skill_path.write_text(yaml.dump(skill_data, allow_unicode=True, default_flow_style=False, sort_keys=False), encoding="utf-8")

    # 重载 SkillManager
    try:
        from modules.thinking.skills import skill_manager
        skill_manager._loaded = False
        skill_manager.load_skills()
        logger.info(f"技能已生成: {skill_id}")
    except Exception:
        pass


@ToolRegistry.register(
    "create_skill",
    description="创建一个新的技能（Skill）。技能定义了角色、规章、流程和工具范围。激活技能后，模型进入对应角色并只看到 skill 允许的工具。",
    params={
        "skill_id": "技能唯一 ID，如 chrome_automation",
        "name": "技能显示名，如 Chrome 自动化",
        "description": "技能描述",
        "keywords": "关键词列表，用于自动匹配，如 ['chrome', 'Chrome']",
        "role": "角色描述，如 Chrome 操作专家",
        "personality": "可选，人格特征",
        "rules": "可选，规章列表。[{'id':'rule1','content':'...','severity':'must'}]",
        "workflow": "可选，流程步骤。[{'step':1,'name':'步骤名','description':'...'}]",
        "tool_allow_tags": "可选，允许的工具标签列表，如 ['learned']",
        "tool_block_tools": "可选，禁止的工具名列表，如 ['exec_command']",
    },
    risk_level="LOW",
    category="mutation",
    core=True,
)
async def create_skill(
    skill_id: str,
    name: str,
    description: str,
    keywords: list,
    role: str = "",
    personality: str = "",
    rules: list = None,
    workflow: list = None,
    tool_allow_tags: list = None,
    tool_block_tools: list = None,
) -> dict:
    """创建一个技能 YAML"""
    if not skill_id or not name:
        return {"status": "error", "message": "skill_id 和 name 不能为空"}

    import yaml
    from pathlib import Path

    project_root = Path(__file__).parent.parent.parent.parent
    skills_dir = project_root / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    skill_path = skills_dir / f"{skill_id}.yaml"
    if skill_path.exists():
        return {"status": "error", "message": f"技能 {skill_id} 已存在"}

    tool_rules = {}
    if tool_allow_tags:
        tool_rules["allow_tags"] = tool_allow_tags
    if tool_block_tools:
        tool_rules["block_tools"] = tool_block_tools

    data = {
        "id": skill_id,
        "name": name,
        "description": description,
        "keywords": keywords or [],
        "role": role or f"{name} 专家",
        "personality": personality or f"你是 {name} 的专家。",
        "speaking_style": "专业、高效",
        "expertise": [],
        "weaknesses": [],
        "rules": rules or [],
        "workflow": workflow or [{"step": 1, "name": "分析", "description": "分析用户需求", "output": "行动计划"}],
    }
    if tool_rules:
        data["tool_rules"] = tool_rules

    skill_path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False), encoding="utf-8")

    # 重载 SkillManager
    try:
        from modules.thinking.skills import skill_manager
        skill_manager._loaded = False
        skill_manager.load_skills()
    except Exception:
        pass

    return {"status": "success", "skill_id": skill_id, "path": str(skill_path), "message": f"技能 {name} 已创建，可用 request_skill(skill_id='{skill_id}') 激活"}


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
