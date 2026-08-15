"""
MediumModelClient 单测 — 覆盖 infra/model/medium_model_client.py 至 ~100%

策略：
- 用 MockSession 拦截 POST（不真实调用 API），可编程控制 status/data/异常。
- 构造参数传 api_key/api_url（参照 test_model_clients.py / test_post_format.py）。
- 依赖 settings 单例的字段用 monkeypatch 打桩（参照 test_post_format.py）。
"""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from infra.model.base_model import ChatMessage, ToolCall
from infra.model.medium_model_client import MediumModelClient
from config.settings import settings

DEFAULT_URL = "http://localhost:1/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DASHSCOPE_URL = "https://dashscope.aliyuncs.com/api/v1/apps/call"


class _MockResponse:
    def __init__(self, status=200, data=None, text=None):
        self.status = status
        self._data = data
        self._text = text

    async def json(self):
        return self._data

    async def text(self):
        return self._text if self._text is not None else ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class MockSession:
    """拦截 POST，支持按顺序消费响应或抛异常"""

    def __init__(self, responses=None, errors=None, error=None):
        self.responses = list(responses or [])
        self.errors = list(errors or [])
        self.error = error
        self.captured = []
        self.closed = False

    def post(self, url, **kwargs):
        self.captured.append({
            "url": url,
            "headers": dict(kwargs.get("headers", {})),
            "json": kwargs.get("json", {}),
        })
        if self.errors:
            raise self.errors.pop(0)
        if self.error is not None:
            raise self.error
        return self.responses.pop(0) if self.responses else _MockResponse()

    async def close(self):
        self.closed = True


def _make_client(api_url=DEFAULT_URL):
    return MediumModelClient(api_key="test-key", api_url=api_url)


def _attach_session(client, **kwargs):
    session = MockSession(**kwargs)
    client._session = session
    return session


OPENAI_OK = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}


# ---------------------------------------------------------------------------
# __init__ / from_config
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_init_defaults_openai(self):
        client = _make_client()
        assert client.api_key == "test-key"
        assert client.timeout == 60
        assert client.max_tokens == 1024
        assert client.temperature == 0.1
        assert client.supports_native_tools is True
        assert client._api_format == "openai"
        assert client._chat_url == DEFAULT_URL
        assert client._provider.model_name == settings.MEDIUM_MODEL_NAME or ""

    def test_init_anthropic_format(self):
        client = _make_client(ANTHROPIC_URL)
        assert client._api_format == "anthropic"
        assert client._chat_url == f"{ANTHROPIC_URL}"

    def test_init_dashscope_format(self):
        client = _make_client(DASHSCOPE_URL)
        assert client._api_format == "dashscope"
        assert client._chat_url == DASHSCOPE_URL

    def test_init_falls_back_to_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "MEDIUM_MODEL_API_KEY", "med-key")
        monkeypatch.setattr(settings, "MEDIUM_MODEL_API_URL", "http://fallback/v1/chat/completions")
        monkeypatch.setattr(settings, "MEDIUM_MODEL_NAME", "deepseek-v4-flash")
        client = MediumModelClient()
        assert client.api_key == "med-key"
        assert client.api_url == "http://fallback/v1/chat/completions"
        assert client.model_name == "deepseek-v4-flash"
        assert client._chat_url == "http://fallback/v1/chat/completions"

    def test_init_falls_back_to_large_key(self, monkeypatch):
        monkeypatch.setattr(settings, "MEDIUM_MODEL_API_KEY", "")
        monkeypatch.setattr(settings, "LARGE_MODEL_API_KEY", "large-key")
        monkeypatch.setattr(settings, "MEDIUM_MODEL_API_URL", "http://u/v1/chat/completions")
        client = MediumModelClient()
        assert client.api_key == "large-key"

    def test_from_config(self, monkeypatch):
        monkeypatch.setattr(settings, "MEDIUM_MODEL_API_KEY", "cfg-key")
        monkeypatch.setattr(settings, "MEDIUM_MODEL_API_URL", "http://cfg/v1/chat/completions")
        monkeypatch.setattr(settings, "MEDIUM_MODEL_NAME", "deepseek-v4-flash")
        client = MediumModelClient.from_config()
        assert isinstance(client, MediumModelClient)
        assert client.api_key == "cfg-key"
        assert client.model_name == "deepseek-v4-flash"


# ---------------------------------------------------------------------------
# chat()
# ---------------------------------------------------------------------------

class TestChat:
    def test_chat_success_openai_with_reasoning(self):
        client = _make_client()
        session = _attach_session(client, responses=[_MockResponse(status=200, data={
            "choices": [{
                "message": {"role": "assistant", "content": "answer", "reasoning_content": "thinking..."},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })])
        response = asyncio.run(client.chat([ChatMessage(role="user", content="hi")]))
        assert response.message.content == "answer"
        assert response.message.reasoning_content == "thinking..."
        assert response.usage == {"prompt_tokens": 10, "completion_tokens": 5}
        assert session.captured[0]["headers"]["Authorization"] == "Bearer test-key"

    def test_chat_serializes_messages_with_tools(self):
        client = _make_client()
        session = _attach_session(client, responses=[_MockResponse(status=200, data=OPENAI_OK)])
        messages = [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="assistant", content="", reasoning_content="rc",
                        tool_calls=[ToolCall(id="tc1", name="delegate_task", arguments="{}")]),
            ChatMessage(role="tool", content="tool result", tool_call_id="tc1"),
        ]
        asyncio.run(client.chat(messages))
        api_messages = session.captured[0]["json"]["messages"]
        assert api_messages[0] == {"role": "system", "content": "sys"}
        assert api_messages[1]["reasoning_content"] == "rc"
        assert api_messages[1]["tool_calls"] == [
            {"id": "tc1", "type": "function", "function": {"name": "delegate_task", "arguments": "{}"}}
        ]
        assert api_messages[2]["tool_call_id"] == "tc1"
        assert api_messages[2]["role"] == "tool"

    def test_chat_parse_tool_calls(self):
        client = _make_client()
        _attach_session(client, responses=[_MockResponse(status=200, data={
            "choices": [{
                "message": {"role": "assistant", "content": "",
                            "tool_calls": [{"id": "a", "type": "function",
                                            "function": {"name": "delegate_task", "arguments": "{\"x\":1}"}}]},
                "finish_reason": "tool_calls",
            }],
        })])
        response = asyncio.run(client.chat([ChatMessage(role="user", content="go")]))
        assert response.message.tool_calls is not None
        tc = response.message.tool_calls[0]
        assert tc.id == "a" and tc.name == "delegate_task" and json.loads(tc.arguments) == {"x": 1}

    def test_chat_model_override_updates_provider(self):
        client = _make_client()
        _attach_session(client, responses=[_MockResponse(status=200, data=OPENAI_OK)])
        asyncio.run(client.chat([ChatMessage(role="user", content="hi")], model="other-model"))
        assert client._provider.model_name == "other-model"

    def test_chat_anthropic_success(self):
        client = _make_client(ANTHROPIC_URL)
        _attach_session(client, responses=[_MockResponse(status=200, data={
            "content": [{"type": "text", "text": "hello claude"}],
            "usage": {"input_tokens": 3, "output_tokens": 2},
        })])
        response = asyncio.run(client.chat([ChatMessage(role="user", content="hi")]))
        assert response.message.content == "hello claude"

    def test_chat_anthropic_tool_use(self):
        client = _make_client(ANTHROPIC_URL)
        _attach_session(client, responses=[_MockResponse(status=200, data={
            "content": [
                {"type": "tool_use", "id": "tu1", "name": "delegate_task", "input": {"a": 1}},
                {"type": "text", "text": "done"},
            ],
            "stop_reason": "tool_use",
        })])
        response = asyncio.run(client.chat([ChatMessage(role="user", content="go")]))
        assert response.message.content == "done"
        assert response.message.tool_calls[0].id == "tu1"

    def test_chat_non_200_raises(self):
        client = _make_client()
        _attach_session(client, responses=[_MockResponse(status=500, text="server error")])
        with pytest.raises(Exception, match="API request failed: 500"):
            asyncio.run(client.chat([ChatMessage(role="user", content="hi")], max_retries=1))

    def test_chat_retries_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = _make_client()
        _attach_session(client,
                        responses=[_MockResponse(status=500, text="err"), _MockResponse(status=200, data=OPENAI_OK)])
        response = asyncio.run(client.chat([ChatMessage(role="user", content="hi")], max_retries=2))
        assert response.message.content == "ok"

    def test_chat_generic_error_retries_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = _make_client()
        _attach_session(client, errors=[RuntimeError("boom")], responses=[_MockResponse(status=200, data=OPENAI_OK)])
        response = asyncio.run(client.chat([ChatMessage(role="user", content="hi")], max_retries=2))
        assert response.message.content == "ok"

    def test_chat_timeout_exhausted_raises(self):
        client = _make_client()
        _attach_session(client, error=asyncio.TimeoutError())
        with pytest.raises(Exception, match="Medium model chat timeout"):
            asyncio.run(client.chat([ChatMessage(role="user", content="hi")], max_retries=1))

    def test_chat_timeout_retries_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = _make_client()
        _attach_session(client, errors=[asyncio.TimeoutError()], responses=[_MockResponse(status=200, data=OPENAI_OK)])
        response = asyncio.run(client.chat([ChatMessage(role="user", content="hi")], max_retries=2))
        assert response.message.content == "ok"

    def test_chat_empty_choices_raises(self):
        client = _make_client()
        _attach_session(client, responses=[_MockResponse(status=200, data={})])
        with pytest.raises(Exception, match="Empty choices"):
            asyncio.run(client.chat([ChatMessage(role="user", content="hi")]))


# ---------------------------------------------------------------------------
# generate()
# ---------------------------------------------------------------------------

class TestGenerate:
    def test_generate_requires_system_prompt(self):
        client = _make_client()
        with pytest.raises(TypeError):
            asyncio.run(client.generate("hi", system_prompt=""))

    def test_generate_success(self):
        client = _make_client()
        session = _attach_session(client, responses=[_MockResponse(status=200, data={
            "choices": [{"message": {"role": "assistant", "content": "hello"}}]
        })])
        result = asyncio.run(client.generate("hi", system_prompt="sys"))
        assert result == "hello"
        body = session.captured[0]["json"]
        assert body["messages"][0] == {"role": "system", "content": "sys"}
        assert body["messages"][1] == {"role": "user", "content": "hi"}

    def test_generate_does_not_use_reasoning_by_default(self):
        """默认：content 为空时不返回思维链（思考过程≠正式输出，§51）"""
        client = _make_client()
        _attach_session(client, responses=[_MockResponse(status=200, data={
            "choices": [{"message": {"role": "assistant", "content": "", "reasoning_content": "reasoned"}}]
        })])
        result = asyncio.run(client.generate("hi", system_prompt="sys"))
        assert result == ""

    def test_generate_reasoning_fallback_explicit_true(self):
        """显式 fallback_to_reasoning=True 才用思维链兜底"""
        client = _make_client()
        _attach_session(client, responses=[_MockResponse(status=200, data={
            "choices": [{"message": {"role": "assistant", "content": "", "reasoning_content": "reasoned"}}]
        })])
        result = asyncio.run(client.generate("hi", system_prompt="sys", fallback_to_reasoning=True))
        assert result == "reasoned"

    def test_generate_empty_message_returns_empty(self):
        client = _make_client()
        _attach_session(client, responses=[_MockResponse(status=200, data={
            "choices": [{"message": {"role": "assistant", "content": ""}}]
        })])
        result = asyncio.run(client.generate("hi", system_prompt="sys"))
        assert result == ""

    def test_generate_empty_reasoning_returns_empty(self):
        client = _make_client()
        _attach_session(client, responses=[_MockResponse(status=200, data={
            "choices": [{"message": {"role": "assistant", "content": "", "reasoning_content": ""}}]
        })])
        result = asyncio.run(client.generate("hi", system_prompt="sys"))
        assert result == ""

    def test_generate_no_choices_returns_empty(self):
        client = _make_client()
        _attach_session(client, responses=[_MockResponse(status=200, data={})])
        result = asyncio.run(client.generate("hi", system_prompt="sys"))
        assert result == ""

    def test_generate_anthropic_joins_text_blocks(self):
        client = _make_client(ANTHROPIC_URL)
        _attach_session(client, responses=[_MockResponse(status=200, data={
            "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        })])
        result = asyncio.run(client.generate("hi", system_prompt="sys"))
        assert result == "a\nb"

    def test_generate_anthropic_no_text_returns_empty(self):
        client = _make_client(ANTHROPIC_URL)
        _attach_session(client, responses=[_MockResponse(status=200, data={
            "content": [{"type": "image", "image": "x"}]
        })])
        result = asyncio.run(client.generate("hi", system_prompt="sys"))
        assert result == ""

    def test_generate_non_200_raises(self):
        client = _make_client()
        _attach_session(client, responses=[_MockResponse(status=500, text="boom")])
        with pytest.raises(Exception, match="API request failed: 500"):
            asyncio.run(client.generate("hi", system_prompt="sys"))

    def test_generate_timeout_reports_and_raises(self, monkeypatch):
        from unittest.mock import MagicMock
        reporter = MagicMock()
        monkeypatch.setattr("infra.model.medium_model_client.report_exception", reporter)
        client = _make_client()
        _attach_session(client, error=asyncio.TimeoutError())
        with pytest.raises(Exception, match="Medium model request timeout"):
            asyncio.run(client.generate("hi", system_prompt="sys"))
        assert reporter.call_count == 1

    def test_generate_generic_error_re_raises(self):
        client = _make_client()
        _attach_session(client, error=RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            asyncio.run(client.generate("hi", system_prompt="sys"))


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_health_check_ok(self):
        client = _make_client()
        _attach_session(client, responses=[_MockResponse(status=200, data=OPENAI_OK)])
        assert asyncio.run(client.health_check()) is True

    def test_health_check_failure(self):
        client = _make_client()
        _attach_session(client, error=RuntimeError("down"))
        assert asyncio.run(client.health_check()) is False
