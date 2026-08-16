"""
中模型调用客户端 - API 调用（32B 级）
"""
from .base_model import BaseModelClient, ChatMessage, ChatResponse, ToolCall
import asyncio
import json
from typing import Dict, List, Optional, Any
from config.settings import settings
from utils.error_reporter import report_exception
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

    def __init__(self, api_key: str = None, api_url: str = None, timeout: int = 60):
        """初始化中模型客户端

        Args:
            api_key: API 密钥
            api_url: API 地址
            timeout: 超时时间（秒）- 默认60秒支持深度推理
        """
        key = api_key or settings.MEDIUM_MODEL_API_KEY or settings.LARGE_MODEL_API_KEY
        url = api_url or settings.MEDIUM_MODEL_API_URL
        super().__init__(key, url, timeout)
        self.max_tokens = 1024
        self.temperature = 0.1
        self.model_name = settings.MEDIUM_MODEL_NAME
        self.supports_native_tools = True
        self._api_format = self.detect_api_format(self.api_url)
        # 接线 config.providers 格式层（headers/请求体/URL 归一化统一由 Provider 负责）
        from config.providers.registry import get_provider
        self._provider = get_provider(self.model_name, key, url, self._api_format)
        self._chat_url = self._provider.chat_url()

    @classmethod
    def from_config(cls) -> 'MediumModelClient':
        """从配置创建实例"""
        return cls()

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
        # 格式层统一由 Provider 构建（headers/请求体；anthropic system 提取内含）
        headers = self._provider.build_headers()
        model = kwargs.get("model", self.model_name)
        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        temperature = kwargs.get("temperature", self.temperature)
        tool_choice = kwargs.get("tool_choice")

        # api_messages 序列化保留客户端（reasoning_content 回传 / tool_calls / tool_call_id）
        api_messages = []
        for msg in messages:
            d: Dict[str, Any] = {"role": msg.role, "content": msg.content or ""}
            if msg.reasoning_content:
                d["reasoning_content"] = msg.reasoning_content
            if msg.tool_calls:
                d["tool_calls"] = [
                    {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
                    for tc in msg.tool_calls
                ]
            if msg.tool_call_id:
                d["tool_call_id"] = msg.tool_call_id
            api_messages.append(d)

        if model != self._provider.model_name:
            self._provider.model_name = model
        payload = self._provider.build_request(
            api_messages,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            tool_choice=tool_choice,
        )

        max_retries = kwargs.get("max_retries", 2)
        last_error = None
        data: Any = None

        for attempt in range(1, max_retries + 1):
            try:
                session = await self._get_session()
                self._log_request("POST", self._chat_url, len(json.dumps(payload)))
                async with session.post(
                    self._chat_url, headers=headers, json=payload, timeout=self.timeout
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

        # 响应解析统一由 Provider 负责（openai 含 reasoning_content / anthropic content blocks）
        if self._api_format != "anthropic" and not data.get("choices"):
            raise Exception(f"Empty choices in medium model response: {list(data.keys())}")
        parsed = self._provider.parse_response(data)
        raw_tool_calls = parsed.get("tool_calls")
        tool_calls = None
        if raw_tool_calls:
            tool_calls = [
                ToolCall(id=tc.get("id", ""), name=tc.get("name", ""), arguments=tc.get("arguments", "{}"))
                for tc in raw_tool_calls
            ]
        return ChatResponse(
            message=ChatMessage(
                role="assistant",
                content=parsed.get("content"),
                tool_calls=tool_calls,
                reasoning_content=parsed.get("reasoning_content"),  # thinking模式
            ),
            usage={"prompt_tokens": data.get("usage", {}).get("prompt_tokens", 0), "completion_tokens": data.get("usage", {}).get("completion_tokens", 0)},
        )

    async def generate(self, prompt: str, *, system_prompt: str,  # type: ignore[override]
                       fallback_to_reasoning: bool = False, **kwargs) -> str:
        """生成响应（支持 OpenAI / Anthropic）

        Args:
            system_prompt: 系统提示词（必填）。单次调用不注入默认 agent 人设，
                调用方必须显式给出本次任务的身份/指令；缺失时抛 TypeError。
            fallback_to_reasoning: content 为空时是否用 reasoning_content 兜底。
                默认 False：思考过程不冒充正式输出，content 为空即返回空（§51）。
        """
        if not system_prompt:
            raise TypeError("generate() 的 system_prompt 为必填参数，不能为空")
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]

        # 格式层统一由 Provider 构建（headers/请求体；anthropic system 提取内含）
        headers = self._provider.build_headers()
        payload = self._provider.build_request(
            messages,
            max_tokens=kwargs.get("max_tokens", self.max_tokens),
            temperature=kwargs.get("temperature", self.temperature),
        )

        try:
            session = await self._get_session()
            async with session.post(self._chat_url, headers=headers, json=payload, timeout=self.timeout) as response:
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
                        # 默认不兜底：思考过程（reasoning_content）不冒充正式输出
                        if not content and fallback_to_reasoning and "reasoning_content" in message:
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
            test_response = await self.generate(
                "你好",
                system_prompt="你是健康检查助手，收到后请回复任意单词确认模型可用。",
                max_tokens=10,
            )
            return len(test_response) > 0
        except Exception as e:
            logger.warning(f"中模型健康检查失败: {e}")
            return False