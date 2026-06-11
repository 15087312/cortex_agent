"""
模型调用基类 - 统一接口定义
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, AsyncGenerator, List, Callable
import asyncio
import aiohttp
import json
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据类 — 原生工具调用
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    """模型返回的工具调用指令"""
    id: str = ""
    name: str = ""
    arguments: str = "{}"
    type: str = "function"


@dataclass
class ChatMessage:
    """聊天消息 (含工具调用支持)"""
    role: str  # system / user / assistant / tool
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    name: Optional[str] = None        # DashScope: tool role 时标识函数名
    tool_call_id: Optional[str] = None  # OpenAI: tool role 时关联 tool_call
    reasoning_content: Optional[str] = None  # thinking模式下的推理内容


@dataclass
class ChatResponse:
    """聊天响应 — 可能包含文本和/或工具调用"""
    message: ChatMessage
    finish_reason: str = "stop"
    usage: Optional[Dict[str, int]] = None


class BaseModelClient(ABC):
    """模型调用基类"""
    
    def __init__(self, api_key: str, api_url: str, timeout: int = 30, allow_empty: bool = False):
        if not allow_empty and not api_key:
            raise ValueError("API key 不能为空")
        if not allow_empty and not api_url:
            raise ValueError("API URL 不能为空")
        
        self.api_key = api_key
        self.api_url = api_url
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None
        self._request_count = 0
        self._last_request_time: Optional[datetime] = None
        self._total_tokens_used = 0
        self.supports_native_tools: bool = False
    
    @abstractmethod
    async def generate(self, prompt: str, **kwargs) -> str:
        """生成响应（纯文本，无工具支持）

        Args:
            prompt: 输入提示词
            **kwargs: 额外参数（如 max_tokens, temperature 等）

        Returns:
            生成的响应文本

        Raises:
            Exception: API 请求失败或超时时抛出异常
        """
        pass

    async def chat(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict]] = None,
        **kwargs,
    ) -> ChatResponse:
        """带原生工具调用的对话生成（可选覆盖）

        默认实现回退到 generate()，仅保留文本响应。
        子类（如 LargeModelClient）应覆盖此方法以支持原生工具调用。

        Args:
            messages: 消息列表（system / user / assistant / tool）
            tools: API 工具描述列表（JSON Schema 格式）
            **kwargs: 额外参数

        Returns:
            ChatResponse 包含文本和/或 tool_calls
        """
        # 默认回退：仅取最后一条 user 消息调用 generate()
        last_content = next(
            (m.content for m in reversed(messages) if m.content),
            "",
        )
        text = await self.generate(last_content, **kwargs)
        return ChatResponse(
            message=ChatMessage(role="assistant", content=text),
        )

    async def chat_stream(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict]] = None,
        on_token: Optional[Callable[[str], None]] = None,
        **kwargs,
    ) -> ChatResponse:
        """流式对话生成 — 每收到一个 token 调用 on_token 回调

        默认实现回退到非流式 chat()，收到完整结果后一次性调用 on_token。
        子类应覆盖此方法以支持真正的 token 级流式输出。

        Args:
            messages: 消息列表
            tools: API 工具描述列表
            on_token: 每个文本 token 到达时的回调 (chunk: str) -> None
            **kwargs: 额外参数

        Returns:
            ChatResponse — 完整响应（含 tool_calls）
        """
        response = await self.chat(messages, tools=tools, **kwargs)
        if on_token and response.message.content:
            on_token(response.message.content)
        return response

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查
        
        Returns:
            服务是否可用
        """
        pass
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 HTTP session"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session
    
    async def close(self):
        """关闭连接"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # API 格式检测 + Anthropic 共享辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def detect_api_format(url: str) -> str:
        """从 URL 自动检测 API 格式: dashscope / openai / anthropic"""
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
        """按 API 格式构建请求头"""
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
        """将 ChatMessage 列表转为 Anthropic 格式。

        Returns:
            (system_text, anthropic_messages) 元组
        """
        system_text = ""
        result = []
        for m in messages:
            if m.role == "system":
                system_text = m.content or ""
                continue
            if m.role == "tool":
                result.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id or "",
                        "content": m.content or "",
                    }],
                })
                continue
            if m.tool_calls:
                blocks = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    try:
                        inp = json.loads(tc.arguments)
                    except Exception as e:
                        logger.warning(f"工具调用参数解析失败: {e}")
                        inp = {}
                    blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": inp})
                result.append({"role": "assistant", "content": blocks})
            else:
                result.append({"role": m.role, "content": m.content or ""})
        return system_text, result

    @staticmethod
    def _parse_anthropic_response(data: Dict) -> ChatResponse:
        """解析 Anthropic API 响应为 ChatResponse"""
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

    def _tools_to_anthropic(self, tools: List[Dict]) -> List[Dict]:
        """将 OpenAI 格式工具列表转为 Anthropic 格式"""
        result = []
        for t in tools:
            func = t.get("function", t)
            result.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", func.get("input_schema", {})),
            })
        return result
    
    def _update_usage_stats(self, tokens_used: int = 0):
        """更新使用统计信息
        
        Args:
            tokens_used: 本次消耗的 token 数
        """
        self._request_count += 1
        self._last_request_time = datetime.now()
        self._total_tokens_used += tokens_used
    
    def get_usage_stats(self) -> Dict[str, Any]:
        """获取使用统计信息
        
        Returns:
            包含请求次数、最后请求时间、token 使用量的字典
        """
        return {
            "request_count": self._request_count,
            "last_request_time": self._last_request_time.isoformat() if self._last_request_time else None,
            "total_tokens_used": self._total_tokens_used
        }
    
    def reset_usage_stats(self):
        """重置使用统计信息"""
        self._request_count = 0
        self._last_request_time = None
        self._total_tokens_used = 0
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
