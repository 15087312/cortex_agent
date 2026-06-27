"""PerceptionSource — 感知系统 → TurnContext 接入点"""
from modules.thinking.context.pool import ContextFragment


class PerceptionSource:
    """从 PerceptionPool 取快照 → ContextFragment"""

    async def collect(self) -> ContextFragment:
        from modules.perception.integration import get_perception_integrator
        integrator = get_perception_integrator()
        return integrator.pool.snapshot()
