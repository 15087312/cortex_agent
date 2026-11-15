"""技能系统 — 技能说明书

YAML 技能 → 模型通过工具查询和阅读 → 自行决定是否激活
"""
from .skill import Skill
from .manager import SkillManager, skill_manager


def get_skill_manager() -> SkillManager:
    """端口优先取技能管理器：接管 ``skill_manager`` 端口 = 换掉全部调用点。

    取不到端口才回退内置实例（脚本、没跑过 bootstrap 的入口）。bootstrap 注册的
    provider 必须是 ``_default_skill_manager`` 而不是本函数，否则端口→本函数→端口
    一调用就递归。
    """
    from infra.tool_manager.service_registry import get_capability

    factory = get_capability("skill_manager")
    if factory is None:
        return _default_skill_manager()
    return factory()


def _default_skill_manager() -> SkillManager:
    """内置实例。取的是**包级**名字：测试 patch 的是 ``modules.thinking.skills.skill_manager``，
    绕过它去读 ``manager`` 里那份会让那批 patch 静默失效。"""
    return skill_manager


__all__ = ["Skill", "SkillManager", "skill_manager", "get_skill_manager"]
