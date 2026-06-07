"""
中模型调用客户端 - API 调用（32B 级）
"""
from .base_model import BaseModelClient, ChatMessage, ChatResponse, ToolCall
import aiohttp
import asyncio
import json
from typing import Dict, List, Optional
from config.model_config import MediumModelConfig, get_medium_model_config
from config.settings import settings
from infra.prompts import prompt_manager
from modules.management import report_api_error, report_exception
from utils.logger import setup_logger

logger = setup_logger("medium_model_client")


class MediumModelClient(BaseModelClient):
    """中模型客户端 - API 调用（DeepSeek 32B 级）

    用于任务分解、专家调度、结果汇总等主管职责。

    默认配置:
        - model_name: deepseek-v4-flash
        - max_tokens: 1024 (增加以支持更深度推理)
        - temperature: 0.1
    """

    def __init__(self, config: MediumModelConfig = None, api_key: str = None, api_url: str = None, timeout: int = 60):
        """初始化中模型客户端

        Args:
            config: 配置对象
            api_key: API 密钥
            api_url: API 地址
            timeout: 超时时间（秒）- 默认60秒支持深度推理
        """
        if config:
            super().__init__(config.api_key, config.api_url, config.timeout)
            self.max_tokens = config.max_tokens
            self.temperature = config.temperature
            self.model_name = config.model_name or settings.MEDIUM_MODEL_NAME
        else:
            super().__init__(api_key or "", api_url or "", timeout)
            self.max_tokens = 1024  # 增加 token 支持深度推理
            self.temperature = 0.1
            self.model_name = settings.MEDIUM_MODEL_NAME
        self.supports_native_tools = True
        self._api_format = self.detect_api_format(self.api_url)

    @classmethod
    def from_config(cls) -> 'MediumModelClient':
        """从配置文件创建实例"""
        config = get_medium_model_config()
        return cls(config=config)

    # ------------------------------------------------------------------
    # 原生工具调用 chat()
    # ------------------------------------------------------------------

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

        max_retries = kwargs.get("max_retries", 2)
        last_error = None
        data = None

        for attempt in range(1, max_retries + 1):
            try:
                session = await self._get_session()
                async with session.post(
                    self.api_url, headers=headers, json=payload, timeout=self.timeout
                ) as response:
                    if response.status != 200:
                        error_body = await response.text()
                        logger.error(f"[MediumModelClient] chat 调用失败: status={response.status}, body={error_body[:500]}")
                        raise Exception(f"API request failed: {response.status} - {error_body[:500]}")
                    data = await response.json()
                    break
            except asyncio.TimeoutError:
                last_error = Exception(f"Medium model chat timeout (attempt {attempt}/{max_retries})")
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise last_error
            except Exception as e:
                last_error = e
                logger.error(f"[MediumModelClient] chat attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise

        if self._api_format == "anthropic":
            return self._parse_anthropic_response(data)

        choices = data.get("choices", [])
        if not choices:
            raise Exception(f"Empty choices in medium model response: {list(data.keys())}")
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

    async def generate(self, prompt: str, **kwargs) -> str:
        """生成响应 - 自动添加系统提示词（支持 OpenAI / Anthropic）"""

        # 构建完整提示词
        full_prompt = prompt_manager.build_medium_model_prompt(user_input=prompt)

        headers = self._build_headers(self._api_format)

        if self._api_format == "anthropic":
            payload = {
                "model": self.model_name,
                "max_tokens": kwargs.get("max_tokens", self.max_tokens),
                "messages": [{"role": "user", "content": full_prompt}],
            }
        else:
            payload = {
                "model": self.model_name,
                "messages": [{"role": "user", "content": full_prompt}],
                "max_tokens": kwargs.get("max_tokens", self.max_tokens),
                "temperature": kwargs.get("temperature", self.temperature),
                "stream": False,
            }

        try:
            session = await self._get_session()
            async with session.post(self.api_url, headers=headers, json=payload, timeout=self.timeout) as response:
                if response.status == 200:
                    data = await response.json()
                    if self._api_format == "anthropic":
                        content_blocks = data.get("content", [])
                        texts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
                        return "\n".join(texts) if texts else ""
                    choices = data.get("choices", [])
                    if choices:
                        message = choices[0].get("message", {})
                        content = message.get("content", "")
                        if not content and "reasoning_content" in message:
                            reasoning = message.get("reasoning_content", "")
                            if reasoning:
                                content = reasoning
                                logger.debug(f"[medium] 使用 reasoning_content: {len(content)} 字符")
                        return content
                    return ""
                else:
                    error_text = await response.text()
                    logger.error(f"[medium] API error status {response.status}: {error_text[:500]}")
                    raise Exception(f"API request failed: {response.status} - {error_text}")
        except asyncio.TimeoutError as e:
            logger.error(f"[medium] Timeout error: {e}")
            report_exception(e, module="infra.model.medium_model_client", function="generate",
                             context={"api_url": self.api_url, "model": self.model_name}, source="model_api")
            raise Exception("Medium model request timeout")
        except Exception as e:
            logger.error(f"[medium] Generate error: {e}")
            raise

    async def health_check(self) -> bool:
        """健康检查"""
        try:
            test_response = await self.generate("你好", max_tokens=10)
            return len(test_response) > 0
        except Exception as e:
            logger.warning(f"中模型健康检查失败: {e}")
            return False