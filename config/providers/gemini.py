"""
Google Gemini 原生 API 适配器（generativelanguage.googleapis.com）

协议与 OpenAI / Anthropic 均不同：contents[] / parts[] 结构，
流式为 :streamGenerateContent?alt=sse。认证用 x-goog-api-key 头。
"""
import json
from typing import Any, Dict, List, Optional

from config.providers.base import ProviderBase


class GeminiProvider(ProviderBase):
    """Google Gemini 原生 GenerateContent API"""

    format_name = "gemini"

    def build_headers(self) -> Dict[str, str]:
        return {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # 消息/工具转换
    # ------------------------------------------------------------------

    @staticmethod
    def _role_to_gemini(role: str) -> str:
        if role == "assistant":
            return "model"
        if role == "system":
            return "user"  # system 单独提取，不进入 contents
        return role  # user / tool → user（工具结果也归入 user）

    def _messages_to_contents(self, messages: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[str]]:
        contents: List[Dict[str, Any]] = []
        system_texts: List[str] = []
        for m in messages:
            role = m.get("role", "user")
            if role == "system":
                content = m.get("content")
                if isinstance(content, str):
                    system_texts.append(content)
                continue
            content = m.get("content") or ""
            parts: List[Dict[str, Any]] = []
            if m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    fn = tc.get("function", tc)
                    args = fn.get("arguments") or "{}"
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    parts.append({
                        "functionCall": {"name": fn.get("name", ""), "args": args},
                    })
            elif m.get("tool_call_id"):
                parts.append({"text": content})
            else:
                parts.append({"text": content})
            contents.append({"role": self._role_to_gemini(role), "parts": parts})
        return contents, system_texts

    def _tools_to_gemini(self, tools: List[Dict]) -> Optional[List[Dict]]:
        declarations = []
        for t in tools:
            func = t.get("function", t)
            declarations.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "parameters": func.get("parameters", func.get("input_schema", {})),
            })
        if not declarations:
            return None
        return [{"functionDeclarations": declarations}]

    # ------------------------------------------------------------------
    # 请求构建 / URL
    # ------------------------------------------------------------------

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
        contents, system_texts = self._messages_to_contents(messages)
        payload: Dict[str, Any] = {"contents": contents}
        if system_texts:
            payload["systemInstruction"] = {
                "parts": [{"text": "".join(system_texts)}],
            }
        gen_cfg: Dict[str, Any] = {"maxOutputTokens": max_tokens}
        if temperature is not None:
            gen_cfg["temperature"] = temperature
        if top_p is not None:
            gen_cfg["topP"] = top_p
        payload["generationConfig"] = gen_cfg
        gemini_tools = self._tools_to_gemini(tools or [])
        if gemini_tools:
            payload["tools"] = gemini_tools
        if tool_choice:
            # Gemini 原生无强制 tool_choice，仅可设 functionCallingConfig
            pass
        return payload

    def chat_url(self) -> str:
        base = self.base_url.rstrip("/")
        return f"{base}/models/{self.model_name}:generateContent"

    def stream_url(self) -> str:
        base = self.base_url.rstrip("/")
        return f"{base}/models/{self.model_name}:streamGenerateContent?alt=sse"

    # ------------------------------------------------------------------
    # 响应解析
    # ------------------------------------------------------------------

    def parse_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        candidates = data.get("candidates") or []
        text_parts: List[str] = []
        tool_calls = None
        finish_reason = "stop"
        if candidates:
            content = candidates[0].get("content", {})
            for part in content.get("parts", []):
                if "text" in part:
                    text_parts.append(part["text"])
                elif "functionCall" in part:
                    if tool_calls is None:
                        tool_calls = []
                    fc = part["functionCall"]
                    tool_calls.append({
                        "id": fc.get("name", ""),
                        "name": fc.get("name", ""),
                        "arguments": json.dumps(fc.get("args", {}), ensure_ascii=False),
                    })
            fr = candidates[0].get("finishReason", "STOP")
            if fr == "STOP":
                finish_reason = "stop"
            elif fr == "MAX_TOKENS":
                finish_reason = "length"
            elif fr == "MALFORMED_FUNCTION_CALL":
                finish_reason = "tool_calls"
        usage = data.get("usageMetadata")
        usage_out = None
        if usage:
            usage_out = {
                "prompt_tokens": usage.get("promptTokenCount", 0),
                "completion_tokens": usage.get("candidatesTokenCount", 0),
            }
        return {
            "content": "\n".join(text_parts) if text_parts else None,
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
            "usage": usage_out,
            "reasoning_content": self._extract_reasoning(candidates),
        }

    @staticmethod
    def _extract_reasoning(candidates: List[Dict]) -> Optional[str]:
        try:
            thought = candidates[0].get("content", {}).get("parts", [])
            for p in thought:
                if "thought" in p:
                    return p["thought"]
        except Exception:
            pass
        return None

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
        candidates = data.get("candidates") or []
        if not candidates:
            return None
        parts = candidates[0].get("content", {}).get("parts", [])
        result: Dict[str, Any] = {}
        for part in parts:
            if "text" in part:
                result["content"] = result.get("content", "") + part["text"]
            elif "functionCall" in part:
                fc = part["functionCall"]
                result["tool_calls"] = result.get("tool_calls", []) + [
                    {"id": fc.get("name", ""), "name": fc.get("name", ""),
                     "arguments": json.dumps(fc.get("args", {}), ensure_ascii=False)},
                ]
        fin = candidates[0].get("finishReason")
        if fin:
            result["finish_reason"] = "stop" if fin == "STOP" else fin.lower()
        return result if result else None