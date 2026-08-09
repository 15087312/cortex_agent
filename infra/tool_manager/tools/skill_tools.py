"""
技能查询工具 — 模型查询和阅读技能说明书

get_skill_detail: 阅读指定技能的完整说明书（先通过控制工具 list_skills 获取技能列表）
"""
from typing import Any, Dict

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("skill_tools")


def _get_manager():
    from modules.thinking.skills.manager import skill_manager
    if not skill_manager._loaded:
        skill_manager.load_skills()
    return skill_manager


@ToolRegistry.register(
    "get_skill_detail",
    description="阅读指定技能的完整说明书。阅读后你就知道该技能的要求和做法。先使用 list_skills（控制工具）获取可用技能列表。",
    params={
        "skill_id": "技能 ID（来自 list_skills 的结果）",
    },
    risk_level="LOW",
    category="query",
    core=True,
)
def get_skill_detail(skill_id: str) -> Dict[str, Any]:
    """阅读技能说明书全文"""
    try:
        mgr = _get_manager()
        skill = mgr.get_skill(skill_id)
        if not skill:
            return {"error": f"技能不存在: {skill_id}"}

        return {
            "success": True,
            "skill": {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "keywords": list(skill.keywords or []),
                "trigger": skill.trigger,
                "tool_rules": skill.tool_rules,
                "enabled": skill.enabled,
                "source": skill.source,
                "metadata": skill.metadata,
            },
        }
    except Exception as e:
        logger.warning(f"[get_skill_detail] 失败: {e}")
        return {"error": str(e)}
