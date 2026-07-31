"""
ModelRunner — single-model execution engine.
Wraps ModelClient for single execution with streaming.
"""
from typing import Callable, List, Optional

from infra.model.model_client import ModelClient
from infra.model.base_model import ChatMessage, ChatResponse
from backend.config.settings import settings
from backend.utils.logger import setup_logger

logger = setup_logger("model_runner")


class ModelRunner:
    """Wraps a ModelClient for single execution."""

    def __init__(self, client: ModelClient = None):
        self._client = client

    @property
    def client(self) -> ModelClient:
        if self._client is None:
            from infra.model.model_client import get_model_client
            self._client = get_model_client()
        return self._client

    async def run(
        self,
        messages: List[dict],
        system_prompt: str,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> ChatResponse:
        """Execute a single model call with optional streaming.

        Args:
            messages: Conversation messages [{"role": ..., "content": ...}]
            system_prompt: System prompt text
            on_token: Optional callback for streaming tokens

        Returns:
            ChatResponse with assistant message
        """
        full_messages = [ChatMessage(role="system", content=system_prompt)]

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant", "system") and content:
                full_messages.append(ChatMessage(role=role, content=content))

        # DEBUG: log what we're sending to the model
        for i, m in enumerate(full_messages):
            logger.info(f"[MSG {i}] role={m.role} content={m.content[:120]}...")

        return await self.client.chat_stream(
            messages=full_messages,
            on_token=on_token,
            max_tokens=settings.MODEL_MAX_TOKENS,
            temperature=settings.MODEL_TEMPERATURE,
        )
