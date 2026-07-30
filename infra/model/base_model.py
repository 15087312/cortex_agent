"""
Model client base class — unified interface definition.
Simplified from reference: no tool calling support.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List, Callable
import aiohttp
import ssl
import json
import asyncio
from datetime import datetime

from backend.utils.logger import setup_logger

logger = setup_logger("model_client")


@dataclass
class ChatMessage:
    """Chat message."""
    role: str  # system / user / assistant
    content: Optional[str] = None
    reasoning_content: Optional[str] = None


@dataclass
class ChatResponse:
    """Chat response — text only."""
    message: ChatMessage
    finish_reason: str = "stop"
    usage: Optional[Dict[str, int]] = None


class BaseModelClient(ABC):
    """Model client base class."""

    def __init__(self, api_key: str, api_url: str, timeout: int = 120, allow_empty: bool = False):
        if not allow_empty and not api_key:
            raise ValueError("API key cannot be empty")
        if not allow_empty and not api_url:
            raise ValueError("API URL cannot be empty")

        self.api_key = api_key
        self.api_url = api_url
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None
        self._request_count = 0
        self._last_request_time: Optional[datetime] = None
        self._total_tokens_used = 0

    @abstractmethod
    async def generate(self, prompt: str, **kwargs) -> str:
        pass

    @abstractmethod
    async def chat(
        self,
        messages: List[ChatMessage],
        **kwargs,
    ) -> ChatResponse:
        pass

    async def chat_stream(
        self,
        messages: List[ChatMessage],
        on_token: Optional[Callable[[str], None]] = None,
        **kwargs,
    ) -> ChatResponse:
        """Streaming chat — default falls back to non-streaming."""
        response = await self.chat(messages, **kwargs)
        if on_token and response.message.content:
            on_token(response.message.content)
        return response

    @abstractmethod
    async def health_check(self) -> bool:
        pass

    @staticmethod
    def _create_ssl_context() -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_3
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        try:
            import certifi as _certifi
            ctx.load_verify_locations(cafile=_certifi.where())
        except Exception:
            ctx.load_default_certs(ssl.Purpose.SERVER_AUTH)
        try:
            ctx.set_ciphers('DEFAULT:@SECLEVEL=1')
        except ssl.SSLError:
            pass
        return ctx

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            import os
            from backend.config.settings import settings
            if settings.PROXY_URL:
                os.environ["HTTPS_PROXY"] = settings.PROXY_URL
                os.environ["HTTP_PROXY"] = settings.PROXY_URL
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            ssl_ctx = self._create_ssl_context()
            connector = aiohttp.TCPConnector(ssl=ssl_ctx, enable_cleanup_closed=True)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                trust_env=True,
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    @staticmethod
    def detect_api_format(url: str) -> str:
        if not url:
            return "openai"
        url_lower = url.lower()
        if "dashscope" in url_lower:
            return "dashscope"
        if "anthropic" in url_lower or "claude" in url_lower:
            return "anthropic"
        if any(k in url_lower for k in ("openai", "v1/chat", "v1/completions")):
            return "openai"
        return "openai"

    def _build_headers(self, api_format: str) -> Dict[str, str]:
        if api_format == "anthropic":
            return {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _messages_to_anthropic(self, messages: List[ChatMessage]) -> tuple:
        system_parts = []
        result = []
        for m in messages:
            if m.role == "system":
                if m.content:
                    system_parts.append(m.content)
                continue
            result.append({"role": m.role, "content": m.content or ""})
        return "\n\n".join(system_parts), result

    def _log_request(self, method: str, url: str, size: int = 0):
        self._request_count += 1
        self._last_request_time = datetime.now()
        logger.debug(f"[{method}] {url[:80]}... (payload: {size}B)")

    def _log_response_body(self, status: int, elapsed_ms: float, body: str, tokens: int = 0):
        self._total_tokens_used += tokens
        logger.debug(f"Response {status} ({elapsed_ms:.0f}ms, {tokens} tokens)")
