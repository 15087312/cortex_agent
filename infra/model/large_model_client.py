"""
大模型调用客户端 - 封装 API、异步优先
"""
from .base_model import BaseModelClient, ChatMessage, ChatResponse, ToolCall
from typing import AsyncGenerator, Callable, Dict, Any, List, Optional
import aiohttp
import asyncio
import json
import ssl
import time
from config.settings import settings
from utils.error_reporter import report_api_error, report_exception
from utils.logger import get_logger
from infra.tool_manager.service_registry import get_capability

logger = get_logger(__name__)


class LargeModelClient(BaseModelClient):
    """大模型调用客户端 — 支持 DashScope / OpenAI 两种 API 格式"""

    def __init__(
        self,
        api_key: str = None,
        api_url: str = None,
        timeout: int = 120,
        api_format: str = "",
    ):
        key = api_key or settings.LARGE_MODEL_API_KEY
        url = api_url or settings.LARGE_MODEL_API_URL
        super().__init__(key, url, timeout or settings.MODEL_TIMEOUT)
        self.max_tokens = 4096
        self.temperature = 0.7
        self.model_name = settings.LARGE_MODEL_NAME
        self._api_format = api_format or settings.LARGE_MODEL_API_FORMAT

        # 自动检测 API 格式
        if not self._api_format:
            self._api_format = self._detect_api_format(self.api_url)
        self.supports_native_tools = True
        # 接线 config.providers 格式层（headers/请求体/URL 归一化统一由 Provider 负责；
        # 响应/流式解析保留客户端特有能力，见 config/providers/registry.py）
        from config.providers.registry import get_provider
        self._provider = get_provider(self.model_name, key, url, self._api_format)
        # chat_url() 做 /v1、/chat/completions、/messages 归一化，消除"配 /v1 直接 404"
        self._chat_url = self._provider.chat_url()
        logger.info(
            f"[LargeModelClient] API 格式: {self._api_format}, URL: {self._chat_url[:60]}..."
        )

    @classmethod
    def from_config(cls) -> 'LargeModelClient':
        """从配置创建实例"""
        return cls()

    @staticmethod
    def _detect_api_format(url: str) -> str:
        """从 URL 自动检测 API 格式"""
        if not url:
            return "dashscope"
        url_lower = url.lower()
        if "dashscope" in url_lower:
            return "dashscope"
        if "anthropic" in url_lower or "claude" in url_lower:
            return "anthropic"
        if any(k in url_lower for k in ("openai", "v1/chat", "v1/completions")):
            return "openai"
        # 默认兼容 DashScope（原有用户不受影响）
        return "dashscope"
    
    async def generate(self, prompt: str, *, system_prompt: str, max_retries: int = 2,  # type: ignore[override]
                       fallback_to_reasoning: bool = False, **kwargs) -> str:
        """生成响应 - 支持 DashScope / OpenAI / Anthropic，带重试机制

        Args:
            system_prompt: 系统提示词（必填）。单次调用不注入默认 agent 人设，
                调用方必须显式给出本次任务的身份/指令；缺失时抛 TypeError。
            fallback_to_reasoning: content 为空时是否用 reasoning_content 兜底。
                默认 False：思考过程不冒充正式输出，content 为空即返回空（§51）。
        """
        if not system_prompt:
            raise TypeError("generate() 的 system_prompt 为必填参数，不能为空")

        # 格式层统一由 Provider 构建（anthropic system 提取 / openai messages / dashscope input）
        headers = self._provider.build_headers()
        payload = self._provider.build_request(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            max_tokens=kwargs.get("max_tokens", self.max_tokens),
            temperature=kwargs.get("temperature", self.temperature),
        )

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                # RES-1: Reuse pooled session instead of creating new one per request
                session = await self._get_session()
                self._log_request("POST", self._chat_url, len(json.dumps(payload)))
                self._log_payload(payload)
                async with session.post(
                    self._chat_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout
                ) as response:
                    content_type = response.headers.get("Content-Type", "")
                    logger.debug(f"[generate] Status: {response.status}, Content-Type: {content_type}")

                    if response.status == 200:
                        # Try to parse JSON regardless of Content-Type
                        try:
                            t1 = time.time()
                            raw = await response.text()
                            elapsed = (t1 - time.time()) * 1000
                            data = json.loads(raw)
                            self._log_response_body(200, elapsed, raw, tokens=data.get("usage",{}).get("total_tokens",0))
                            logger.debug(f"[generate] API response keys: {data.keys()}")

                            # Anthropic 格式响应
                            if self._api_format == "anthropic":
                                content_blocks = data.get("content", [])
                                if content_blocks:
                                    # 合并所有 text block
                                    texts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
                                    return "\n".join(texts) if texts else ""
                                return ""

                            # OpenAI/DashScope 格式响应
                            choices = data.get("choices", [])
                            logger.debug(f"[generate] choices count: {len(choices)}")
                            if choices:
                                message = choices[0].get("message", {})
                                content = message.get("content", "")

                                # 默认不兜底：思考过程（reasoning_content）不冒充正式输出
                                if not content and fallback_to_reasoning and "reasoning_content" in message:
                                    reasoning = message.get("reasoning_content", "")
                                    if reasoning:
                                        content = reasoning
                                        logger.debug(f"[generate] 使用 reasoning_content: {len(content)} 字符")

                                logger.debug(f"[generate] content length: {len(content) if content else 0}")
                                return content
                            logger.warning(f"[generate] Empty choices array in response: {data}")
                            return ""
                        except ValueError as je:
                            # JSON parsing failed, try to read as text
                            logger.warning(f"[generate] JSON decode failed (Content-Type: {content_type}), trying text fallback: {je}")
                            try:
                                text = await response.text()
                                logger.warning(f"[generate] Response body: {text[:200]}")
                                # If it's HTML or error message, treat as error
                                if text.strip().startswith("<"):
                                    raise Exception("Got HTML response instead of JSON")
                                # If we got text that looks like error, return empty and retry
                                return ""
                            except Exception as e:
                                # If even text reading fails, return empty and retry
                                logger.warning(f"读取大模型响应文本失败: {e}")
                                return ""
                    else:
                        # Non-200 status - try to get error details
                        # Always use text() first to avoid ContentTypeError from aiohttp
                        error_text = await response.text()
                        try:
                            error_data = json.loads(error_text)
                        except (json.JSONDecodeError, ValueError):
                            error_data = error_text
                        logger.error(f"[generate] API error status {response.status}: {error_data}")
                        report_api_error(
                            Exception(f"API request failed: {response.status} - {error_data}"),
                            module="infra.model.large_model_client",
                            function="generate",
                            status_code=response.status,
                            request={"model": self.model_name, "api_url": self.api_url},
                            response=error_data,
                            source="model_api",
                        )
                        raise Exception(f"API request failed: {response.status} - {error_data}")
            except asyncio.TimeoutError:
                last_error = Exception(f"Large model request timeout (attempt {attempt}/{max_retries})")
                logger.warning(last_error)
                if attempt < max_retries:
                    wait_time = 2 ** attempt
                    await asyncio.sleep(wait_time)
                    continue
            except (aiohttp.ClientConnectorError, aiohttp.ClientOSError, ssl.SSLError) as conn_err:
                # SSL 连接错误（如 "SSL record layer failure"）通常由 keep-alive
                # 连接失效或网络波动引起。重置 session 强制新建连接。
                last_error = conn_err
                logger.error(f"[generate] 连接错误 (attempt {attempt}/{max_retries}): {conn_err}")
                self._reset_session()
                if attempt < max_retries:
                    wait_time = 2 ** attempt
                    await asyncio.sleep(wait_time)
                    continue
                break
            except Exception as e:
                last_error = e
                logger.error(f"[generate] Attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    is_service_busy = "503" in str(e)
                    if is_service_busy:
                        wait_time = min(5 * (3 ** (attempt - 1)), 60)
                    else:
                        wait_time = 2 ** attempt
                    await asyncio.sleep(wait_time)
                    continue
                break

        raise last_error or Exception("Large model request failed after all retries")

    # ------------------------------------------------------------------
    # 原生工具调用 chat()
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict]] = None,
        **kwargs,
    ) -> ChatResponse:
        """带原生工具调用的对话生成 (DashScope / OpenAI 双格式)

        Args:
            messages: 消息列表
            tools: API 工具描述列表
            **kwargs: 额外参数

        Returns:
            ChatResponse
        """
        # ── 格式层统一由 Provider 构建（headers/请求体；anthropic system 提取、dashscope input 均内含）──
        api_messages = self._messages_to_api(messages)
        model = kwargs.get("model", self.model_name)
        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        temperature = kwargs.get("temperature", self.temperature)
        tool_choice = kwargs.get("tool_choice")

        headers = self._provider.build_headers()
        if model != self._provider.model_name:
            # 保留"调用方显式覆盖模型名"的功能
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

        for attempt in range(1, max_retries + 1):
            try:
                # RES-1: Reuse pooled session instead of creating new one per request
                session = await self._get_session()
                self._log_request("POST", self._chat_url, len(json.dumps(payload)))
                self._log_payload(payload)
                request_start = time.time()
                async with session.post(
                    self._chat_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        self._log_response_body(200, (time.time() - request_start) * 1000, json.dumps(data, ensure_ascii=False))
                        return self._parse_chat_response(data, tools=tools)
                    else:
                        error_data = await response.json()
                        report_api_error(
                            Exception(f"API request failed: {response.status} - {error_data}"),
                            module="infra.model.large_model_client",
                            function="generate",
                            status_code=response.status,
                            request={"model": self.model_name, "api_url": self.api_url},
                            response=error_data,
                            source="model_api",
                        )
                        raise Exception(
                            f"API request failed: {response.status} - {error_data}"
                        )
            except asyncio.TimeoutError:
                last_error = Exception(
                    f"Chat request timeout (attempt {attempt}/{max_retries})"
                )
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    # 503 服务繁忙：使用更长退避 (5s, 15s, 30s)
                    is_service_busy = "503" in str(e)
                    if is_service_busy:
                        await asyncio.sleep(min(5 * (3 ** (attempt - 1)), 60))
                    else:
                        await asyncio.sleep(2 ** attempt)
                    continue
                break

        final_error = last_error or Exception("Chat request failed after all retries")
        report_exception(
            final_error,
            module="infra.model.large_model_client",
            function="chat",
            context={"api_url": self.api_url, "model": self.model_name, "api_format": self._api_format},
            source="model_api",
        )
        raise final_error

    # ------------------------------------------------------------------
    # 流式对话生成
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict]] = None,
        on_token: Optional[Callable[[str], None]] = None,
        **kwargs,
    ) -> ChatResponse:
        """流式对话生成 — 每收到一个文本 token 调用 on_token 回调

        支持 OpenAI / Anthropic / DashScope 三种流式格式。
        工具调用通过 SSE delta 累积，最终返回完整 ChatResponse。
        """
        # ── 格式层统一由 Provider 构建（headers/请求体，stream 由 build_request 处理）──
        api_messages = self._messages_to_api(messages)
        model = kwargs.get("model", self.model_name)
        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        temperature = kwargs.get("temperature", self.temperature)
        tool_choice = kwargs.get("tool_choice")

        headers = self._provider.build_headers()
        if model != self._provider.model_name:
            self._provider.model_name = model
        payload = self._provider.build_request(
            api_messages,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            tool_choice=tool_choice,
            stream=True,
        )

        # ── 发起流式请求并解析 SSE ──
        max_retries = kwargs.get("max_retries", 2)
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                session = await self._get_session()
                self._log_request("POST", self._chat_url, len(json.dumps(payload)))
                self._log_payload(payload)
                async with session.post(
                    self._chat_url, headers=headers, json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.timeout, sock_read=30)
                ) as response:
                    if response.status != 200:
                        error_data = await response.text()
                        # 诊断：记录实际使用的 API key 和 URL
                        key_preview = self.api_key[:8] + "..." + self.api_key[-4:] if len(self.api_key) > 12 else "(empty)"
                        logger.error(f"[Auth诊断] status={response.status} url={self.api_url} key={key_preview}")
                        raise Exception(f"Stream API error {response.status}: {error_data[:200]}")

                    # 按 API 格式解析 SSE 流
                    if self._api_format == "anthropic":
                        result = await self._parse_anthropic_stream(response, on_token)
                    elif self._api_format == "dashscope":
                        result = await self._parse_dashscope_stream(response, on_token)
                    else:
                        result = await self._parse_openai_stream(response, on_token)
                    resp_text = ""
                    if result and hasattr(result, 'message'):
                        resp_text = getattr(result.message, 'content', '') or ""
                    self._log_response_body(200, 0, resp_text)
                    return result

            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    # 503 服务繁忙：使用更长退避 (5s, 15s, 30s)
                    is_service_busy = "503" in str(e)
                    if is_service_busy:
                        await asyncio.sleep(min(5 * (3 ** (attempt - 1)), 60))
                    else:
                        await asyncio.sleep(2 ** attempt)
                    continue
                break

        raise last_error or Exception("Stream chat failed after all retries")

    async def _parse_openai_stream(
        self, response: aiohttp.ClientResponse, on_token: Optional[Callable],
    ) -> ChatResponse:
        """解析 OpenAI SSE 流"""
        text_parts: List[str] = []
        reasoning_parts: List[str] = []  # deepseek reasoning_content（思考过程）
        # tool_calls 累积: index -> {id, name, arguments_parts}
        tc_accum: Dict[int, Dict] = {}
        finish_reason = "stop"

        async for raw_line in response.content:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                choices = chunk.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                fr = choices[0].get("finish_reason")
                if fr:
                    finish_reason = "tool_calls" if fr == "tool_calls" else ("length" if fr == "length" else "stop")

                # 文本 token
                content = delta.get("content")
                if content:
                    text_parts.append(content)
                    if on_token:
                        on_token(content)

                # 思考 token（deepseek reasoning_content）
                reasoning = delta.get("reasoning_content")
                if reasoning:
                    reasoning_parts.append(reasoning)

                # 工具调用 delta
                for tc_delta in delta.get("tool_calls", []):
                    idx = tc_delta.get("index", 0)
                    if idx not in tc_accum:
                        tc_accum[idx] = {"id": "", "name": "", "arguments_parts": []}
                    if tc_delta.get("id"):
                        tc_accum[idx]["id"] = tc_delta["id"]
                    func = tc_delta.get("function", {})
                    if func.get("name"):
                        tc_accum[idx]["name"] = func["name"]
                    if func.get("arguments"):
                        tc_accum[idx]["arguments_parts"].append(func["arguments"])

        # 构建最终 ChatResponse
        tool_calls = None
        if tc_accum:
            tool_calls = []
            for idx in sorted(tc_accum.keys()):
                tc = tc_accum[idx]
                tool_calls.append(ToolCall(
                    id=tc["id"],
                    name=tc["name"],
                    arguments="".join(tc["arguments_parts"]),
                ))

        full_text = "".join(text_parts) if text_parts else None
        reasoning_full = "".join(reasoning_parts) if reasoning_parts else None
        return ChatResponse(
            message=ChatMessage(
                role="assistant",
                content=full_text,
                tool_calls=tool_calls,
                reasoning_content=reasoning_full,
            ),
            finish_reason=finish_reason,
        )

    async def _parse_anthropic_stream(
        self, response: aiohttp.ClientResponse, on_token: Optional[Callable],
    ) -> ChatResponse:
        """解析 Anthropic SSE 流"""
        text_parts: List[str] = []
        tool_calls_list: List[ToolCall] = []
        current_tool: Optional[Dict] = None
        finish_reason = "stop"

        async for raw_line in response.content:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_type = line[6:].strip()
                continue
            if line.startswith("data:"):
                data_str = line[5:].strip()
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                event_type = data.get("type", "")

                if event_type == "content_block_start":
                    block = data.get("content_block", {})
                    if block.get("type") == "tool_use":
                        current_tool = {"id": block.get("id", ""), "name": block.get("name", ""), "input_parts": []}
                elif event_type == "content_block_delta":
                    delta = data.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            text_parts.append(text)
                            if on_token:
                                on_token(text)
                    elif delta.get("type") == "input_json_delta":
                        if current_tool is not None:
                            current_tool["input_parts"].append(delta.get("partial_json", ""))
                elif event_type == "content_block_stop":
                    if current_tool is not None:
                        full_input = "".join(current_tool["input_parts"])
                        try:
                            parsed = json.loads(full_input) if full_input else {}
                        except json.JSONDecodeError:
                            parsed = {}
                        tool_calls_list.append(ToolCall(
                            id=current_tool["id"],
                            name=current_tool["name"],
                            arguments=json.dumps(parsed, ensure_ascii=False),
                        ))
                        current_tool = None
                elif event_type == "message_delta":
                    stop = data.get("delta", {}).get("stop_reason", "")
                    if stop == "tool_use":
                        finish_reason = "tool_calls"
                    elif stop == "max_tokens":
                        finish_reason = "length"

        full_text = "".join(text_parts) if text_parts else None
        return ChatResponse(
            message=ChatMessage(
                role="assistant", content=full_text,
                tool_calls=tool_calls_list if tool_calls_list else None,
            ),
            finish_reason=finish_reason,
        )

    async def _parse_dashscope_stream(
        self, response: aiohttp.ClientResponse, on_token: Optional[Callable],
    ) -> ChatResponse:
        """解析 DashScope SSE 流"""
        text_parts: List[str] = []

        async for raw_line in response.content:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_str = line[5:].strip()
                if not data_str:
                    continue
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                output = data.get("output", {})
                text = output.get("text", "")
                if text:
                    # DashScope 每次发送累积文本，取增量
                    if len(text) > len("".join(text_parts)):
                        delta = text[len("".join(text_parts)):]
                        text_parts.append(delta)
                        if on_token and delta:
                            on_token(delta)

                # 检查是否有工具调用
                choices = output.get("choices", [])
                if choices:
                    msg = choices[0].get("message", {})
                    if msg.get("tool_calls"):
                        # DashScope 非流式格式的工具调用（罕见于流式模式）
                        full_text = "".join(text_parts) if text_parts else None
                        tc_list = []
                        for tc in msg["tool_calls"]:
                            func = tc.get("function", {})
                            tc_list.append(ToolCall(
                                id=tc.get("id", ""),
                                name=func.get("name", ""),
                                arguments=func.get("arguments", "{}"),
                            ))
                        return ChatResponse(
                            message=ChatMessage(role="assistant", content=full_text, tool_calls=tc_list),
                            finish_reason="tool_calls",
                        )

        full_text = "".join(text_parts) if text_parts else None
        return ChatResponse(
            message=ChatMessage(role="assistant", content=full_text),
            finish_reason="stop",
        )

    # ------------------------------------------------------------------
    # 消息格式转换
    # ------------------------------------------------------------------

    def _messages_to_api(self, messages: List[ChatMessage]) -> List[Dict]:
        """将内部 ChatMessage 列表转为目标 API 消息格式"""
        fmt = self._api_format
        result = []

        # ── 当前回合用户图片（直连多模态）：取一次并清除，避免 ReAct 循环重复附图 ──
        turn_images = None
        try:
            factory = get_capability("turn_images")
            if factory is not None:
                get_turn_images, clear_turn_images = factory()
                turn_images = get_turn_images()
                if turn_images:
                    clear_turn_images()
        except Exception:
            turn_images = None

        for m in messages:
            # Anthropic: tool 结果用 user + tool_result block
            if m.role == "tool" and fmt == "anthropic":
                result.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id or "",
                        "content": m.content or "",
                    }],
                })
                continue

            msg: Dict[str, Any] = {"role": m.role}
            if m.content is not None:
                msg["content"] = m.content

            # thinking模式：传回reasoning_content
            if m.reasoning_content and fmt == "openai":
                msg["reasoning_content"] = m.reasoning_content

            # Anthropic: assistant 的 tool_calls 用 tool_use content blocks
            if m.tool_calls and fmt == "anthropic":
                blocks: List[Dict[str, Any]] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    try:
                        inp = json.loads(tc.arguments)
                    except Exception as e:
                        logger.warning(f"大模型工具调用参数解析失败: {e}")
                        inp = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": inp,
                    })
                msg = {"role": "assistant", "content": blocks}
            elif m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "type": tc.type or "function",
                        "function": {
                            "name": tc.name,
                            "arguments": tc.arguments,
                        },
                        "id": tc.id,
                    }
                    for tc in m.tool_calls
                ]

            # tool role 消息：非 Anthropic 格式处理
            if m.role == "tool" and fmt != "anthropic":
                if fmt == "openai":
                    if m.tool_call_id:
                        msg["tool_call_id"] = m.tool_call_id
                else:
                    if m.name:
                        msg["name"] = m.name
            result.append(msg)

        # ── 把当前回合图片挂到最后一个"纯文本 user 消息"上 ──
        # 跳过 content 为 list 的（Anthropic tool_result）消息，避免把图附到工具结果上。
        if turn_images:
            for msg in reversed(result):
                if msg.get("role") != "user":
                    continue
                content = msg.get("content")
                if content is not None and not isinstance(content, str):
                    continue
                if fmt == "anthropic":
                    blocks = []
                    if content:
                        blocks.append({"type": "text", "text": content})
                    for img in turn_images:
                        media, b64 = self._parse_image_dataurl(img)
                        blocks.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": media, "data": b64},
                        })
                    msg["content"] = blocks
                else:
                    # OpenAI / DashScope 兼容格式：image_url content blocks
                    blocks = []
                    if content:
                        blocks.append({"type": "text", "text": content})
                    for img in turn_images:
                        blocks.append({"type": "image_url", "image_url": {"url": img}})
                    msg["content"] = blocks
                break

        return result

    @staticmethod
    def _parse_image_dataurl(dataurl: str) -> tuple:
        """把 dataURL 拆成 (media_type, base64)；非 dataURL 视为裸 base64。"""
        if isinstance(dataurl, str) and dataurl.startswith("data:"):
            try:
                head, b64 = dataurl.split(",", 1)
                media_type = head[5:].split(";", 1)[0] or "image/jpeg"
                return media_type, b64
            except Exception:
                return "image/jpeg", dataurl
        return "image/jpeg", dataurl

    def _parse_chat_response(self, data: Dict, tools: Optional[List[Dict]] = None) -> ChatResponse:
        """解析 API 响应为 ChatResponse。

        openai/anthropic 由 Provider.parse_response 统一解析（含 reasoning_content/usage）；
        dashscope 保留客户端实现（含 legacy output.text 文本工具调用解析，Provider 不具备）。
        """
        fmt = self._api_format

        # ── OpenAI / Anthropic 格式：由 Provider 统一解析 ──
        if fmt in ("openai", "anthropic"):
            parsed = self._provider.parse_response(data)
            tool_calls_raw = parsed.get("tool_calls")
            tool_calls = None
            if tool_calls_raw:
                tool_calls = [
                    ToolCall(
                        id=tc.get("id", f"call_{i}"),
                        name=tc.get("name", ""),
                        arguments=tc.get("arguments", "{}"),
                    )
                    for i, tc in enumerate(tool_calls_raw)
                ]
            return ChatResponse(
                message=ChatMessage(
                    role="assistant",
                    content=parsed.get("content"),
                    tool_calls=tool_calls,
                    reasoning_content=parsed.get("reasoning_content"),  # thinking模式
                ),
                finish_reason=parsed.get("finish_reason", "stop"),
                usage=parsed.get("usage"),
            )

        # ── DashScope 格式（保留客户端实现：legacy 文本工具调用解析）──
        output = data.get("output", {})

        # 优先 choices 格式 (工具调用时必用)
        choices = output.get("choices", [])
        if choices:
            choice = choices[0]
            msg = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "stop")
            content = msg.get("content")
            tool_calls_raw = msg.get("tool_calls", [])
            usage = data.get("usage")

            tool_calls = None
            if tool_calls_raw:
                tool_calls = [
                    ToolCall(
                        id=tc.get("id", f"call_{i}"),
                        name=tc.get("function", {}).get("name", ""),
                        arguments=tc.get("function", {}).get("arguments", "{}"),
                    )
                    for i, tc in enumerate(tool_calls_raw)
                ]

            return ChatResponse(
                message=ChatMessage(
                    role=msg.get("role", "assistant"),
                    content=content,
                    tool_calls=tool_calls,
                ),
                finish_reason=finish_reason,
                usage=usage,
            )

        # 回退 output.text (legacy)
        # 但如果文本是工具调用格式，尝试解析它
        text_content = output.get("text", "")
        tool_calls = None

        if text_content and tools:
            import json as json_module
            import re

            # 尝试解析 JSON 或函数调用格式的工具调用
            try:
                # 模式 1: JSON 对象格式
                if text_content.strip().startswith('{'):
                    tool_json = json_module.loads(text_content)
                    if "action" in tool_json or "role" in tool_json:
                        action_name = tool_json.get("action") or tool_json.get("role")
                        tool_names = [t.get("function", {}).get("name", "") for t in (tools or [])]
                        if action_name in tool_names:
                            tool_calls = [
                                ToolCall(
                                    id=f"call_{action_name}",
                                    name=action_name,
                                    arguments=json_module.dumps(tool_json, ensure_ascii=False),
                                )
                            ]
                            text_content = ""

                # 模式 2: 函数调用格式 function_name(param="value", param2=value)
                elif "(" in text_content and ")" in text_content:
                    match = re.match(r'(\w+)\s*\((.*)\)', text_content.strip())
                    if match:
                        func_name = match.group(1)
                        args_str = match.group(2)

                        # 查找工具定义中是否有匹配的工具名
                        tool_names = [t.get("function", {}).get("name", "") for t in (tools or [])]
                        if func_name in tool_names:
                            # 简单的参数解析：key="value" 格式
                            args_dict = {}
                            param_pattern = r'(\w+)\s*=\s*(?:"([^"]*)"|(\w+)|({[^}]*}))'
                            for param_match in re.finditer(param_pattern, args_str):
                                key = param_match.group(1)
                                val = param_match.group(2) or param_match.group(3) or param_match.group(4)
                                args_dict[key] = val

                            tool_calls = [
                                ToolCall(
                                    id=f"call_{func_name}",
                                    name=func_name,
                                    arguments=json_module.dumps(args_dict, ensure_ascii=False),
                                )
                            ]
                            text_content = ""
            except Exception as e:
                logger.debug(f"工具调用文本解析失败，回退纯文本: {e}")

        return ChatResponse(
            message=ChatMessage(
                role="assistant",
                content=text_content if text_content else None,
                tool_calls=tool_calls,
            ),
            finish_reason="stop",
        )

    async def generate_stream(self, prompt: str, **kwargs) -> AsyncGenerator[str, None]:
        """流式生成 - 支持 Qwen API"""
        import json
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-SSE": "enable"  # Qwen 流式输出需要
        }
        
        # Qwen API 流式请求格式
        payload = {
            "model": self.model_name,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            },
            "parameters": {
                "max_tokens": kwargs.get("max_tokens", self.max_tokens),
                "temperature": kwargs.get("temperature", self.temperature)
            },
            "stream": True
        }
        
        try:
            # RES-1: Reuse pooled session instead of creating new one per request
            session = await self._get_session()
            self._log_request("POST", self._chat_url, len(json.dumps(payload)))
            self._log_payload(payload)
            time.time()
            async with session.post(
                self._chat_url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            ) as response:
                async for line in response.content:
                    if line:
                        line_str = line.decode('utf-8').strip()
                        # 解析 SSE 格式
                        if line_str.startswith('data:'):
                            data_str = line_str[5:].strip()
                            if data_str and data_str != '[DONE]':
                                try:
                                    data = json.loads(data_str)
                                    text = data.get('output', {}).get('text', '')
                                    if text:
                                        yield text
                                except json.JSONDecodeError:
                                    continue
        except asyncio.TimeoutError:
            raise Exception("Large model stream timeout")
    
    async def health_check(self) -> bool:
        """健康检查 - 简单检查 API 连通性"""
        try:
            # 使用一个简单的请求测试 API（system_prompt 必填，健康检查显式传参）
            test_response = await self.generate(
                "你好",
                system_prompt="你是健康检查助手，收到后请回复任意单词确认模型可用。",
                max_tokens=5,
            )
            return len(test_response) > 0
        except Exception as e:
            logger.warning(f"大模型健康检查失败: {e}")
            return False