"""
技能查询工具 — 模型查询和阅读技能说明书

list_skills: 列出所有可用技能（可关键词过滤）
get_skill_detail: 阅读指定技能的完整说明书
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
    "list_skills",
    description="列出所有可用的技能说明书。支持按关键词搜索过滤。",
    params={
        "query": "可选，关键词搜索过滤（留空返回全部）",
    },
    risk_level="LOW",
    category="query",
)
def list_skills(query: str = "") -> Dict[str, Any]:
    """列出技能（按关键词搜索）"""
    try:
        mgr = _get_manager()
        if query:
            skills = mgr.search_skills(query)
        else:
            skills = mgr.list_skills()

        return {
            "success": True,
            "count": len(skills),
            "skills": [
                {
                    "id": s.id,
                    "name": s.name,
                    "description": s.description[:150] + ("..." if len(s.description) > 150 else ""),
                }
                for s in skills
            ],
        }
    except Exception as e:
        logger.warning(f"[list_skills] 失败: {e}")
        return {"error": str(e)}


@ToolRegistry.register(
    "get_skill_detail",
    description="阅读指定技能的完整说明书。阅读后你就知道该技能的要求和做法。",
    params={
        "skill_id": "技能 ID（来自 list_skills 的结果）",
    },
    risk_level="LOW",
    category="query",
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
            },
        }
    except Exception as e:
        logger.warning(f"[get_skill_detail] 失败: {e}")
        return {"error": str(e)}
