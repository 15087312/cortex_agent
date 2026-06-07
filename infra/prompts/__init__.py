"""
Infra Prompts - 提示词基础设施模块

架构：
├─ manager.py      # PromptManager 单例管理器
├─ builders.py     # 动态提示词构建器
├─ constraints.py  # 防复读约束规则
├─ registry.py     # 提示词注册表
└─ templates/      # 提示词模板文件

导出：
    from infra.prompts import prompt_manager
    from infra.prompts import LargeModelPromptBuilder
    from infra.prompts import MediumModelPromptBuilder
    from infra.prompts import SmallModelPromptBuilder
    from infra.prompts import ExpertPromptBuilder
"""
from .manager import PromptManager, prompt_manager
from .builders import (
    LargeModelPromptBuilder,
    MediumModelPromptBuilder,
    SmallModelPromptBuilder,
    ExpertPromptBuilder
)
from .registry import PromptRegistry, prompt_registry
from .constraints import AntiRepetitionConstraints

__all__ = [
    "PromptManager",
    "prompt_manager",
    "LargeModelPromptBuilder",
    "MediumModelPromptBuilder",
    "SmallModelPromptBuilder",
    "ExpertPromptBuilder",
    "PromptRegistry",
    "prompt_registry",
    "AntiRepetitionConstraints",
]
