"""测试：config/providers/gemini.py — Google Gemini 原生格式适配器。"""
import pytest

from config.providers.gemini import GeminiProvider


@pytest.fixture
def provider():
    return GeminiProvider(api_key="key-1", base_url="https://generativelanguage.googleapis.com/v1beta",
                          model_name="gemini-2.0-flash")


class TestHeaders:
    def test_headers(self, provider):
        h = provider.build_headers()
        assert h["x-goog-api-key"] == "key-1"
        assert h["Content-Type"] == "application/json"


class TestBuildRequest:
    def test_basic(self, provider):
        req = provider.build_request([{"role": "user", "content": "hi"}], 100, 0.5)
        assert req["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
        assert req["generationConfig"]["maxOutputTokens"] == 100
        assert req["generationConfig"]["temperature"] == 0.5

    def test_system_extracted(self, provider):
        req = provider.build_request(
            [{"role": "system", "content": "be nice"}, {"role": "user", "content": "hi"}], 10, 0.2)
        assert req["systemInstruction"] == {"parts": [{"text": "be nice"}]}
        # system 不进入 contents
        assert all(c["role"] != "system" for c in req["contents"])

    def test_tools(self, provider):
        req = provider.build_request(
            [{"role": "user", "content": "x"}], 10, 0.5,
            tools=[{"function": {"name": "lookup", "description": "d",
                                 "parameters": {"type": "object"}}}])
        assert req["tools"] == [{"functionDeclarations": [{"name": "lookup", "description": "d",
                                                           "parameters": {"type": "object"}}]}]

    def test_tool_call_history(self, provider):
        msgs = [
            {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "calc",
                                                                          "arguments": '{"a":1}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "42"},
        ]
        req = provider.build_request(msgs, 10, 0.5)
        assert req["contents"][0]["role"] == "model"
        assert "functionCall" in req["contents"][0]["parts"][0]


class TestChatUrl:
    def test_generate_content(self, provider):
        assert provider.chat_url() == \
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

    def test_stream_url(self, provider):
        assert provider.stream_url() == \
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:streamGenerateContent?alt=sse"


class TestParseResponse:
    def test_text_and_tool(self, provider):
        out = provider.parse_response({
            "candidates": [{"content": {"parts": [{"text": "答"}, {"functionCall": {"name": "calc",
                                                                                  "args": {"a": 1}}}]},
                            "finishReason": "STOP"}],
        })
        assert out["content"] == "答"
        assert out["tool_calls"][0]["name"] == "calc"
        assert out["finish_reason"] == "stop"

    def test_no_candidates(self, provider):
        out = provider.parse_response({})
        assert out["content"] is None
        assert out["tool_calls"] is None


class TestParseStreamLine:
    def test_text_delta(self, provider):
        out = provider.parse_stream_line(
            'data: {"candidates":[{"content":{"parts":[{"text":"加"}]}}]}')
        assert out == {"content": "加"}

    def test_non_data(self, provider):
        assert provider.parse_stream_line("event: ping") is None

    def test_done(self, provider):
        assert provider.parse_stream_line("data: [DONE]") is None


class TestGeminiBranches:
    def test_role_to_gemini_system_returns_user(self, provider):
        assert provider._role_to_gemini("system") == "user"
        assert provider._role_to_gemini("assistant") == "model"
        assert provider._role_to_gemini("tool") == "tool"

    def test_system_non_str_content_ignored(self, provider):
        contents, systems = provider._messages_to_contents(
            [{"role": "system", "content": {"complex": True}}])
        assert systems == []
        assert contents == []

    def test_tool_call_invalid_json_args(self, provider):
        req = provider.build_request(
            [{"role": "assistant", "tool_calls": [{"id": "c1", "function": {
                "name": "calc", "arguments": "not-json"}}]}], 10, 0.5)
        part = req["contents"][0]["parts"][0]
        assert part["functionCall"]["name"] == "calc"
        assert part["functionCall"]["args"] == {}

    def test_tool_call_args_dict(self, provider):
        msgs = [{"role": "assistant", "tool_calls": [{
            "function": {"name": "f", "arguments": {"a": 1}}}]}]
        req = provider.build_request(msgs, 10, 0.5)
        assert req["contents"][0]["parts"][0]["functionCall"]["args"] == {"a": 1}

    def test_top_p_and_tool_choice(self, provider):
        req = provider.build_request([{"role": "user", "content": "x"}], 10, None, top_p=0.9, tool_choice="auto")
        assert req["generationConfig"]["topP"] == 0.9
        assert "temperature" not in req["generationConfig"]

    def test_parse_finish_length(self, provider):
        out = provider.parse_response({"candidates": [{
            "content": {"parts": [{"text": "长"}]}, "finishReason": "MAX_TOKENS"}],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 5}})
        assert out["finish_reason"] == "length"
        assert out["usage"] == {"prompt_tokens": 2, "completion_tokens": 5}

    def test_parse_finish_malformed(self, provider):
        out = provider.parse_response({"candidates": [{
            "content": {"parts": [{"functionCall": {"name": "f", "args": {}}}]},
            "finishReason": "MALFORMED_FUNCTION_CALL"}]})
        assert out["finish_reason"] == "tool_calls"
        assert out["tool_calls"][0]["name"] == "f"

    def test_extract_reasoning_thought(self, provider):
        candidates = [{"content": {"parts": [{"text": "x"}, {"thought": "思考中"}]}}]
        assert provider._extract_reasoning(candidates) == "思考中"

    def test_stream_bad_json_and_no_candidates(self, provider):
        assert provider.parse_stream_line("data: {bad") is None
        assert provider.parse_stream_line('data: {"candidates":[]}') is None

    def test_stream_function_call_and_finish(self, provider):
        out = provider.parse_stream_line(
            'data: {"candidates":[{"content":{"parts":[{"functionCall":{"name":"f","args":{}}}]},'
            '"finishReason":"SAFETY"}]}')
        assert out["tool_calls"][0]["name"] == "f"
        assert out["finish_reason"] == "safety"