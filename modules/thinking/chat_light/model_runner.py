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
        if client is not None:
            # 显式注入的 client：调用方显式控制，记录当前配置指纹，不自动重建
            from infra.model.config_fingerprint import model_config_fingerprint
            self._client_cfg = model_config_fingerprint("large")
        else:
            # 懒建：None 表示首次访问需按当前配置构建
            self._client_cfg = None

    @property
    def client(self) -> LargeModelClient:
        """懒建并缓存 LargeModelClient；模型配置（URL/Key/名称/格式）变更时自动重建，
        使设置页修改实时生效而无需重启。"""
        from infra.model.config_fingerprint import (
            model_config_fingerprint, close_client_session,
        )
        cfg = model_config_fingerprint("large")
        if self._client is None or self._client_cfg != cfg:
            old = self._client
            self._client = LargeModelClient()
            self._client_cfg = cfg
            close_client_session(old)
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
            logger.info(f"[MSG {i}] role={m.role} content={(m.content or '')[:120]}...")

        return await self.client.chat_stream(
            messages=full_messages,
            on_token=on_token,
            max_tokens=settings.MODEL_MAX_TOKENS,
            temperature=settings.MODEL_TEMPERATURE,
        )
