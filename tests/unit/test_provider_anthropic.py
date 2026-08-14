"""
测试：config/providers/anthropic.py — Anthropic Messages API 格式层。
纯字符串/字典处理，无网络边界。
"""
import pytest

from config.providers.anthropic import AnthropicProvider


@pytest.fixture
def provider():
    return AnthropicProvider(api_key="sk-test", base_url="https://api.anthropic.com/v1",
                             model_name="claude-3-5-sonnet")


class TestBuildHeaders:
    def test_headers(self, provider):
        h = provider.build_headers()
        assert h["x-api-key"] == "sk-test"
        assert h["anthropic-version"] == "2023-06-01"
        assert h["content-type"] == "application/json"


class TestBuildRequest:
    def test_basic_payload(self, provider):
        payload = provider.build_request(
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1024,
            temperature=0.7,
        )
        assert payload["model"] == "claude-3-5-sonnet"
        assert payload["max_tokens"] == 1024
        assert payload["messages"] == [{"role": "user", "content": "hi"}]
        assert "system" not in payload
        assert "tools" not in payload
        assert "tool_choice" not in payload
        assert "stream" not in payload

    def test_system_message_separated(self, provider):
        payload = provider.build_request(
            messages=[
                {"role": "system", "content": "be nice"},
                {"role": "user", "content": "hi"},
            ],
            max_tokens=10,
            temperature=0.0,
        )
        assert payload["system"] == "be nice"
        assert payload["messages"] == [{"role": "user", "content": "hi"}]

    def test_system_content_can_be_non_string(self, provider):
        payload = provider.build_request(
            messages=[{"role": "system", "content": [{"text": "x"}]}],
            max_tokens=10,
            temperature=0.0,
        )
        assert payload["system"] == [{"text": "x"}]

    def test_stream_flag(self, provider):
        payload = provider.build_request([{"role": "user", "content": "x"}], 10, 0.5, stream=True)
        assert payload["stream"] is True

    def test_tools_and_tool_choice(self, provider):
        tools = [{"function": {"name": "lookup", "description": "d",
                               "parameters": {"type": "object"}}}]
        payload = provider.build_request(
            [{"role": "user", "content": "x"}], 10, 0.5,
            tools=tools, tool_choice={"function": {"name": "lookup"}},
        )
        assert payload["tools"] == [{"name": "lookup", "description": "d",
                                     "input_schema": {"type": "object"}}]
        assert payload["tool_choice"] == {"type": "tool", "name": "lookup"}


class TestToolsConversion:
    def test_wrapped_function(self, provider):
        out = provider._tools_to_anthropic(
            [{"function": {"name": "a", "description": "d", "parameters": {"p": 1}}}])
        assert out == [{"name": "a", "description": "d", "input_schema": {"p": 1}}]

    def test_flat_tool_and_input_schema(self, provider):
        out = provider._tools_to_anthropic(
            [{"name": "b", "description": "", "input_schema": {"s": 1}}])
        assert out == [{"name": "b", "description": "", "input_schema": {"s": 1}}]

    def test_missing_fields_default(self, provider):
        out = provider._tools_to_anthropic([{}])
        assert out == [{"name": "", "description": "", "input_schema": {}}]


class TestToolChoiceConversion:
    def test_dict_with_function(self, provider):
        assert provider._tool_choice_to_anthropic({"function": {"name": "lookup"}}) == \
            {"type": "tool", "name": "lookup"}

    def test_dict_function_missing_name(self, provider):
        assert provider._tool_choice_to_anthropic({"function": {}}) == \
            {"type": "tool", "name": ""}

    def test_required(self, provider):
        assert provider._tool_choice_to_anthropic("required") == {"type": "any"}

    def test_auto(self, provider):
        assert provider._tool_choice_to_anthropic("auto") == {"type": "auto"}

    def test_other_falls_back_to_auto(self, provider):
        assert provider._tool_choice_to_anthropic("none") == {"type": "auto"}
        assert provider._tool_choice_to_anthropic(None) == {"type": "auto"}


class TestParseResponse:
    def test_text_and_tool_blocks(self, provider):
        data = {
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "tool_use", "id": "t1", "name": "lookup",
                 "input": {"q": "中文"}},
                {"type": "text", "text": " world"},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 5, "output_tokens": 3},
        }
        out = provider.parse_response(data)
        assert out["content"] == "hello\n world"
        assert out["tool_calls"] == [{"id": "t1", "name": "lookup",
                                      "arguments": '{"q": "中文"}'}]
        assert out["finish_reason"] == "tool_calls"
        assert out["usage"] == {"prompt_tokens": 5, "completion_tokens": 3}

    def test_multiple_tool_use_blocks(self, provider):
        data = {
            "content": [
                {"type": "tool_use", "id": "t1", "name": "a", "input": {}},
                {"type": "tool_use", "id": "t2", "name": "b", "input": {}},
            ],
            "stop_reason": "tool_use",
            "usage": None,
        }
        out = provider.parse_response(data)
        assert len(out["tool_calls"]) == 2
        assert out["tool_calls"][1]["id"] == "t2"

    def test_non_text_non_tool_block_skipped(self, provider):
        data = {
            "content": [
                {"type": "image", "source": {"data": "..."}},
                {"type": "text", "text": "kept"},
            ],
            "stop_reason": "end_turn",
            "usage": None,
        }
        out = provider.parse_response(data)
        assert out["content"] == "kept"
        assert out["tool_calls"] is None

    def test_no_text_parts(self, provider):
        out = provider.parse_response({"content": [], "stop_reason": "end_turn"})
        assert out["content"] is None
        assert out["tool_calls"] is None
        assert out["finish_reason"] == "stop"

    def test_max_tokens_stop(self, provider):
        out = provider.parse_response({"content": [], "stop_reason": "max_tokens"})
        assert out["finish_reason"] == "length"

    def test_no_usage(self, provider):
        out = provider.parse_response({"content": [], "stop_reason": "end_turn"})
        assert out["usage"] is None


class TestChatUrl:
    def test_base_with_messages(self):
        p = AnthropicProvider("k", "https://api.anthropic.com/v1/messages", "m")
        assert p.chat_url() == "https://api.anthropic.com/v1/messages"

    def test_base_without_messages(self):
        p = AnthropicProvider("k", "https://api.anthropic.com/v1/", "m")
        assert p.chat_url() == "https://api.anthropic.com/v1/messages"


class TestParseStreamLine:
    def test_non_data_line(self, provider):
        assert provider.parse_stream_line("event: ping") is None
        assert provider.parse_stream_line("") is None

    def test_invalid_json(self, provider):
        assert provider.parse_stream_line("data: {not json") is None

    def test_text_delta(self, provider):
        line = 'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}}'
        assert provider.parse_stream_line(line) == {"content": "hi"}

    def test_text_delta_missing_text(self, provider):
        line = 'data: {"type": "content_block_delta", "delta": {"type": "text_delta"}}'
        assert provider.parse_stream_line(line) == {"content": ""}

    def test_message_delta(self, provider):
        line = 'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}'
        assert provider.parse_stream_line(line) == {"finish_reason": "end_turn"}

    def test_other_delta_types(self, provider):
        line = 'data: {"type": "content_block_delta", "delta": {"type": "input_json_delta"}}'
        assert provider.parse_stream_line(line) is None

    def test_unknown_event_type(self, provider):
        assert provider.parse_stream_line('data: {"type": "ping"}') is None
