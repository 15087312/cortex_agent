"""
Unified model client — supports OpenAI / Anthropic / DashScope with auto-detection.
Ported from reference large_model_client.py, simplified to single client.
"""
from .base_model import BaseModelClient, ChatMessage, ChatResponse
from typing import Callable, Dict, List, Optional
import json

from backend.config.settings import settings
from backend.utils.logger import setup_logger

logger = setup_logger("model_client")


class ModelClient(BaseModelClient):
    """Unified LLM client with auto-detection."""

    def __init__(
        self,
        api_key: str = None,
        api_url: str = None,
        timeout: int = 120,
        api_format: str = "",
    ):
        key = api_key or settings.MODEL_API_KEY
        url = api_url or settings.MODEL_API_URL
        super().__init__(key, url, timeout)
        self.model_name = settings.MODEL_NAME
        self._api_format = api_format or settings.MODEL_API_FORMAT

        if not self._api_format:
            self._api_format = self.detect_api_format(self.api_url)

        logger.info(f"ModelClient initialized: format={self._api_format}, url={self.api_url[:60]}...")

    async def generate(self, prompt: str, max_retries: int = 2, **kwargs) -> str:
        messages = [
            ChatMessage(role="system", content="You are a helpful assistant."),
            ChatMessage(role="user", content=prompt),
        ]
        response = await self.chat(messages, **kwargs)
        return response.message.content or ""

    async def chat(
        self,
        messages: List[ChatMessage],
        max_retries: int = 2,
        **kwargs,
    ) -> ChatResponse:
        max_tokens = kwargs.get("max_tokens", settings.MODEL_MAX_TOKENS)
        temperature = kwargs.get("temperature", settings.MODEL_TEMPERATURE)

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                session = await self._get_session()

                if self._api_format == "anthropic":
                    payload = self._build_anthropic_payload(messages, max_tokens, temperature)
                    url = self.api_url
                else:
                    payload = self._build_openai_payload(messages, max_tokens, temperature)
                    url = self.api_url

                headers = self._build_headers(self._api_format)
                self._log_request("POST", url, len(json.dumps(payload)))
                self._log_payload(payload)

                async with session.post(url, headers=headers, json=payload) as response:
                    if response.status == 200:
                        raw = await response.text()
                        data = json.loads(raw)

                        if self._api_format == "anthropic":
                            return self._parse_anthropic_response(data)
                        else:
                            return self._parse_openai_response(data)
                    else:
                        error_body = await response.text()
                        last_error = f"HTTP {response.status}: {error_body[:200]}"
                        logger.warning(f"API error (attempt {attempt}): {last_error}")

            except Exception as e:
                last_error = str(e)
                logger.warning(f"Request error (attempt {attempt}): {last_error}")

        raise RuntimeError(f"Model API failed after {max_retries} attempts: {last_error}")

    async def chat_stream(
        self,
        messages: List[ChatMessage],
        on_token: Optional[Callable[[str], None]] = None,
        **kwargs,
    ) -> ChatResponse:
        max_tokens = kwargs.get("max_tokens", settings.MODEL_MAX_TOKENS)
        temperature = kwargs.get("temperature", settings.MODEL_TEMPERATURE)

        if self._api_format == "anthropic":
            return await self._stream_anthropic(messages, on_token, max_tokens, temperature)
        else:
            return await self._stream_openai(messages, on_token, max_tokens, temperature)

    async def _stream_openai(
        self,
        messages: List[ChatMessage],
        on_token: Optional[Callable[[str], None]],
        max_tokens: int,
        temperature: float,
    ) -> ChatResponse:
        payload = self._build_openai_payload(messages, max_tokens, temperature, stream=True)
        headers = self._build_headers("openai")
        url = self.api_url

        # 打印发送给 LLM 的动态上下文（固定 system 提示词省略）
        logger.info("=" * 60)
        logger.info("API REQUEST PAYLOAD:")
        for i, m in enumerate(messages):
            role = m.role
            if role == "system":
                logger.info(f"  [{i}] system: <固定提示词，省略>")
                continue
            content = (m.content or '')[:200]
            logger.info(f"  [{i}] {role}: {content}")
        logger.info("=" * 60)

        session = await self._get_session()
        self._log_request("POST (stream)", url, len(json.dumps(payload)))

        full_content = []
        finish_reason = "stop"
        usage = None

        async with session.post(url, headers=headers, json=payload) as response:
            if response.status != 200:
                error_body = await response.text()
                raise RuntimeError(f"Stream API error: HTTP {response.status}: {error_body[:200]}")

            async for line in response.content:
                line = line.decode("utf-8").strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    if "content" in delta and delta["content"]:
                        token = delta["content"]
                        full_content.append(token)
                        if on_token:
                            on_token(token)
                    if data.get("choices", [{}])[0].get("finish_reason"):
                        finish_reason = data["choices"][0]["finish_reason"]
                    if "usage" in data:
                        usage = data["usage"]
                except json.JSONDecodeError:
                    continue

        content = "".join(full_content)
        logger.info(f"[STREAM DONE] chunks={len(full_content)} chars={len(content)} content={content[:200]}")
        return ChatResponse(
            message=ChatMessage(role="assistant", content=content),
            finish_reason=finish_reason,
            usage=usage,
        )

    async def _stream_anthropic(
        self,
        messages: List[ChatMessage],
        on_token: Optional[Callable[[str], None]],
        max_tokens: int,
        temperature: float,
    ) -> ChatResponse:
        system_text, anthropic_messages = self._messages_to_anthropic(messages)
        payload = {
            "model": self.model_name,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "messages": anthropic_messages,
        }
        if system_text:
            payload["system"] = system_text

        headers = self._build_headers("anthropic")
        url = self.api_url
        session = await self._get_session()
        self._log_request("POST (stream)", url, len(json.dumps(payload)))
        self._log_payload(payload)

        full_content = []
        finish_reason = "stop"

        async with session.post(url, headers=headers, json=payload) as response:
            if response.status != 200:
                error_body = await response.text()
                raise RuntimeError(f"Anthropic stream error: HTTP {response.status}: {error_body[:200]}")

            async for line in response.content:
                line = line.decode("utf-8").strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                try:
                    data = json.loads(data_str)
                    event_type = data.get("type", "")

                    if event_type == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            token = delta.get("text", "")
                            full_content.append(token)
                            if on_token:
                                on_token(token)

                    elif event_type == "message_stop":
                        finish_reason = "end_turn"

                except json.JSONDecodeError:
                    continue

        return ChatResponse(
            message=ChatMessage(role="assistant", content="".join(full_content)),
            finish_reason=finish_reason,
        )

    def _build_openai_payload(
        self,
        messages: List[ChatMessage],
        max_tokens: int,
        temperature: float,
        stream: bool = False,
    ) -> Dict:
        formatted = []
        for m in messages:
            formatted.append({"role": m.role, "content": m.content or ""})
        payload = {
            "model": self.model_name,
            "messages": formatted,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if stream:
            payload["stream"] = True
        return payload

    def _build_anthropic_payload(
        self,
        messages: List[ChatMessage],
        max_tokens: int,
        temperature: float,
        stream: bool = False,
    ) -> Dict:
        system_text, anthropic_messages = self._messages_to_anthropic(messages)
        payload = {
            "model": self.model_name,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": anthropic_messages,
        }
        if system_text:
            payload["system"] = system_text
        if stream:
            payload["stream"] = True
        return payload

    @staticmethod
    def _parse_openai_response(data: Dict) -> ChatResponse:
        choices = data.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            return ChatResponse(
                message=ChatMessage(role="assistant", content=message.get("content", "")),
                finish_reason=choices[0].get("finish_reason", "stop"),
                usage=data.get("usage"),
            )
        return ChatResponse(message=ChatMessage(role="assistant", content=""))

    @staticmethod
    def _parse_anthropic_response(data: Dict) -> ChatResponse:
        content_blocks = data.get("content", [])
        texts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
        return ChatResponse(
            message=ChatMessage(role="assistant", content="\n".join(texts)),
            finish_reason=data.get("stop_reason", "end_turn"),
            usage=data.get("usage"),
        )

    async def health_check(self) -> bool:
        try:
            response = await self.generate("Say 'ok' in one word.")
            return bool(response.strip())
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False


# Global singleton
_model_client: Optional[ModelClient] = None


def get_model_client() -> ModelClient:
    global _model_client
    if _model_client is None:
        _model_client = ModelClient()
    return _model_client
