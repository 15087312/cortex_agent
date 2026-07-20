"""技能系统 — 技能说明书

YAML 技能 → 模型通过工具查询和阅读 → 自行决定是否激活
"""
from .skill import Skill
from .manager import SkillManager, skill_manager

__all__ = ["Skill", "SkillManager", "skill_manager"]
