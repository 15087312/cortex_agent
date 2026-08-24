"""
Cohere 原生 API 适配器（非 OpenAI 兼容）

端点: POST {base_url}/chat，Bearer 认证。
消息角色: SYSTEM / USER / CHATBOT / TOOL；工具用 tools[]。
"""
import json
from typing import Any, Dict, List, Optional

from config.providers.base import ProviderBase

_ROLE_MAP = {
    "system": "SYSTEM",
    "user": "USER",
    "assistant": "CHATBOT",
    "tool": "TOOL",
}


class CohereProvider(ProviderBase):
    """Cohere Command API（v2 chat）"""

    format_name = "cohere"

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
            "messages": [
                {"role": _ROLE_MAP.get(m.get("role", "user"), "USER"),
                 "content": m.get("content") or ""}
                for m in messages
            ],
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if top_p is not None:
            payload["p"] = top_p
        if stream:
            payload["stream"] = True
        if tools:
            payload["tools"] = self._tools_to_cohere(tools)
        return payload

    @staticmethod
    def _tools_to_cohere(tools: List[Dict]) -> List[Dict]:
        result = []
        for t in tools:
            func = t.get("function", t)
            result.append({
                "type": "function",
                "function": {
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "parameters": func.get("parameters", func.get("input_schema", {})),
                },
            })
        return result

    def chat_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat"):
            return base
        return f"{base}/chat"

    def parse_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        message = data.get("message", {})
        content_parts = message.get("content", [])
        text_parts = []
        tool_calls = None
        for block in content_parts:
            if isinstance(block, str):
                text_parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "tool_calls":
                    for tc in block.get("tool_calls", []):
                        if tool_calls is None:
                            tool_calls = []
                        tool_calls.append({
                            "id": tc.get("id", ""),
                            "name": tc.get("function", {}).get("name", ""),
                            "arguments": json.dumps(
                                tc.get("function", {}).get("arguments", {}), ensure_ascii=False),
                        })
        usage = data.get("usage")
        return {
            "content": "\n".join(text_parts) if text_parts else None,
            "tool_calls": tool_calls,
            "finish_reason": self._map_finish(data.get("finish_reason", "")),
            "usage": {
                "prompt_tokens": (usage or {}).get("tokens", {}).get("inputTokens", 0),
                "completion_tokens": (usage or {}).get("tokens", {}).get("outputTokens", 0),
            } if usage else None,
        }

    @staticmethod
    def _map_finish(finish: str) -> str:
        f = (finish or "").upper()
        if "TOOL" in f:
            return "tool_calls"
        if "MAX" in f:
            return "length"
        return "stop"

    def parse_stream_line(self, line: str) -> Optional[Dict[str, Any]]:
        if not line.startswith("data:"):
            return None
        data_str = line[5:].strip()
        if not data_str or data_str == "[DONE]":
            return None
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            return None
        result: Dict[str, Any] = {}
        text = data.get("text")
        if text:
            result["content"] = text
        if data.get("is_finished"):
            result["finish_reason"] = "stop"
        return result if result else None