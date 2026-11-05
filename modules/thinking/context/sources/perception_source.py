"""PerceptionSource — 感知系统 → TurnContext 接入点"""
# ContextFragment 来自 infra 契约模块：本文件虽在 thinking 内，但 Fragment 是
# 感知/记忆/良知共享的数据单位，走 infra 可避免与 perception 形成逻辑循环。
from infra.context_types import ContextFragment


class PerceptionSource:
    """从 PerceptionPool 取快照 → ContextFragment"""

    async def collect(self) -> ContextFragment:
        # 经端口取用感知整合器，避免 thinking → perception 模块边
        from infra.ports import resolve
        integrator = resolve("perception.integrator")
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
        if integrator is None:
            # 感知模块未加载：返回空片段而非报错，与「总开关关闭」同语义，
            # 使 agent/纯对话路径不会因感知缺席而中断。
            return ContextFragment(
                source="perception",
                content="",
                target_roles=(),
                section_title="环境感知",
                priority=5,
            )
        return integrator.pool.snapshot()
