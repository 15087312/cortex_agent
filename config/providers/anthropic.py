"""
Anthropic API 适配器
"""
from typing import Dict, Any, List, Optional
import json
from config.providers.base import ProviderBase


class AnthropicProvider(ProviderBase):
    """Anthropic Messages API"""

    format_name = "anthropic"

    def build_headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def build_request(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int,
        temperature: float,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[Any] = None,
        stream: bool = False,
        top_p: Optional[float] = None,  # Anthropic 无 top_p，接受但忽略
        reasoning_effort: str = "",  # DeepSeek 专用，Anthropic 忽略
    ) -> Dict[str, Any]:
        system_text = ""
        user_messages = []
        for m in messages:
            if m.get("role") == "system":
                system_text = m.get("content", "")
            else:
                user_messages.append(m)

        payload: Dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": max_tokens,
            "messages": user_messages,
        }
        if system_text:
            payload["system"] = system_text
        if stream:
            payload["stream"] = True
        if tools:
            payload["tools"] = self._tools_to_anthropic(tools)
        if tool_choice:
            payload["tool_choice"] = self._tool_choice_to_anthropic(tool_choice)
        return payload

    def _tools_to_anthropic(self, tools: List[Dict]) -> List[Dict]:
        result = []
        for t in tools:
            func = t.get("function", t)
            result.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", func.get("input_schema", {})),
            })
        return result

    def _tool_choice_to_anthropic(self, tool_choice: Any) -> Dict:
        if isinstance(tool_choice, dict) and "function" in tool_choice:
            return {"type": "tool", "name": tool_choice["function"].get("name", "")}
        if tool_choice == "required":
            return {"type": "any"}
        if tool_choice == "auto":
            return {"type": "auto"}
        return {"type": "auto"}

    def parse_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        content_blocks = data.get("content", [])
        text_parts = []
        tool_calls = None

        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append({
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                })

        stop_reason = data.get("stop_reason", "end_turn")
        finish_reason = "stop"
        if stop_reason == "tool_use":
            finish_reason = "tool_calls"
        elif stop_reason == "max_tokens":
            finish_reason = "length"

        usage = data.get("usage")
        return {
            "content": "\n".join(text_parts) if text_parts else None,
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
            } if usage else None,
        }

    def chat_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/messages"):
            return base
        return f"{base}/messages"

    def parse_stream_line(self, line: str) -> Optional[Dict[str, Any]]:
        # Anthropic SSE: event: content_block_delta / data: {...}
        if not line.startswith("data:"):
            return None
        data_str = line[5:].strip()
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            return None

        event_type = data.get("type", "")
        if event_type == "content_block_delta":
            delta = data.get("delta", {})
            if delta.get("type") == "text_delta":
                return {"content": delta.get("text", "")}
        elif event_type == "message_delta":
            stop = data.get("delta", {}).get("stop_reason", "")
            return {"finish_reason": stop}
        return None
