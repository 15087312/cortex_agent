"""思考编排端口的默认适配器。"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from utils.logger import setup_logger

logger = setup_logger("thinking_adapters")


class DifferenceDetectorActivityNotifier:
    """由可选差异检测器支持的活动通知器。"""

    def notify_activity(self) -> None:
        try:
            from modules.difference_detector import get_detector

            get_detector().notify_activity()
        except Exception as e:
            logger.debug(f"[活动通知] 差异检测器通知失败 (非致命): {e}")


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


class ContextManagerAdapter:
    """由 ContextManager 支持的上下文端口。"""

    async def load_context(
        self,
        user_input: str,
        context: List[Dict[str, Any]],
        session_id: str | None,
    ) -> Tuple[str, Any]:
        from modules.thinking.context import ContextManager

        return await ContextManager.load_context(user_input, context, session_id)

    def inject_to_dialog(self, blackboard: Any, memory_context_text: str) -> None:
        from modules.thinking.context import ContextManager

        ContextManager.inject_to_dialog(blackboard, memory_context_text)

    def save_memory(
        self,
        memory_manager: Any,
        session_id: str | None,
        user_input: str,
        final_response: str,
        *,
        gcm_pool: Any = None,
        turns: int = 0,
    ) -> None:
        from modules.thinking.context import ContextManager

        ContextManager.save_memory(
            memory_manager,
            session_id,
            user_input,
            final_response,
            gcm_pool=gcm_pool,
            turns=turns,
        )


class PreGenExpertGuidanceAdapter:
    """由 PreGenExpertPipeline 支持的指导端口。"""

    async def run(self, user_input: str, memory_context_text: str) -> Dict[str, Any]:
        try:
            from modules.thinking.experts.pre_gen_experts import PreGenExpertPipeline

            pipeline = PreGenExpertPipeline()
            guidance = await pipeline.run(
                user_input=user_input,
                memory_context=memory_context_text,
            )
            logger.info(
                f"[专家流水线] 风险={guidance.get('risk_level')} "
                f"准则={guidance.get('principle', '') or '无'}"
            )
            return guidance
        except Exception as e:
            logger.warning(f"专家流水线失败: {e}")
            return {}


class OutputSystemReviewAdapter:
    """由 OutputSystem 支持的输出审查端口。"""

    async def review(self, raw_response: str, user_input: str = "", expert_guidance: dict = None) -> str:
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
