"""
小模型调用客户端 - 云端 API（7B）

使用 OpenAI 兼容 API（DeepSeek）调用云端 7B 级模型。
"""
from typing import Dict, List, Optional, Any
from .base_model import BaseModelClient, ToolCall, ChatMessage, ChatResponse
from config.settings import settings
from utils.logger import setup_logger
from utils.error_reporter import report_api_error, report_exception
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
        tier_cfg = settings.resolve_model_tier("expert") if settings.SMALL_MODEL_PROVIDER else None
        if tier_cfg:
            key = api_key or tier_cfg.get("api_key") or key
            url = api_url or tier_cfg.get("base_url") or url
        super().__init__(key, url, timeout)
        self.model_name = model_name or settings.SMALL_MODEL_NAME
        if tier_cfg:
            self.model_name = tier_cfg.get("model") or self.model_name
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.logger = setup_logger("small_model_client")
        self.supports_native_tools = True
        self._api_format = self.detect_api_format(self.api_url)
        if tier_cfg and tier_cfg.get("api_format"):
            self._api_format = tier_cfg["api_format"]
        # 接线 config.providers 格式层（headers/请求体/URL 归一化统一由 Provider 负责）
        from config.providers.registry import get_provider
        self._provider = get_provider(self.model_name, key, url, self._api_format, settings.SMALL_MODEL_PROVIDER)
        # 注入 OpenRouter 路由的上游供应商偏好（设置页「OpenRouter 供应商」，仅 openrouter 生效）
        self._provider.openrouter_supplier = settings.SMALL_MODEL_OPENROUTER_SUPPLIER
        self._chat_url = self._provider.chat_url()

    @classmethod
    def from_config(cls) -> 'SmallModelClient':
        """从配置创建实例"""
        return cls(
            model_name=settings.SMALL_MODEL_NAME,
            max_tokens=512,
            temperature=0.3,
            api_key=settings.SMALL_MODEL_API_KEY or settings.LARGE_MODEL_API_KEY,
            api_url=settings.SMALL_MODEL_API_URL or settings.LARGE_MODEL_API_URL,
        )

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

        # api_messages 序列化保留客户端（tool_calls/tool_call_id 转换是客户端特有）
        api_messages = []
        for msg in messages:
            d: Dict[str, Any] = {"role": msg.role, "content": msg.content or ""}
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
            top_p=kwargs.get("top_p"),
        )

        max_retries = kwargs.get("max_retries", 3)
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                session = await self._get_session()
                self._log_request("POST", self._chat_url, len(json.dumps(payload)))
                async with session.post(self._chat_url, headers=headers, json=payload, timeout=self.timeout) as response:
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
            except asyncio.TimeoutError:
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

        # 响应解析统一由 Provider 负责（openai/anthropic；保留原"空 choices 抛错"语义）
        if self._api_format != "anthropic" and not data.get("choices"):
            raise Exception(f"Empty choices in small model response: {list(data.keys())}")
        parsed = self._provider.parse_response(data)
        raw_tool_calls = parsed.get("tool_calls")
        tool_calls = None
        if raw_tool_calls:
            tool_calls = [
                ToolCall(id=tc.get("id", ""), name=tc.get("name", ""), arguments=tc.get("arguments", "{}"))
                for tc in raw_tool_calls
            ]
        return ChatResponse(
            message=ChatMessage(role="assistant", content=parsed.get("content"), tool_calls=tool_calls),
            usage={"prompt_tokens": data.get("usage", {}).get("prompt_tokens", 0), "completion_tokens": data.get("usage", {}).get("completion_tokens", 0)},
        )

    async def generate(self, prompt: str, *, system_prompt: str, max_retries: int = 3,  # type: ignore[override]
                       fallback_to_reasoning: bool = False, **kwargs) -> str:
        """生成响应 - 使用 OpenAI / Anthropic 兼容 API，带重试机制

        Args:
            system_prompt: 系统提示词（必填）。单次调用不注入默认 agent 人设，
                调用方必须显式给出本次任务的身份/指令；缺失时抛 TypeError。
            fallback_to_reasoning: content 为空时是否用 reasoning_content 兜底。
                默认 False：思考过程（思维链）永远不冒充正式输出——content 为空即返回空，
                由调用方降级。仅当明确需要"以思维链为产物"的极少数场景才显式开 True。
        """
        if not system_prompt:
            raise TypeError("generate() 的 system_prompt 为必填参数，不能为空")
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]

        # 格式层统一由 Provider 构建（top_p 合并进 Provider 接口，openai 生效）
        headers = self._provider.build_headers()
        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        temp = kwargs.get("temperature", self.temperature)
        payload = self._provider.build_request(
            messages,
            max_tokens=max_tokens,
            temperature=temp,
            top_p=kwargs.get("top_p", 0.9),
        )

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                session = await self._get_session()
                self._log_request("POST", self._chat_url, len(json.dumps(payload)))
                async with session.post(self._chat_url, headers=headers, json=payload, timeout=self.timeout) as response:
                    if response.status == 200:
                        data = await response.json()
                        if self._api_format == "anthropic":
                            content_blocks = data.get("content", [])
                            texts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
                            return "\n".join(texts).strip() if texts else ""
                        choices = data.get("choices", [])
                        if choices:
                            message = choices[0].get("message", {})
                            content = message.get("content", "").strip()
                            # 处理 Reasoner 模型响应：content 为空时用 reasoning_content 兜底
                            #（需正式输出的场景调用方可传 fallback_to_reasoning=False，避免思维链当结果）
                            if not content and fallback_to_reasoning and "reasoning_content" in message:
                                reasoning = message.get("reasoning_content", "")
                                if reasoning:
                                    content = reasoning.strip()
                            return content
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
            response = await self.generate(
                "hi",
                system_prompt="你是健康检查助手，收到后请回复任意单词确认模型可用。",
                max_tokens=5,
            )
            return len(response) > 0
        except Exception as e:
            logger.warning(f"小模型健康检查失败: {e}")
            return False

    async def close(self):
        """关闭客户端"""
        await super().close()
