"""create_skill 工具 — 创建 SKILL.md 格式的技能说明书

委托 SkillManager.create_skill() 落盘（内部使用 yaml.safe_dump 序列化 front matter，
避免手拼 YAML 在 description 含冒号/换行时破坏 front matter 导致加载失败）。
"""

from infra.tool_manager.tool_registry import ToolRegistry
from infra.tool_manager.service_registry import get_capability
from utils.logger import setup_logger

logger = setup_logger("create_skill_tool")


@ToolRegistry.register(
    "create_skill",
    description="创建一个新的技能（Skill）。技能定义了角色、规章、流程和工具范围。激活技能后，模型进入对应角色并只看到 skill 允许的工具。",
    params={
        "skill_id": "技能唯一 ID，如 chrome_automation",
        "name": "技能显示名，如 Chrome 自动化",
        "description": "技能描述（技能说明书正文）",
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
    tags=["learning"],
)
async def create_skill(
    skill_id: str,
    name: str,
    description: str,
    keywords: list = None,
) -> dict:
    """创建一个 SKILL.md（技能说明书格式：YAML front matter + Markdown 正文）"""
    if not skill_id or not name:
        return {"status": "error", "message": "skill_id 和 name 不能为空"}

    try:
        factory = get_capability("skill_manager")
        if factory is None:
            return {"status": "error", "message": "技能服务未注册"}
        skill_manager = factory()
        # SkillManager.create_skill 内部：yaml.safe_dump front matter + 原子写 + reload
        ok, msg = skill_manager.create_skill(
            skill_id=skill_id,
            name=name,
            description=description or "",
            keywords=list(keywords or []),
            trigger=None,
            tool_rules=None,
        )
        if not ok:
            return {"status": "error", "message": msg}
        return {
            "status": "success",
            "skill_id": skill_id,
            "path": msg or f"skills/{skill_id}/SKILL.md",
            "message": f"技能 {name} 已创建，可用 request_skill(skill_id='{skill_id}') 激活",
        }
    except Exception as e:
        logger.warning(f"[create_skill] 失败: {e}")
        return {"status": "error", "message": str(e)}
