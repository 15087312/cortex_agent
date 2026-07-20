"""
Tests for model clients — data classes and helper methods.
"""
import pytest
from infra.model.base_model import ChatMessage, ChatResponse, ToolCall


class TestDataClasses:
    def test_chat_message_defaults(self):
        msg = ChatMessage(role="user", content="hello")
        assert msg.role == "user"
        assert msg.content == "hello"
        assert msg.tool_calls is None
        assert msg.tool_call_id is None

    def test_chat_message_with_tool_calls(self):
        tc = ToolCall(id="123", name="search", arguments='{"q":"test"}')
        msg = ChatMessage(role="assistant", content="", tool_calls=[tc])
        assert msg.tool_calls is not None
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "search"

    def test_tool_call(self):
        tc = ToolCall(id="123", name="search", arguments='{"q":"test"}')
        assert tc.name == "search"
        assert tc.id == "123"
        assert tc.arguments == '{"q":"test"}'

    def test_chat_message_tool_response(self):
        msg = ChatMessage(role="tool", content="result data", tool_call_id="tc_123")
        assert msg.role == "tool"
        assert msg.tool_call_id == "tc_123"
