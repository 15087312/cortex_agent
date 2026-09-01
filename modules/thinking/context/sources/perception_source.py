"""PerceptionSource — 感知系统 → TurnContext 接入点"""
from modules.thinking.context.pool import ContextFragment


class PerceptionSource:
    """从 PerceptionPool 取快照 → ContextFragment"""

    async def collect(self) -> ContextFragment:
        from modules.perception.integration import get_perception_integrator
        # 感知系统总开关关闭时不再采集，直接返回空 fragment，
        # 使上层（agent/纯对话）都不会注入「环境感知」块。
        from config.settings import settings as _cfg
        if not getattr(_cfg, "PERCEPTION_ENABLED", True):
            return ContextFragment(
                source="perception",
                content="",
                target_roles=(),
                section_title="环境感知",
                priority=5,
            )
        integrator = get_perception_integrator()
        return integrator.pool.snapshot()
