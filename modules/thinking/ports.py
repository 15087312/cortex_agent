"""思考编排边界的协议端口。"""
from __future__ import annotations

from typing import Any, Dict, List, Protocol, Tuple


class ActivityNotifierPort(Protocol):
    """通知可选观察者发生了用户可见的活动。"""

    def notify_activity(self) -> None:
        """如果运行时支持，则发出活动通知。"""


class SecurityPort(Protocol):
    """编排器使用的输入安全验证。"""

    def validate_input(self, user_input: str) -> Tuple[bool, str]:
        """返回是否允许输入以及可选的错误消息。"""


class ContextPort(Protocol):
    """编排器需要的上下文和记忆操作。"""

    def load_context(
        self,
        user_input: str,
        context: List[Dict[str, Any]],
        session_id: str | None,
    ) -> Tuple[str, Any]:
        """加载记忆/上下文文本并返回它以及记忆管理器。"""

    def inject_to_dialog(self, blackboard: Any, memory_context_text: str) -> None:
        """将检索到的上下文注入到共享对话中。"""

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
        """在编排完成后持久化对话/记忆。"""


class GuidancePort(Protocol):
    """生成前专家指导提供者。"""

    def run(self, user_input: str, memory_context_text: str) -> Dict[str, Any]:
        """为当前用户输入返回专家指导。"""


class OutputReviewPort(Protocol):
    """最终响应清理、专家审查和输出验证。"""

    def review(self, raw_response: str, user_input: str = "", expert_guidance: dict = None) -> str:
        """返回经过审查和验证后的用户可见响应。"""
