"""测试：config/providers/dashscope.py — DashScope API 格式层。

纯格式层（同 test_provider_anthropic.py 模式），mock 网络边界。
"""
import pytest

from config.providers.dashscope import DashScopeProvider


@pytest.fixture
def provider():
    return DashScopeProvider(
        api_key="sk-dashscope-test",
        base_url="https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
        model_name="qwen-plus",
    )


class TestBuildHeaders:
    def test_headers(self, provider):
        h = provider.build_headers()
        assert h["Authorization"] == "Bearer sk-dashscope-test"
        assert h["Content-Type"] == "application/json"


class TestBuildRequest:
    def test_basic_payload(self, provider):
        payload = provider.build_request(
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1024,
            temperature=0.7,
        )
        assert payload["model"] == "qwen-plus"
        assert payload["input"]["messages"] == [{"role": "user", "content": "hi"}]
        assert payload["parameters"]["max_tokens"] == 1024
        assert payload["parameters"]["temperature"] == 0.7
        assert "stream" not in payload
        assert "tools" not in payload

    def test_stream_flag(self, provider):
        payload = provider.build_request([{"role": "user", "content": "x"}], 10, 0.5, stream=True)
        assert payload["stream"] is True

    def test_tools(self, provider):
        tools = [{"function": {"name": "lookup", "description": "d", "parameters": {}}}]
        payload = provider.build_request([{"role": "user", "content": "x"}], 10, 0.5, tools=tools)
        assert payload["tools"] == tools

    def test_tool_choice_dict_function_with_name(self, provider):
        payload = provider.build_request(
            [{"role": "user", "content": "x"}], 10, 0.5,
            tool_choice={"function": {"name": "lookup"}},
        )
        assert payload["parameters"]["tool_choice"] == {
            "type": "function",
            "function": {"name": "lookup"},
        }

    def test_tool_choice_dict_function_without_name(self, provider):
        # function 对象存在但没有 name → 不写 tool_choice
        payload = provider.build_request(
            [{"role": "user", "content": "x"}], 10, 0.5,
            tool_choice={"function": {}},
        )
        assert "tool_choice" not in payload["parameters"]

    def test_tool_choice_non_dict(self, provider):
        payload = provider.build_request(
            [{"role": "user", "content": "x"}], 10, 0.5,
            tool_choice="required",
        )
        assert payload["parameters"]["tool_choice"] == "required"

    def test_top_p_accepted_but_ignored(self, provider):
        payload = provider.build_request([{"role": "user", "content": "x"}], 10, 0.5, top_p=0.9)
        assert "top_p" not in payload


class TestParseResponse:
    def test_text_response(self, provider):
        out = provider.parse_response({
            "output": {"choices": [{"message": {"content": "你好"}, "finish_reason": "stop"}]},
            "usage": {"total_tokens": 10},
        })
        assert out["content"] == "你好"
        assert out["finish_reason"] == "stop"
        assert out["tool_calls"] is None
        assert out["usage"] == {"total_tokens": 10}

    def test_tool_calls_response(self, provider):
        out = provider.parse_response({
            "output": {
                "choices": [{
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"id": "t1", "function": {"name": "lookup", "arguments": '{"q": "x"}'}},
                        ],
                    },
                    "finish_reason": "tool_calls",
                }],
            },
            "usage": None,
        })
        assert out["tool_calls"] == [
            {"id": "t1", "name": "lookup", "arguments": '{"q": "x"}'},
        ]
        assert out["finish_reason"] == "tool_calls"

    def test_empty_choices_fallback_to_text(self, provider):
        out = provider.parse_response({"output": {"text": "fallback"}})
        assert out["content"] == "fallback"
        assert out["finish_reason"] == "stop"
        assert out["usage"] is None

    def test_no_choices_empty_output(self, provider):
        out = provider.parse_response({"output": {}})
        assert out["content"] == ""


class TestChatUrl:
    def test_returns_base_url(self, provider):
        assert provider.chat_url() == (
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
        )


class TestParseStreamLine:
    def test_non_data_line(self, provider):
        assert provider.parse_stream_line("event: ping") is None
        assert provider.parse_stream_line(": keep-alive") is None

    def test_data_empty(self, provider):
        assert provider.parse_stream_line("data:") is None
        assert provider.parse_stream_line("data:  ") is None

    def test_invalid_json(self, provider):
        assert provider.parse_stream_line("data: {not json") is None

    def test_text_delta(self, provider):
        line = 'data: {"output": {"text": "你好"}}'
        assert provider.parse_stream_line(line) == {"content": "你好"}

    def test_empty_text(self, provider):
        line = 'data: {"output": {"text": ""}}'
        assert provider.parse_stream_line(line) is None
