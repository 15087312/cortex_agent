"""SmallModelClient 补充测试 — chat/generate/health_check 完整路径覆盖"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from config.settings import settings
from infra.model.base_model import ChatMessage, ToolCall
from infra.model.small_model_client import SmallModelClient

DEFAULT_URL = "https://api.deepseek.com/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

OPENAI_OK = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


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

    async def __aexit__(self, *a):
        return False


class MockSession:
    def __init__(self, responses=None, errors=None, error=None):
        self.responses = list(responses or [])
        self.errors = list(errors or [])
        self.error = error
        self.captured = []
        self.closed = False

    def post(self, url, **kwargs):
        self.captured.append({"url": url, "json": kwargs.get("json", {})})
        if self.errors:
            raise self.errors.pop(0)
        if self.error is not None:
            raise self.error
        return self.responses.pop(0) if self.responses else _MockResponse()

    async def close(self):
        self.closed = True


def _make_client(api_url=DEFAULT_URL, **kw):
    kw.setdefault("api_key", "test-key")
    return SmallModelClient(api_url=api_url, **kw)


def _attach(client, **kw):
    s = MockSession(**kw)
    client._session = s
    return s


class TestConstructor:
    def test_init_defaults_openai(self):
        client = _make_client()
        assert client.api_key == "test-key"
        assert client._api_format == "openai"
        assert client.max_tokens == 512
        assert client.temperature == 0.3
        assert client.supports_native_tools is True
        assert client._chat_url == DEFAULT_URL

    def test_init_anthropic(self):
        client = _make_client(ANTHROPIC_URL)
        assert client._api_format == "anthropic"
        assert client._chat_url == ANTHROPIC_URL

    def test_init_model_name_and_args(self):
        client = _make_client(model_name="my-model", max_tokens=100, temperature=0.1)
        assert client.model_name == "my-model"
        assert client.max_tokens == 100
        assert client.temperature == 0.1

    def test_init_falls_back_to_small_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "SMALL_MODEL_API_KEY", "small-key")
        monkeypatch.setattr(settings, "SMALL_MODEL_API_URL", "http://small/v1/chat/completions")
        client = SmallModelClient()
        assert client.api_key == "small-key"
        assert client.api_url == "http://small/v1/chat/completions"

    def test_init_falls_back_to_large_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "SMALL_MODEL_API_KEY", "")
        monkeypatch.setattr(settings, "SMALL_MODEL_API_URL", "")
        monkeypatch.setattr(settings, "LARGE_MODEL_API_KEY", "large-key")
        monkeypatch.setattr(settings, "LARGE_MODEL_API_URL", "http://large/v1/chat/completions")
        client = SmallModelClient()
        assert client.api_key == "large-key"
        assert client.api_url == "http://large/v1/chat/completions"

    def test_from_config(self, monkeypatch):
        monkeypatch.setattr(settings, "SMALL_MODEL_API_KEY", "cfg-key")
        monkeypatch.setattr(settings, "SMALL_MODEL_API_URL", "http://cfg/v1/chat/completions")
        monkeypatch.setattr(settings, "SMALL_MODEL_NAME", "deepseek-v4-flash")
        client = SmallModelClient.from_config()
        assert isinstance(client, SmallModelClient)
        assert client.api_key == "cfg-key"
        assert client.model_name == "deepseek-v4-flash"


class TestChat:
    def test_success_with_tool_calls(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, data={
            "choices": [{"message": {"content": "",
                                     "tool_calls": [{"id": "a", "function": {"name": "f", "arguments": "{}"}}]},
                          "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        })])
        resp = asyncio.run(client.chat([ChatMessage(role="user", content="hi")]))
        tc = resp.message.tool_calls[0]
        assert tc.id == "a" and tc.name == "f"
        assert resp.usage == {"prompt_tokens": 3, "completion_tokens": 2}

    def test_serializes_tool_messages(self):
        client = _make_client()
        session = _attach(client, responses=[_MockResponse(status=200, data=OPENAI_OK)])
        msgs = [
            ChatMessage(role="assistant", content="",
                        tool_calls=[ToolCall(id="t1", name="f", arguments="{}")]),
            ChatMessage(role="tool", content="res", tool_call_id="t1"),
        ]
        asyncio.run(client.chat(msgs))
        api_messages = session.captured[0]["json"]["messages"]
        assert api_messages[0]["tool_calls"] == [
            {"id": "t1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
        ]
        assert api_messages[1]["tool_call_id"] == "t1"

    def test_model_override(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, data=OPENAI_OK)])
        asyncio.run(client.chat([ChatMessage(role="user", content="hi")], model="other"))
        assert client._provider.model_name == "other"

    def test_anthropic_success(self):
        client = _make_client(ANTHROPIC_URL)
        _attach(client, responses=[_MockResponse(status=200, data={
            "content": [{"type": "text", "text": "hello"}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })])
        resp = asyncio.run(client.chat([ChatMessage(role="user", content="hi")]))
        assert resp.message.content == "hello"

    def test_empty_choices_raises(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, data={})])
        with pytest.raises(Exception, match="Empty choices"):
            asyncio.run(client.chat([ChatMessage(role="user", content="hi")], max_retries=1))

    def test_non_200_retry_then_success(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=500, text="err"), _MockResponse(status=200, data=OPENAI_OK)])
        resp = asyncio.run(client.chat([ChatMessage(role="user", content="hi")], max_retries=2))
        assert resp.message.content == "ok"

    def test_non_200_exhausted_raises(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=500, text="err")])
        with pytest.raises(Exception, match="API request failed: 500"):
            asyncio.run(client.chat([ChatMessage(role="user", content="hi")], max_retries=1))

    def test_timeout_retry_then_success(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = _make_client()
        _attach(client, errors=[asyncio.TimeoutError()], responses=[_MockResponse(status=200, data=OPENAI_OK)])
        resp = asyncio.run(client.chat([ChatMessage(role="user", content="hi")], max_retries=2))
        assert resp.message.content == "ok"

    def test_timeout_exhausted_raises(self):
        client = _make_client()
        _attach(client, error=asyncio.TimeoutError())
        with pytest.raises(Exception, match="Small model chat timeout"):
            asyncio.run(client.chat([ChatMessage(role="user", content="hi")], max_retries=1))

    def test_generic_error_retry_then_success(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = _make_client()
        _attach(client, errors=[RuntimeError("boom")], responses=[_MockResponse(status=200, data=OPENAI_OK)])
        resp = asyncio.run(client.chat([ChatMessage(role="user", content="hi")], max_retries=2))
        assert resp.message.content == "ok"

    def test_generic_error_exhausted_raises(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = _make_client()
        _attach(client, error=RuntimeError("boom"))
        with pytest.raises(Exception, match="boom"):
            asyncio.run(client.chat([ChatMessage(role="user", content="hi")], max_retries=1))


class TestGenerate:
    def test_requires_system_prompt(self):
        client = _make_client()
        with pytest.raises(TypeError):
            asyncio.run(client.generate("hi"))
        with pytest.raises(TypeError):
            asyncio.run(client.generate("hi", system_prompt=""))

    def test_success(self):
        client = _make_client()
        session = _attach(client, responses=[_MockResponse(status=200, data={
            "choices": [{"message": {"content": "hello "}}],
        })])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))
        assert out == "hello"
        body = session.captured[0]["json"]
        assert body["messages"][0] == {"role": "system", "content": "sys"}
        assert body["messages"][1] == {"role": "user", "content": "hi"}

    def test_reasoning_fallback(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, data={
            "choices": [{"message": {"content": "", "reasoning_content": "reasoned "}}],
        })])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))
        assert out == "reasoned"

    def test_empty_reasoning_returns_empty(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, data={
            "choices": [{"message": {"content": "", "reasoning_content": ""}}],
        })])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))
        assert out == ""

    def test_anthropic_joins_text(self):
        client = _make_client(ANTHROPIC_URL)
        _attach(client, responses=[_MockResponse(status=200, data={
            "content": [{"type": "text", "text": " a "}, {"type": "text", "text": "b"}],
        })])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))
        assert out == "a \nb"

    def test_anthropic_no_text(self):
        client = _make_client(ANTHROPIC_URL)
        _attach(client, responses=[_MockResponse(status=200, data={"content": []})])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))
        assert out == ""

    def test_no_choices_raises(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, data={})])
        with pytest.raises(Exception, match="No choices in response"):
            asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))

    def test_non_200_raises_and_reports(self, monkeypatch):
        reporter = MagicMock()
        monkeypatch.setattr("infra.model.small_model_client.report_api_error", reporter)
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=500, text="boom")])
        with pytest.raises(Exception, match="API request failed: 500"):
            asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))
        assert reporter.call_count == 1

    def test_non_200_json_error_body(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=429, text='{"error": "rate limit"}')])
        with pytest.raises(Exception, match="rate limit"):
            asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))

    def test_timeout_retry_then_success(self, monkeypatch):
        reporter = MagicMock()
        monkeypatch.setattr("infra.model.small_model_client.report_exception", reporter)
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = _make_client()
        _attach(client, errors=[asyncio.TimeoutError()], responses=[_MockResponse(status=200, data=OPENAI_OK)])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=2))
        assert out == "ok"
        assert reporter.call_count == 1

    def test_timeout_exhausted_raises(self, monkeypatch):
        reporter = MagicMock()
        monkeypatch.setattr("infra.model.small_model_client.report_exception", reporter)
        client = _make_client()
        _attach(client, error=asyncio.TimeoutError())
        with pytest.raises(Exception, match="Small model timeout"):
            asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))
        assert reporter.call_count == 1

    def test_generic_error_retry_then_success(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = _make_client()
        _attach(client, errors=[RuntimeError("boom")], responses=[_MockResponse(status=200, data=OPENAI_OK)])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=2))
        assert out == "ok"

    def test_generic_error_exhausted_raises(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = _make_client()
        _attach(client, error=RuntimeError("boom"))
        with pytest.raises(Exception, match="boom"):
            asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))


class TestHealthCheckAndClose:
    def test_health_check_ok(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, data=OPENAI_OK)])
        assert asyncio.run(client.health_check()) is True

    def test_health_check_empty_false(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, data={"choices": [{"message": {"content": ""}}]})])
        assert asyncio.run(client.health_check()) is False

    def test_health_check_failure(self):
        client = _make_client()
        _attach(client, error=RuntimeError("down"))
        assert asyncio.run(client.health_check()) is False

    def test_close(self):
        client = _make_client()
        s = _attach(client)
        asyncio.run(client.close())
        assert s.closed is True
