"""
ModelRunner — single-model execution engine.
Wraps LargeModelClient for single execution with streaming.
"""
from typing import Callable, List, Optional

from infra.model.large_model_client import LargeModelClient
from infra.model.base_model import ChatMessage, ChatResponse
from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("model_runner")


class ModelRunner:
    """Wraps a LargeModelClient for single execution."""

    def __init__(self, client: LargeModelClient = None):
        self._client = client

    @property
    def client(self) -> LargeModelClient:
        if self._client is None:
            self._client = LargeModelClient()
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
