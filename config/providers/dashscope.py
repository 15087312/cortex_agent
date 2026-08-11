"""
DashScope API 适配器 (阿里云百炼)
"""
from typing import Dict, Any, List, Optional
import json
from config.providers.base import ProviderBase


class DashScopeProvider(ProviderBase):
    """阿里云 DashScope API"""

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
        top_p: Optional[float] = None,  # DashScope 不支持 top_p，接受但忽略
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "input": {"messages": messages},
            "parameters": {
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
        }
        if stream:
            payload["stream"] = True
        if tools:
            payload["tools"] = tools
        if tool_choice:
            if isinstance(tool_choice, dict) and "function" in tool_choice:
                func_name = tool_choice["function"].get("name")
                if func_name:
                    payload["parameters"]["tool_choice"] = {
                        "type": "function",
                        "function": {"name": func_name},
                    }
            else:
                payload["parameters"]["tool_choice"] = tool_choice
        return payload

    def parse_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        output = data.get("output", {})
        choices = output.get("choices", [])

        if choices:
            choice = choices[0]
            message = choice.get("message", {})
            content = message.get("content")
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

            return {
                "content": content,
                "tool_calls": tool_calls,
                "finish_reason": finish_reason,
                "usage": data.get("usage"),
            }

        return {
            "content": output.get("text", ""),
            "tool_calls": None,
            "finish_reason": "stop",
            "usage": None,
        }

    def chat_url(self) -> str:
        return self.base_url

    def parse_stream_line(self, line: str) -> Optional[Dict[str, Any]]:
        if not line.startswith("data:"):
            return None
        data_str = line[5:].strip()
        if not data_str:
            return None
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            return None
        text = data.get("output", {}).get("text", "")
        return {"content": text} if text else None
