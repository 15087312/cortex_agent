"""create_skill 工具 — 创建 YAML 格式的技能说明书

从 toolbuilder.py 中拆分出来独立维护。
"""
import json
from typing import Dict, List, Any

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
    """创建一个技能 YAML（技能说明书格式：纯提示词文档）"""
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

    data = {
        "id": skill_id,
        "name": name,
        "description": description,
        "keywords": keywords or [],
        "trigger": {"include": keywords or [], "min_score": 1},
        "metadata": {"version": 1, "type": "learned", "generated_at": __import__("datetime").datetime.now().isoformat()},
    }

    skill_path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False), encoding="utf-8")

    # 同步保存到 data/plugins/learned/（统一存储）
    try:
        plugins_learned_dir = Path(__file__).parent.parent.parent.parent / "data" / "plugins" / "learned"
        plugins_learned_dir.mkdir(parents=True, exist_ok=True)
        copy_path = plugins_learned_dir / f"{skill_id}.yaml"
        copy_path.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
    except Exception:
        pass

    # 重载 SkillManager
    try:
        from modules.thinking.skills import skill_manager
        skill_manager._loaded = False
        skill_manager.load_skills()
    except Exception:
        pass

    return {"status": "success", "skill_id": skill_id, "path": str(skill_path), "message": f"技能 {name} 已创建，可用 request_skill(skill_id='{skill_id}') 激活"}
