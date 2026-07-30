"""create_skill 工具 — 创建 SKILL.md 格式的技能说明书"""
from typing import Dict, List

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("create_skill_tool")


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

    import yaml
    from pathlib import Path
    from datetime import datetime

    project_root = Path(__file__).parent.parent.parent.parent
    skills_dir = project_root / "skills"
    skill_dir = skills_dir / skill_id
    skill_path = skill_dir / "SKILL.md"

    if skill_path.exists():
        return {"status": "error", "message": f"技能 {skill_id} 已存在"}

    skill_dir.mkdir(parents=True, exist_ok=True)

    front = {
        "id": skill_id,
        "name": name,
        "description": description[:200],
        "keywords": keywords or [],
        "trigger": {"include": keywords or [], "min_score": 1},
        "metadata": {
            "version": 1,
            "type": "learned",
            "generated_at": datetime.now().isoformat(),
        },
    }

    lines = ["---"]
    for k, v in front.items():
        if isinstance(v, dict):
            lines.append(f"{k}:")
            for sk, sv in v.items():
                if isinstance(sv, list):
                    items = ", ".join(repr(x) for x in sv)
                    lines.append(f"  {sk}: [{items}]")
                elif isinstance(sv, int):
                    lines.append(f"  {sk}: {sv}")
                else:
                    lines.append(f"  {sk}: {sv}")
        elif isinstance(v, list):
            items = ", ".join(repr(x) for x in v)
            lines.append(f"{k}: [{items}]")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(description)
    skill_path.write_text("\n".join(lines), encoding="utf-8")

    # 重载 SkillManager
    try:
        from modules.thinking.skills import skill_manager
        skill_manager._loaded = False
        skill_manager.load_skills()
    except Exception:
        pass

    return {"status": "success", "skill_id": skill_id, "path": str(skill_path), "message": f"技能 {name} 已创建，可用 request_skill(skill_id='{skill_id}') 激活"}
