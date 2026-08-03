"""思考编排端口的默认适配器。"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from utils.logger import setup_logger

logger = setup_logger("thinking_adapters")


class DifferenceDetectorActivityNotifier:
    """由感知系统支持的活动通知器 — 通知 idle timer 用户正在活动"""

    def notify_activity(self) -> None:
        try:
            from modules.perception.difference import get_detector
            get_detector().notify_activity()
        except Exception as e:
            logger.debug(f"[活动通知] 差异检测器通知失败 (非致命): {e}")

        try:
            from modules.perception.setup import get_perception_system
            ps = get_perception_system()
            if ps.proactive_trigger:
                ps.proactive_trigger.notify_activity()
        except Exception as e:
            logger.debug(f"[活动通知] 主动触发通知失败 (非致命): {e}")


class SecurityApiAdapter:
    """由 SecurityAPI 支持的安全端口。"""

    def validate_input(self, user_input: str) -> Tuple[bool, str]:
        try:
            from modules.security_system.api import get_security_api

            api = get_security_api()
            return api.validate_input(user_input)
        except Exception as e:
            logger.warning(f"[安全] 验证异常，拒绝输入: {e}")
            return False, f"[安全系统异常] 输入验证失败: {e}"


class PreGenExpertGuidanceAdapter:
    """由良知系统支持的指导端口。"""

    async def run(self, user_input: str, owner_id: str = "large_primary") -> Dict[str, Any]:
        try:
            from modules.thinking.conscience import get_conscience
            from infra.model.small_model_client import SmallModelClient
            from config.settings import settings

            client = SmallModelClient(
                api_key=settings.SMALL_MODEL_API_KEY or settings.LARGE_MODEL_API_KEY,
                api_url=settings.SMALL_MODEL_API_URL or settings.LARGE_MODEL_API_URL,
            )
            conscience = get_conscience()
            conscience._model_client = client
            thoughts = await conscience.think(user_input, owner_id=owner_id)
            return {"inner_thoughts": thoughts} if thoughts else {}
        except Exception as e:
            logger.warning(f"良知系统失败: {e}")
            return {}


class OutputSystemReviewAdapter:
    """由 OutputSystem 支持的输出审查端口。"""

    async def review(self, raw_response: str, user_input: str = "") -> str:
        """只做输出清洗（格式化），不做安全拦截。

        安全拦截由 SecurityMonitor 在 Blackboard 层面处理，
        OutputSystem 只负责统一输出格式。
        """
        if not raw_response:
            return ""

        try:
            from modules.output_system.core import OutputSystem
            return OutputSystem.clean_response(raw_response)
        except Exception as e:
            logger.debug(f"[输出清洗] clean_response 失败，使用原始响应: {e}")
            return raw_response
