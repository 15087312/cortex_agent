"""
OpenAI 兼容 API 适配器 (DeepSeek / Groq / OpenRouter 等)
"""
from typing import Dict, Any, List, Optional
import json
from config.providers.base import ProviderBase


class OpenAIProvider(ProviderBase):
    """OpenAI 兼容 API

    适用: DeepSeek, OpenRouter, Groq, 百川, Mistral 等
    """

    def build_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def build_request(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int,
        temperature: float,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[Any] = None,
        stream: bool = False,
        top_p: Optional[float] = None,
        reasoning_effort: str = "",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if top_p is not None:
            payload["top_p"] = top_p
        if stream:
            payload["stream"] = True
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
        if reasoning_effort and "deepseek" in self.model_name.lower():
            payload["reasoning_effort"] = reasoning_effort
        # DeepSeek thinking 模式：模型名含 deepseek/DeepSeek 时显式启用推理。
        # DeepSeek API 自 V4(0731) 起，thinking 需为 ThinkingOptions 结构体
        #   {"type": "enabled"/"disabled"}
        # 而非旧版裸布尔值 boolean true（否则 400: expected struct ThinkingOptions）。
        if "deepseek" in self.model_name.lower():
            payload["thinking"] = {"type": "enabled"}
        return payload

    def parse_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        choices = data.get("choices", [])
        if not choices:
            return {"content": "", "tool_calls": None, "finish_reason": "stop", "usage": None}

        choice = choices[0]
        message = choice.get("message", {})
        content = message.get("content", "")
        finish_reason = choice.get("finish_reason", "stop")
        tool_calls_raw = message.get("tool_calls")
        # thinking 模式推理内容（客户端展示思考区需要，与 infra/model 行为一致）
        reasoning_content = message.get("reasoning_content")

        tool_calls = None
        if tool_calls_raw:
            tool_calls = [
                {
                    "id": tc.get("id", ""),
                    "name": tc.get("function", {}).get("name", ""),
                    "arguments": tc.get("function", {}).get("arguments", "{}"),
                }
                for tc in tool_calls_raw
            ]

        usage = data.get("usage")

        return {
            "content": content or None,
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
            "usage": usage,
            "reasoning_content": reasoning_content,
        }

    def chat_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if "/v1" in base:
            return f"{base.rstrip('/')}/chat/completions"
        return f"{base}/v1/chat/completions"

    def parse_stream_line(self, line: str) -> Optional[Dict[str, Any]]:
        if not line.startswith("data:"):
            return None
        data_str = line[5:].strip()
        if data_str == "[DONE]":
            return None
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            return None
        choices = chunk.get("choices", [])
        if not choices:
            return None
        delta = choices[0].get("delta", {})
        result: Dict[str, Any] = {}
        if delta.get("content"):
            result["content"] = delta["content"]
        if delta.get("tool_calls"):
            result["tool_calls"] = delta["tool_calls"]
        fr = choices[0].get("finish_reason")
        if fr:
            result["finish_reason"] = fr
        return result if result else None
