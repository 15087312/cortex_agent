"""
测试reasoning_content处理

验证thinking模式下的reasoning_content正确保存和传回
"""
import pytest
from infra.model.base_model import ChatMessage, ToolCall
from infra.model.large_model_client import LargeModelClient


class TestReasoningContent:
    """测试reasoning_content处理"""

    def test_chat_message_has_reasoning_content_field(self):
        """ChatMessage应该有reasoning_content字段"""
        msg = ChatMessage(
            role="assistant",
            content="test",
            reasoning_content="thinking process"
        )
        assert msg.reasoning_content == "thinking process"

    def test_chat_message_reasoning_content_default_none(self):
        """reasoning_content默认值应该是None"""
        msg = ChatMessage(role="assistant", content="test")
        assert msg.reasoning_content is None

    def test_parse_openai_response_with_reasoning_content(self):
        """解析OpenAI响应时应该保存reasoning_content"""
        client = LargeModelClient(api_key="test", api_url="http://test.com")
        client._api_format = "openai"
        
        data = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "final answer",
                    "reasoning_content": "thinking process here"
                },
                "finish_reason": "stop"
            }]
        }
        
        response = client._parse_chat_response(data)
        assert response.message.content == "final answer"
        assert response.message.reasoning_content == "thinking process here"

    def test_messages_to_api_includes_reasoning_content(self):
        """消息转换时应该包含reasoning_content"""
        client = LargeModelClient(api_key="test", api_url="http://test.com")
        client._api_format = "openai"
        
        messages = [
            ChatMessage(
                role="assistant",
                content="response",
                reasoning_content="my thinking"
            )
        ]
        
        api_messages = client._messages_to_api(messages)
        assert len(api_messages) == 1
        assert api_messages[0].get("reasoning_content") == "my thinking"

    def test_messages_to_api_no_reasoning_content(self):
        """没有reasoning_content时不应该包含该字段"""
        client = LargeModelClient(api_key="test", api_url="http://test.com")
        client._api_format = "openai"
        
        messages = [
            ChatMessage(role="assistant", content="response")
        ]
        
        api_messages = client._messages_to_api(messages)
        assert len(api_messages) == 1
        assert "reasoning_content" not in api_messages[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])