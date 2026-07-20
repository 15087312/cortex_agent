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
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if stream:
            payload["stream"] = True
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
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
