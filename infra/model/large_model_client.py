"""
大模型调用客户端 - 封装 API、异步优先
"""
from .base_model import BaseModelClient, ChatMessage, ChatResponse, ToolCall
from typing import AsyncGenerator, Callable, Dict, Any, List, Optional
import aiohttp
import asyncio
import json
import re
import logging
from config.model_config import LargeModelConfig, get_large_model_config
from config.settings import settings
from infra.prompts import prompt_manager
from modules.management import report_api_error, report_exception

# Q-12: Module-level logger to avoid repeated creation
logger = logging.getLogger(__name__)


class LargeModelClient(BaseModelClient):
    """大模型调用客户端 — 支持 DashScope / OpenAI 两种 API 格式"""

    def __init__(
        self,
        config: LargeModelConfig = None,
        api_key: str = None,
        api_url: str = None,
        timeout: int = 120,
        api_format: str = "",
    ):
        if config:
            super().__init__(config.api_key, config.api_url, config.timeout)
            self.max_tokens = config.max_tokens
            self.temperature = config.temperature
            self.model_name = config.model_name
            self._api_format = config.api_format or ""
        else:
            super().__init__(api_key or "", api_url or "", timeout)
            self.max_tokens = 4096
            self.temperature = 0.7
            self.model_name = settings.LARGE_MODEL_NAME
            self._api_format = api_format

        # 自动检测 API 格式
        if not self._api_format:
            self._api_format = self._detect_api_format(self.api_url)
        self.supports_native_tools = True
        logger.info(
            f"[LargeModelClient] API 格式: {self._api_format}, URL: {self.api_url[:60]}..."
        )

    @classmethod
    def from_config(cls) -> 'LargeModelClient':
        """从配置文件创建实例"""
        config = get_large_model_config()
        return cls(config=config)

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
    
    async def generate(self, prompt: str, max_retries: int = 2, **kwargs) -> str:
        """生成响应 - 支持 DashScope / OpenAI / Anthropic，带重试机制"""
        # 从提示词管理器获取系统提示词
        from infra.prompts.registry import prompt_registry
        system_prompt = prompt_registry.get("large_model") or ""

        if self._api_format == "anthropic":
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            payload = {
                "model": self.model_name,
                "max_tokens": kwargs.get("max_tokens", self.max_tokens),
                "system": system_prompt,
                "messages": [{"role": "user", "content": prompt}],
            }
        else:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": self.model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": kwargs.get("max_tokens", self.max_tokens),
                "temperature": kwargs.get("temperature", self.temperature),
            }

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                # RES-1: Reuse pooled session instead of creating new one per request
                session = await self._get_session()
                async with session.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout
                ) as response:
                    content_type = response.headers.get("Content-Type", "")
                    logger.debug(f"[generate] Status: {response.status}, Content-Type: {content_type}")

                    if response.status == 200:
                        # Try to parse JSON regardless of Content-Type
                        try:
                            data = await response.json()
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

                                # 处理 Reasoner 模型响应：如果 content 为空但有 reasoning_content，使用推理内容
                                if not content and "reasoning_content" in message:
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
                                    raise Exception(f"Got HTML response instead of JSON")
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
                    wait_time = 2 ** attempt  # 指数退避：2s, 4s, 8s
                    await asyncio.sleep(wait_time)
                    continue
            except Exception as e:
                last_error = e
                logger.error(f"[generate] Attempt {attempt} failed: {e}")
                if attempt < max_retries:
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
        # ── 按 API 格式构建 headers ──
        if self._api_format == "anthropic":
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        else:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

        api_messages = self._messages_to_api(messages)
        model = kwargs.get("model", self.model_name)
        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        temperature = kwargs.get("temperature", self.temperature)

        # ── 按 API 格式构建请求 ──
        tool_choice = kwargs.get("tool_choice")
        if self._api_format == "anthropic":
            # Anthropic: system 是顶层参数，不在 messages 里
            system_text = ""
            user_messages = []
            for m in api_messages:
                if m.get("role") == "system":
                    system_text = m.get("content", "")
                else:
                    user_messages.append(m)
            payload: Dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": user_messages,
            }
            if system_text:
                payload["system"] = system_text
            if tools:
                # Anthropic 工具格式: name + description + input_schema
                anthropic_tools = []
                for t in tools:
                    func = t.get("function", t)
                    anthropic_tools.append({
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "input_schema": func.get("parameters", func.get("input_schema", {})),
                    })
                payload["tools"] = anthropic_tools
            if tool_choice:
                if isinstance(tool_choice, dict) and "function" in tool_choice:
                    payload["tool_choice"] = {"type": "tool", "name": tool_choice["function"].get("name", "")}
                elif tool_choice == "required":
                    payload["tool_choice"] = {"type": "any"}
                elif tool_choice == "auto":
                    payload["tool_choice"] = {"type": "auto"}
        elif self._api_format == "openai":
            payload: Dict[str, Any] = {
                "model": model,
                "messages": api_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if tools:
                payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
        else:  # dashscope
            payload = {
                "model": model,
                "input": {"messages": api_messages},
                "parameters": {
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            }
            if tools:
                payload["tools"] = tools
                # Try DashScope format: tool_choice as string "required" or object
                if tool_choice:
                    if isinstance(tool_choice, dict) and "function" in tool_choice:
                        # Convert OpenAI format to DashScope format if needed
                        func_name = tool_choice["function"].get("name")
                        if func_name:
                            payload["parameters"]["tool_choice"] = {"type": "function", "function": {"name": func_name}}
                    else:
                        payload["parameters"]["tool_choice"] = tool_choice

        max_retries = kwargs.get("max_retries", 2)
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                # RES-1: Reuse pooled session instead of creating new one per request
                session = await self._get_session()
                async with session.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                ) as response:
                    if response.status == 200:
                        data = await response.json()
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
        # ── 构建 headers ──
        if self._api_format == "anthropic":
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        else:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

        api_messages = self._messages_to_api(messages)
        model = kwargs.get("model", self.model_name)
        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        temperature = kwargs.get("temperature", self.temperature)
        tool_choice = kwargs.get("tool_choice")

        # ── 构建 payload（与 chat() 相同，加 stream: True）──
        if self._api_format == "anthropic":
            system_text = ""
            user_messages = []
            for m in api_messages:
                if m.get("role") == "system":
                    system_text = m.get("content", "")
                else:
                    user_messages.append(m)
            payload: Dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": user_messages,
                "stream": True,
            }
            if system_text:
                payload["system"] = system_text
            if tools:
                anthropic_tools = []
                for t in tools:
                    func = t.get("function", t)
                    anthropic_tools.append({
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "input_schema": func.get("parameters", func.get("input_schema", {})),
                    })
                payload["tools"] = anthropic_tools
            if tool_choice:
                if isinstance(tool_choice, dict) and "function" in tool_choice:
                    payload["tool_choice"] = {"type": "tool", "name": tool_choice["function"].get("name", "")}
                elif tool_choice == "required":
                    payload["tool_choice"] = {"type": "any"}
                elif tool_choice == "auto":
                    payload["tool_choice"] = {"type": "auto"}
        elif self._api_format == "openai":
            payload = {
                "model": model,
                "messages": api_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": True,
            }
            if tools:
                payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
        else:  # dashscope
            payload = {
                "model": model,
                "input": {"messages": api_messages},
                "parameters": {"max_tokens": max_tokens, "temperature": temperature},
                "stream": True,
            }
            if tools:
                payload["tools"] = tools
                if tool_choice:
                    if isinstance(tool_choice, dict) and "function" in tool_choice:
                        func_name = tool_choice["function"].get("name")
                        if func_name:
                            payload["parameters"]["tool_choice"] = {"type": "function", "function": {"name": func_name}}
                    else:
                        payload["parameters"]["tool_choice"] = tool_choice

        # ── 发起流式请求并解析 SSE ──
        max_retries = kwargs.get("max_retries", 2)
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                session = await self._get_session()
                async with session.post(
                    self.api_url, headers=headers, json=payload,
                ) as response:
                    if response.status != 200:
                        error_data = await response.text()
                        # 诊断：记录实际使用的 API key 和 URL
                        key_preview = self.api_key[:8] + "..." + self.api_key[-4:] if len(self.api_key) > 12 else "(empty)"
                        logger.error(f"[Auth诊断] status={response.status} url={self.api_url} key={key_preview}")
                        raise Exception(f"Stream API error {response.status}: {error_data[:200]}")

                    # 按 API 格式解析 SSE 流
                    if self._api_format == "anthropic":
                        return await self._parse_anthropic_stream(response, on_token)
                    elif self._api_format == "dashscope":
                        return await self._parse_dashscope_stream(response, on_token)
                    else:
                        return await self._parse_openai_stream(response, on_token)

            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                break

        raise last_error or Exception("Stream chat failed after all retries")

    async def _parse_openai_stream(
        self, response: aiohttp.ClientResponse, on_token: Optional[Callable],
    ) -> ChatResponse:
        """解析 OpenAI SSE 流"""
        text_parts: List[str] = []
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
        return ChatResponse(
            message=ChatMessage(role="assistant", content=full_text, tool_calls=tool_calls),
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

            # Anthropic: assistant 的 tool_calls 用 tool_use content blocks
            if m.tool_calls and fmt == "anthropic":
                blocks = []
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
        return result

    def _parse_chat_response(self, data: Dict, tools: Optional[List[Dict]] = None) -> ChatResponse:
        """解析 API 响应为 ChatResponse (DashScope / OpenAI / Anthropic 兼容)"""
        fmt = self._api_format

        # ── Anthropic 格式: data.content[] ──
        if fmt == "anthropic":
            content_blocks = data.get("content", [])
            stop_reason = data.get("stop_reason", "end_turn")
            usage = data.get("usage")

            text_parts = []
            tool_calls = None
            for block in content_blocks:
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    if tool_calls is None:
                        tool_calls = []
                    tool_calls.append(ToolCall(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        arguments=json.dumps(block.get("input", {}), ensure_ascii=False),
                    ))

            finish_reason = "stop"
            if stop_reason == "tool_use":
                finish_reason = "tool_calls"
            elif stop_reason == "max_tokens":
                finish_reason = "length"

            return ChatResponse(
                message=ChatMessage(
                    role="assistant",
                    content="\n".join(text_parts) if text_parts else None,
                    tool_calls=tool_calls,
                ),
                finish_reason=finish_reason,
                usage={
                    "prompt_tokens": usage.get("input_tokens", 0) if usage else 0,
                    "completion_tokens": usage.get("output_tokens", 0) if usage else 0,
                } if usage else None,
            )

        # ── OpenAI 格式: data.choices[].message ──
        if fmt == "openai":
            choices = data.get("choices", [])
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

            return ChatResponse(message=ChatMessage(role="assistant", content=""))

        # ── DashScope 格式 ──
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
            async with session.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=self.timeout
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
            # 使用一个简单的请求测试 API
            test_response = await self.generate("你好", max_tokens=5)
            return len(test_response) > 0
        except Exception as e:
            logger.warning(f"大模型健康检查失败: {e}")
            return False