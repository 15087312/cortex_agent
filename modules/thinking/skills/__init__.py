"""技能系统 — 让大模型按角色、规章、流程执行任务"""
from .skill import Skill, SkillRule, WorkflowStep
from .manager import SkillManager, skill_manager

__all__ = [
    "Skill", "SkillRule", "WorkflowStep",
    "SkillManager", "skill_manager",
]
