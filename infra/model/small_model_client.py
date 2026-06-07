"""
小模型调用客户端 - 云端 API（7B）

使用 OpenAI 兼容 API（DeepSeek）调用云端 7B 级模型。
"""
from typing import Any, Dict, List, Optional
from .base_model import BaseModelClient, ToolCall, ChatMessage, ChatResponse
from config.model_config import SmallModelConfig, get_small_model_config
from config.settings import settings
from utils.logger import setup_logger
from modules.management import report_api_error, report_exception
import aiohttp
import asyncio
import json

logger = setup_logger("small_model_client")


class SmallModelClient(BaseModelClient):
    """小模型客户端 - 云端 API（7B）

    用于快速响应任务，如情感分析、简单分类等。
    使用 OpenAI 兼容 API（DeepSeek）调用云端 7B 级模型。

    默认配置:
        - model_name: deepseek-v4-flash
        - max_tokens: 512
        - temperature: 0.3
    """

    def __init__(
        self,
        model_name: str = None,
        max_tokens: int = 512,
        temperature: float = 0.3,
        api_key: str = None,
        api_url: str = None,
        timeout: int = 30,
    ):
        key = api_key or settings.SMALL_MODEL_API_KEY or settings.LARGE_MODEL_API_KEY
        url = api_url or settings.SMALL_MODEL_API_URL or settings.LARGE_MODEL_API_URL
        super().__init__(key, url, timeout)
        self.model_name = model_name or settings.SMALL_MODEL_NAME
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.logger = setup_logger("small_model_client")
        self.supports_native_tools = True
        self._api_format = self.detect_api_format(self.api_url)

    @classmethod
    def from_config(cls) -> 'SmallModelClient':
        """从配置创建实例"""
        config = get_small_model_config()
        return cls(
            model_name=config.model_name,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            api_key=config.api_key,
            api_url=config.api_url,
        )

    async def chat(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict]] = None,
        **kwargs,
    ) -> ChatResponse:
        """带原生工具调用的对话生成 (OpenAI / Anthropic 双格式)"""
        headers = self._build_headers(self._api_format)
        model = kwargs.get("model", self.model_name)
        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        temperature = kwargs.get("temperature", self.temperature)
        tool_choice = kwargs.get("tool_choice")

        if self._api_format == "anthropic":
            system_text, api_messages = self._messages_to_anthropic(messages)
            payload = {"model": model, "max_tokens": max_tokens, "messages": api_messages}
            if system_text:
                payload["system"] = system_text
            if tools:
                payload["tools"] = self._tools_to_anthropic(tools)
            if tool_choice:
                if isinstance(tool_choice, dict) and "function" in tool_choice:
                    payload["tool_choice"] = {"type": "tool", "name": tool_choice["function"].get("name", "")}
                elif tool_choice == "required":
                    payload["tool_choice"] = {"type": "any"}
                elif tool_choice == "auto":
                    payload["tool_choice"] = {"type": "auto"}
        else:
            api_messages = []
            for msg in messages:
                d = {"role": msg.role, "content": msg.content or ""}
                if msg.tool_calls:
                    d["tool_calls"] = [
                        {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
                        for tc in msg.tool_calls
                    ]
                if msg.tool_call_id:
                    d["tool_call_id"] = msg.tool_call_id
                api_messages.append(d)
            payload = {"model": model, "messages": api_messages, "max_tokens": max_tokens, "temperature": temperature}
            if tools:
                payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice

        max_retries = kwargs.get("max_retries", 3)
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                session = await self._get_session()
                async with session.post(self.api_url, headers=headers, json=payload, timeout=self.timeout) as response:
                    if response.status == 200:
                        data = await response.json()
                        break
                    else:
                        error_text = await response.text()
                        last_error = Exception(f"API request failed: {response.status} - {error_text}")
                        if attempt < max_retries:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        raise last_error
            except asyncio.TimeoutError as e:
                last_error = Exception(f"Small model chat timeout (attempt {attempt}/{max_retries})")
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise last_error
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise

        if self._api_format == "anthropic":
            return self._parse_anthropic_response(data)

        choices = data.get("choices", [])
        if not choices:
            raise Exception(f"Empty choices in small model response: {list(data.keys())}")
        choice = choices[0]
        message = choice.get("message", {})
        content = message.get("content", "") or ""
        raw_tool_calls = message.get("tool_calls")
        tool_calls = []
        if raw_tool_calls:
            for tc in raw_tool_calls:
                func = tc.get("function", {})
                tool_calls.append(ToolCall(id=tc.get("id", ""), name=func.get("name", ""), arguments=func.get("arguments", "{}")))
        return ChatResponse(
            message=ChatMessage(role="assistant", content=content, tool_calls=tool_calls if tool_calls else None),
            usage={"prompt_tokens": data.get("usage", {}).get("prompt_tokens", 0), "completion_tokens": data.get("usage", {}).get("completion_tokens", 0)},
        )

    async def generate(self, prompt: str, max_retries: int = 3, **kwargs) -> str:
        """生成响应 - 使用 OpenAI / Anthropic 兼容 API，带重试机制"""
        headers = self._build_headers(self._api_format)
        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        temp = kwargs.get("temperature", self.temperature)

        if self._api_format == "anthropic":
            payload = {
                "model": self.model_name,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
        else:
            payload = {
                "model": self.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temp,
                "top_p": kwargs.get("top_p", 0.9),
            }

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                session = await self._get_session()
                async with session.post(self.api_url, headers=headers, json=payload, timeout=self.timeout) as response:
                    if response.status == 200:
                        data = await response.json()
                        if self._api_format == "anthropic":
                            content_blocks = data.get("content", [])
                            texts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
                            return "\n".join(texts).strip() if texts else ""
                        choices = data.get("choices", [])
                        if choices:
                            return choices[0].get("message", {}).get("content", "").strip()
                        raise Exception("No choices in response")
                    else:
                        error_text = await response.text()
                        try:
                            error_data = json.loads(error_text)
                        except (json.JSONDecodeError, ValueError):
                            error_data = error_text
                        report_api_error(Exception(f"API request failed: {response.status} - {error_data}"),
                            module="infra.model.small_model_client", function="generate",
                            status_code=response.status, request={"model": self.model_name, "api_url": self.api_url},
                            response=error_data, source="model_api")
                        raise Exception(f"API request failed: {response.status} - {error_data}")
            except asyncio.TimeoutError as e:
                last_error = Exception(f"Small model timeout (attempt {attempt}/{max_retries})")
                report_exception(e, module="infra.model.small_model_client", function="generate",
                    context={"api_url": self.api_url, "model": self.model_name}, source="model_api")
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                break

        raise last_error or Exception("Small model request failed after all retries")

    async def health_check(self) -> bool:
        """健康检查"""
        try:
            response = await self.generate("hi", max_tokens=5)
            return len(response) > 0
        except Exception as e:
            logger.warning(f"小模型健康检查失败: {e}")
            return False

    async def close(self):
        """关闭客户端"""
        await super().close()
