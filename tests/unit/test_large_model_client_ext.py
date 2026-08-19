"""LargeModelClient 补充测试 — generate/chat/chat_stream/_messages_to_api/_parse_chat_response 等路径"""
import asyncio
import json
import ssl
from unittest.mock import AsyncMock, MagicMock

import pytest

from config.settings import settings
from infra.model.base_model import ChatMessage, ToolCall
from infra.model.large_model_client import LargeModelClient
from infra.tool_manager.service_registry import get_capability, register_capability, unregister_capability

DEFAULT_URL = "https://api.deepseek.com/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DASHSCOPE_URL = "https://dashscope.aliyuncs.com/api/v1"

OPENAI_OK = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}


class _MockResponse:
    def __init__(self, status=200, data=None, text=None, lines=None, headers=None):
        self.status = status
        self._data = data
        self._text = text
        self._lines = lines
        self.headers = headers or {"Content-Type": "application/json"}

    async def json(self):
        return self._data

    async def text(self):
        if self._text is not None:
            return self._text
        return json.dumps(self._data, ensure_ascii=False) if self._data is not None else ""

    @property
    def content(self):
        class _Stream:
            def __init__(self, lines):
                self._lines = lines

            def __aiter__(self):
                self._it = iter(self._lines)
                return self

            async def __anext__(self):
                try:
                    return next(self._it)
                except StopIteration:
                    raise StopAsyncIteration

        return _Stream(self._lines or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _TextFailResp(_MockResponse):
    """第一次 text() 返回非法 JSON，第二次抛错（覆盖 text 读取失败分支）"""

    def __init__(self):
        super().__init__(status=200)
        self._calls = 0

    async def text(self):
        self._calls += 1
        if self._calls == 1:
            return "not-json"
        raise RuntimeError("read failed")


class MockSession:
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


def _detect(url):
    u = (url or "").lower()
    if "anthropic" in u or "claude" in u:
        return "anthropic"
    if "dashscope" in u:
        return "dashscope"
    return "openai"


def _make_client(api_url=DEFAULT_URL, api_format=None, **kw):
    if api_format is None:
        api_format = _detect(api_url)
    return LargeModelClient(api_key="test-key", api_url=api_url, api_format=api_format, **kw)


def _attach(client, **kw):
    s = MockSession(**kw)
    client._session = s
    return s


def _no_retries(client):
    client.timeout = 5
    return client


# ---------------------------------------------------------------------------
# __init__ / _detect_api_format / from_config
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_init_defaults_openai(self):
        client = _make_client()
        assert client.api_key == "test-key"
        assert client._api_format == "openai"
        assert client._chat_url == DEFAULT_URL
        assert client.supports_native_tools is True
        assert client.max_tokens == 4096
        assert client.temperature == 0.7

    def test_init_anthropic(self):
        client = _make_client(ANTHROPIC_URL)
        assert client._api_format == "anthropic"
        assert client._chat_url == ANTHROPIC_URL

    def test_init_dashscope(self):
        client = _make_client(DASHSCOPE_URL)
        assert client._api_format == "dashscope"
        assert client._chat_url == DASHSCOPE_URL

    def test_init_explicit_format_wins(self):
        client = _make_client(DASHSCOPE_URL, api_format="openai")
        assert client._api_format == "openai"

    def test_init_falls_back_to_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "LARGE_MODEL_API_KEY", "cfg-key")
        monkeypatch.setattr(settings, "LARGE_MODEL_API_URL", "http://cfg/v1/chat/completions")
        monkeypatch.setattr(settings, "LARGE_MODEL_NAME", "deepseek-v4-flash")
        monkeypatch.setattr(settings, "LARGE_MODEL_API_FORMAT", "")
        client = LargeModelClient()
        assert client.api_key == "cfg-key"
        assert client.api_url == "http://cfg/v1/chat/completions"
        assert client._api_format == "openai"
        assert client._chat_url == "http://cfg/v1/chat/completions"

    def test_from_config(self, monkeypatch):
        monkeypatch.setattr(settings, "LARGE_MODEL_API_KEY", "c")
        monkeypatch.setattr(settings, "LARGE_MODEL_API_URL", "http://c/v1/chat/completions")
        client = LargeModelClient.from_config()
        assert isinstance(client, LargeModelClient)
        assert client.api_key == "c"

    def test_detect_api_format(self):
        assert LargeModelClient._detect_api_format("") == "openai"
        assert LargeModelClient._detect_api_format("https://dashscope.aliyuncs.com") == "dashscope"
        assert LargeModelClient._detect_api_format("https://api.anthropic.com/v1") == "anthropic"
        assert LargeModelClient._detect_api_format("https://api.claude.ai") == "anthropic"
        assert LargeModelClient._detect_api_format("https://api.openai.com/v1/chat/completions") == "openai"
        assert LargeModelClient._detect_api_format("https://x/v1/completions") == "openai"
        assert LargeModelClient._detect_api_format("https://x/other") == "openai"
        assert LargeModelClient._detect_api_format("https://openrouter.ai/api/v1") == "openai"
        assert LargeModelClient._detect_api_format("https://api.groq.com/openai/v1") == "openai"


# ---------------------------------------------------------------------------
# generate()
# ---------------------------------------------------------------------------

class TestGenerate:
    def test_requires_system_prompt(self):
        client = _make_client()
        with pytest.raises(TypeError):
            asyncio.run(client.generate("hi"))
        with pytest.raises(TypeError):
            asyncio.run(client.generate("hi", system_prompt=""))

    def test_success(self):
        client = _make_client()
        session = _attach(client, responses=[_MockResponse(status=200, data=OPENAI_OK)])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))
        assert out == "ok"
        body = session.captured[0]["json"]
        assert body["messages"][0]["content"] == "sys"
        assert body["messages"][1]["content"] == "hi"

    def test_reasoning_not_used_by_default(self):
        """默认：content 为空时不返回思维链（思考过程≠正式输出，§51）"""
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, data={
            "choices": [{"message": {"role": "assistant", "content": "", "reasoning_content": "think"}}],
        })])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))
        assert out == ""

    def test_reasoning_fallback_explicit_true(self):
        """显式 fallback_to_reasoning=True 才用思维链兜底"""
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, data={
            "choices": [{"message": {"role": "assistant", "content": "", "reasoning_content": "think"}}],
        })])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1,
                                          fallback_to_reasoning=True))
        assert out == "think"

    def test_anthropic_joins_text(self):
        client = _make_client(ANTHROPIC_URL)
        _attach(client, responses=[_MockResponse(status=200, data={
            "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
        })])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))
        assert out == "a\nb"

    def test_anthropic_no_text(self):
        client = _make_client(ANTHROPIC_URL)
        _attach(client, responses=[_MockResponse(status=200, data={
            "content": [{"type": "image", "image": "x"}],
        })])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))
        assert out == ""

    def test_anthropic_no_content_blocks(self):
        client = _make_client(ANTHROPIC_URL)
        _attach(client, responses=[_MockResponse(status=200, data={"content": []})])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))
        assert out == ""

    def test_reasoning_content_present_but_empty(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, data={
            "choices": [{"message": {"role": "assistant", "content": "", "reasoning_content": ""}}],
        })])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))
        assert out == ""

    def test_empty_choices_returns_empty(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, data={})])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))
        assert out == ""

    def test_json_decode_html_fallback_returns_empty(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, text="<html>not json</html>")])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))
        assert out == ""

    def test_json_decode_plain_text_returns_empty(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, text="plain response")])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))
        assert out == ""

    def test_text_read_failure_returns_empty(self):
        client = _make_client()
        _attach(client, responses=[_TextFailResp()])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))
        assert out == ""

    def test_non_200_raises_and_reports(self, monkeypatch):
        reporter = MagicMock()
        monkeypatch.setattr("infra.model.large_model_client.report_api_error", reporter)
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=500, text="boom")])
        with pytest.raises(Exception, match="API request failed: 500"):
            asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))
        assert reporter.call_count == 1

    def test_non_200_json_body(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=500, text='{"error": "quota"}')])
        with pytest.raises(Exception, match="quota"):
            asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))

    def test_timeout_retry_then_success(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = _make_client()
        _attach(client, errors=[asyncio.TimeoutError()], responses=[_MockResponse(status=200, data=OPENAI_OK)])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=2))
        assert out == "ok"

    def test_timeout_exhausted_raises(self):
        client = _make_client()
        _attach(client, error=asyncio.TimeoutError())
        with pytest.raises(Exception, match="Large model request timeout"):
            asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))

    def test_connector_error_retry_resets_session(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = _make_client()
        s = MockSession(errors=[ssl.SSLError("record layer")], responses=[_MockResponse(status=200, data=OPENAI_OK)])
        client._session = s
        client._get_session = AsyncMock(return_value=s)
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=2))
        assert out == "ok"

    def test_connector_error_exhausted_raises(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = _make_client()
        s = MockSession(error=ssl.SSLError("record layer"))
        client._session = s
        client._get_session = AsyncMock(return_value=s)
        with pytest.raises(Exception, match="record layer"):
            asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))

    def test_generic_error_retry_then_success(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = _make_client()
        _attach(client, errors=[RuntimeError("boom")], responses=[_MockResponse(status=200, data=OPENAI_OK)])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=2))
        assert out == "ok"

    def test_503_busy_backoff(self, monkeypatch):
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)
        client = _make_client()
        _attach(client, errors=[RuntimeError("503 Service Unavailable")],
                responses=[_MockResponse(status=200, data=OPENAI_OK)])
        out = asyncio.run(client.generate("hi", system_prompt="sys", max_retries=2))
        assert out == "ok"
        assert sleep_mock.call_args_list[0][0][0] == 5

    def test_generic_error_exhausted_raises(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = _make_client()
        _attach(client, error=RuntimeError("boom"))
        with pytest.raises(Exception, match="boom"):
            asyncio.run(client.generate("hi", system_prompt="sys", max_retries=1))


# ---------------------------------------------------------------------------
# chat()
# ---------------------------------------------------------------------------

class TestChat:
    def test_success_with_reasoning(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, data={
            "choices": [{"message": {"content": "answer", "reasoning_content": "r"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
        })])
        resp = asyncio.run(client.chat([ChatMessage(role="user", content="hi")], max_retries=1))
        assert resp.message.content == "answer"
        assert resp.message.reasoning_content == "r"
        assert resp.usage == {"prompt_tokens": 1, "completion_tokens": 2}

    def test_model_override_updates_provider(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, data=OPENAI_OK)])
        asyncio.run(client.chat([ChatMessage(role="user", content="hi")], model="other", max_retries=1))
        assert client._provider.model_name == "other"

    def test_non_200_raises_and_reports(self, monkeypatch):
        reporter = MagicMock()
        monkeypatch.setattr("infra.model.large_model_client.report_api_error", reporter)
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=500, data={"error": "x"})])
        with pytest.raises(Exception, match="API request failed: 500"):
            asyncio.run(client.chat([ChatMessage(role="user", content="hi")], max_retries=1))
        assert reporter.call_count == 1

    def test_timeout_exhausted_reports(self, monkeypatch):
        reporter = MagicMock()
        monkeypatch.setattr("infra.model.large_model_client.report_exception", reporter)
        client = _make_client()
        _attach(client, error=asyncio.TimeoutError())
        with pytest.raises(Exception, match="Chat request timeout"):
            asyncio.run(client.chat([ChatMessage(role="user", content="hi")], max_retries=1))
        assert reporter.call_count == 1

    def test_timeout_retry_then_success(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = _make_client()
        _attach(client, errors=[asyncio.TimeoutError()],
                responses=[_MockResponse(status=200, data=OPENAI_OK)])
        resp = asyncio.run(client.chat([ChatMessage(role="user", content="hi")], max_retries=2))
        assert resp.message.content == "ok"

    def test_generic_error_retry_then_success(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = _make_client()
        _attach(client, errors=[RuntimeError("boom")], responses=[_MockResponse(status=200, data=OPENAI_OK)])
        resp = asyncio.run(client.chat([ChatMessage(role="user", content="hi")], max_retries=2))
        assert resp.message.content == "ok"

    def test_503_busy_backoff(self, monkeypatch):
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)
        client = _make_client()
        _attach(client, errors=[RuntimeError("503 busy")], responses=[_MockResponse(status=200, data=OPENAI_OK)])
        resp = asyncio.run(client.chat([ChatMessage(role="user", content="hi")], max_retries=2))
        assert resp.message.content == "ok"
        assert sleep_mock.call_args_list[0][0][0] == 5

    def test_exhausted_generic_raises(self, monkeypatch):
        reporter = MagicMock()
        monkeypatch.setattr("infra.model.large_model_client.report_exception", reporter)
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = _make_client()
        _attach(client, error=RuntimeError("boom"))
        with pytest.raises(Exception, match="boom"):
            asyncio.run(client.chat([ChatMessage(role="user", content="hi")], max_retries=1))
        assert reporter.call_count == 1


# ---------------------------------------------------------------------------
# chat_stream + SSE 解析
# ---------------------------------------------------------------------------

class TestChatStream:
    def test_openai_stream(self):
        lines = [
            b": keep-alive comment",
            b'data: not-json',
            b'data: {"choices":[{"delta":{"content":"Hel"}}]}',
            b'data: {"choices":[{"delta":{"content":"lo"}}]}',
            b'data: {"choices":[{"delta":{"reasoning_content":"think"}}]}',
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"f","arguments":"{\\"a\\":"}}]}}]}',
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"1}"}}]},"finish_reason":"tool_calls"}]}',
            b'data: [DONE]',
        ]
        client = _make_client()
        tokens = []
        _attach(client, responses=[_MockResponse(status=200, lines=lines)])
        resp = asyncio.run(client.chat_stream(
            [ChatMessage(role="user", content="hi")],
            on_token=tokens.append,
            max_retries=1,
        ))
        assert resp.message.content == "Hello"
        assert resp.message.reasoning_content == "think"
        assert resp.finish_reason == "tool_calls"
        assert tokens == ["Hel", "lo"]
        tc = resp.message.tool_calls[0]
        assert tc.id == "c1" and tc.name == "f"
        assert json.loads(tc.arguments) == {"a": 1}

    def test_anthropic_stream(self):
        lines = [
            b'event: content_block_start',
            b'data: {"type":"content_block_start","content_block":{"type":"tool_use","id":"t1","name":"f"}}',
            b'event: content_block_delta',
            b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hi"}}',
            b'event: content_block_delta',
            b'data: {"type":"content_block_delta","delta":{"type":"input_json_delta","partial_json":"{\\"a\\":"}}',
            b'data: {"type":"content_block_delta","delta":{"type":"input_json_delta","partial_json":"1}"}}',
            b'event: content_block_stop',
            b'data: {"type":"content_block_stop"}',
            b'event: message_delta',
            b'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}',
        ]
        client = _make_client(ANTHROPIC_URL)
        tokens = []
        _attach(client, responses=[_MockResponse(status=200, lines=lines)])
        resp = asyncio.run(client.chat_stream(
            [ChatMessage(role="user", content="hi")],
            on_token=tokens.append,
            max_retries=1,
        ))
        assert resp.message.content == "hi"
        assert tokens == ["hi"]
        assert resp.finish_reason == "tool_calls"
        assert resp.message.tool_calls[0].id == "t1"
        assert json.loads(resp.message.tool_calls[0].arguments) == {"a": 1}

    def test_anthropic_stream_max_tokens(self):
        lines = [
            b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"x"}}',
            b'data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"}}',
        ]
        client = _make_client(ANTHROPIC_URL)
        _attach(client, responses=[_MockResponse(status=200, lines=lines)])
        resp = asyncio.run(client.chat_stream([ChatMessage(role="user", content="hi")], max_retries=1))
        assert resp.finish_reason == "length"

    def test_dashscope_stream_incremental(self):
        lines = [
            b'data: ',
            b'data: {"output":{"text":"ab"}}',
            b'data: {"output":{"text":"abcd"}}',
            b'data: {"output":{"text":"abcde"}}',
        ]
        client = _make_client(DASHSCOPE_URL)
        tokens = []
        _attach(client, responses=[_MockResponse(status=200, lines=lines)])
        resp = asyncio.run(client.chat_stream([ChatMessage(role="user", content="hi")], on_token=tokens.append, max_retries=1))
        assert resp.message.content == "abcde"
        assert tokens == ["ab", "cd", "e"]

    def test_dashscope_stream_tool_calls(self):
        lines = [
            b'data: {"output":{"choices":[{"message":{"tool_calls":[{"id":"x","function":{"name":"f","arguments":"{}"}}]}}]}}',
        ]
        client = _make_client(DASHSCOPE_URL)
        _attach(client, responses=[_MockResponse(status=200, lines=lines)])
        resp = asyncio.run(client.chat_stream([ChatMessage(role="user", content="hi")], max_retries=1))
        assert resp.finish_reason == "tool_calls"
        assert resp.message.tool_calls[0].name == "f"

    def test_non_200_raises(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=401, text="unauthorized")])
        with pytest.raises(Exception, match="Stream API error 401"):
            asyncio.run(client.chat_stream([ChatMessage(role="user", content="hi")], max_retries=1))

    def test_retry_then_success(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        lines = [b'data: {"choices":[{"delta":{"content":"ok"}}]}', b'data: [DONE]']
        client = _make_client()
        _attach(client, errors=[RuntimeError("boom")], responses=[_MockResponse(status=200, lines=lines)])
        resp = asyncio.run(client.chat_stream([ChatMessage(role="user", content="hi")], max_retries=2))
        assert resp.message.content == "ok"

    def test_model_override_updates_provider(self):
        lines = [b'data: {"choices":[{"delta":{"content":"ok"}}]}', b'data: [DONE]']
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, lines=lines)])
        asyncio.run(client.chat_stream(
            [ChatMessage(role="user", content="hi")], model="other-model", max_retries=1))
        assert client._provider.model_name == "other-model"

    def test_503_busy_backoff(self, monkeypatch):
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)
        lines = [b'data: {"choices":[{"delta":{"content":"ok"}}]}', b'data: [DONE]']
        client = _make_client()
        _attach(client, errors=[RuntimeError("503 Service Busy")],
                responses=[_MockResponse(status=200, lines=lines)])
        resp = asyncio.run(client.chat_stream([ChatMessage(role="user", content="hi")], max_retries=2))
        assert resp.message.content == "ok"
        assert sleep_mock.call_args_list[0][0][0] == 5

    def test_zero_retries_raises(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, lines=[])])
        with pytest.raises(Exception, match="Stream chat failed after all retries"):
            asyncio.run(client.chat_stream([ChatMessage(role="user", content="hi")], max_retries=0))

    def test_parse_returns_none(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, lines=[b'data: [DONE]'])])
        client._parse_openai_stream = AsyncMock(return_value=None)
        result = asyncio.run(client.chat_stream([ChatMessage(role="user", content="hi")], max_retries=1))
        assert result is None

    def test_openai_stream_edge_lines(self):
        lines = [
            b"",
            b": comment",
            b"raw line",
            b'data: {"choices": []}',
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"f"}}]}}]}',
            b'data: {"choices":[{"delta":{"content":"done"}}]}',
        ]
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, lines=lines)])
        resp = asyncio.run(client.chat_stream([ChatMessage(role="user", content="hi")], max_retries=1))
        assert resp.message.content == "done"
        assert resp.finish_reason == "stop"

    def test_anthropic_stream_edge_lines(self):
        lines = [
            b"",
            b": comment",
            b"raw text",
            b'data: not-json',
            b'data: {"type":"content_block_start","content_block":{"type":"text","text":"hi"}}',
            b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":""}}',
            b'data: {"type":"content_block_delta","delta":{"type":"signature_delta","signature":"x"}}',
            b'data: {"type":"content_block_delta","delta":{"type":"input_json_delta","partial_json":"{}"}}',
            b'data: {"type":"content_block_stop"}',
            b'data: {"type":"message_start"}',
            b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
            b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"end"}}',
        ]
        client = _make_client(ANTHROPIC_URL)
        _attach(client, responses=[_MockResponse(status=200, lines=lines)])
        resp = asyncio.run(client.chat_stream([ChatMessage(role="user", content="hi")], max_retries=1))
        assert resp.message.content == "end"
        assert resp.finish_reason == "stop"

    def test_anthropic_stream_bad_tool_json(self):
        lines = [
            b'data: {"type":"content_block_start","content_block":{"type":"tool_use","id":"t1","name":"f"}}',
            b'data: {"type":"content_block_delta","delta":{"type":"input_json_delta","partial_json":"bad"}}',
            b'data: {"type":"content_block_stop"}',
        ]
        client = _make_client(ANTHROPIC_URL)
        _attach(client, responses=[_MockResponse(status=200, lines=lines)])
        resp = asyncio.run(client.chat_stream([ChatMessage(role="user", content="hi")], max_retries=1))
        assert json.loads(resp.message.tool_calls[0].arguments) == {}

    def test_dashscope_stream_edge_lines(self):
        lines = [
            b"",
            b": comment",
            b"raw line",
            b'data: not-json',
            b'data: {"output":{"text":"ab"}}',
            b'data: {"output":{"text":"ab"}}',
            b'data: {"output":{"text":"abcd"}}',
            b'data: {"output":{"choices":[{"message":{"content":"x"}}]}}',
        ]
        client = _make_client(DASHSCOPE_URL)
        _attach(client, responses=[_MockResponse(status=200, lines=lines)])
        resp = asyncio.run(client.chat_stream([ChatMessage(role="user", content="hi")], max_retries=1))
        assert resp.message.content == "abcd"

    def test_dashscope_stream_no_on_token(self):
        lines = [
            b'data: {"output":{"text":"ab"}}',
            b'data: {"output":{"text":"abcd"}}',
        ]
        client = _make_client(DASHSCOPE_URL)
        _attach(client, responses=[_MockResponse(status=200, lines=lines)])
        resp = asyncio.run(client.chat_stream([ChatMessage(role="user", content="hi")], max_retries=1))
        assert resp.message.content == "abcd"


# ---------------------------------------------------------------------------
# _messages_to_api / _parse_image_dataurl / _parse_chat_response
# ---------------------------------------------------------------------------

@pytest.fixture
def turn_images_cap():
    original = get_capability("turn_images")
    yield
    if original is None:
        unregister_capability("turn_images")
    else:
        register_capability("turn_images", original)


def _set_turn_images(provider):
    register_capability("turn_images", provider)


class TestMessagesToApi:
    def test_openai_basic(self):
        client = _make_client()
        msgs = client._messages_to_api([
            ChatMessage(role="system", content="s"),
            ChatMessage(role="assistant", content="a", reasoning_content="rc"),
        ])
        assert msgs[0] == {"role": "system", "content": "s"}
        assert msgs[1]["reasoning_content"] == "rc"

    def test_openai_tool_calls_and_tool_role(self):
        client = _make_client()
        msgs = client._messages_to_api([
            ChatMessage(role="assistant", content="",
                        tool_calls=[ToolCall(id="tc1", name="f", arguments="{}")]),
            ChatMessage(role="tool", content="res", tool_call_id="tc1"),
        ])
        assert msgs[0]["tool_calls"] == [
            {"type": "function", "function": {"name": "f", "arguments": "{}"}, "id": "tc1"}
        ]
        assert msgs[1]["tool_call_id"] == "tc1"

    def test_dashscope_tool_role_uses_name(self):
        client = _make_client(DASHSCOPE_URL)
        msgs = client._messages_to_api([ChatMessage(role="tool", content="r", name="f")])
        assert msgs[0]["name"] == "f"

    def test_anthropic_tool_result_and_tool_use(self):
        client = _make_client(ANTHROPIC_URL)
        msgs = client._messages_to_api([
            ChatMessage(role="tool", content="r", tool_call_id="t1"),
            ChatMessage(role="assistant", content="txt",
                        tool_calls=[ToolCall(id="u1", name="g", arguments='{"b":2}')]),
        ])
        assert msgs[0]["content"][0]["type"] == "tool_result"
        assert msgs[1]["content"][0] == {"type": "text", "text": "txt"}
        assert msgs[1]["content"][1] == {"type": "tool_use", "id": "u1", "name": "g", "input": {"b": 2}}

    def test_anthropic_tool_use_bad_json(self):
        client = _make_client(ANTHROPIC_URL)
        msgs = client._messages_to_api([
            ChatMessage(role="assistant", tool_calls=[ToolCall(id="u1", name="g", arguments="bad")]),
        ])
        assert msgs[0]["content"][0]["input"] == {}

    def test_turn_images_openai(self, turn_images_cap):
        img = "data:image/png;base64,QUJD"
        _set_turn_images(lambda: (lambda: [img], lambda: None))
        client = _make_client()
        msgs = client._messages_to_api([
            ChatMessage(role="user", content="look"),
        ])
        assert msgs[0]["content"] == [
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": img}},
        ]

    def test_turn_images_anthropic(self, turn_images_cap):
        img = "data:image/png;base64,QUJD"
        _set_turn_images(lambda: (lambda: [img], lambda: None))
        client = _make_client(ANTHROPIC_URL)
        msgs = client._messages_to_api([ChatMessage(role="user", content="look")])
        assert msgs[0]["content"][1]["type"] == "image"
        assert msgs[0]["content"][1]["source"]["media_type"] == "image/png"

    def test_turn_images_empty_skipped(self, turn_images_cap):
        _set_turn_images(lambda: (lambda: None, lambda: None))
        client = _make_client()
        msgs = client._messages_to_api([ChatMessage(role="user", content="look")])
        assert msgs[0] == {"role": "user", "content": "look"}

    def test_turn_images_factory_raises(self, turn_images_cap):
        _set_turn_images(lambda: (_ for _ in ()).throw(RuntimeError("x")))
        client = _make_client()
        msgs = client._messages_to_api([ChatMessage(role="user", content="look")])
        assert msgs[0] == {"role": "user", "content": "look"}

    def test_turn_images_unregistered(self, turn_images_cap):
        unregister_capability("turn_images")
        client = _make_client()
        msgs = client._messages_to_api([ChatMessage(role="user", content="look")])
        assert msgs[0] == {"role": "user", "content": "look"}

    def test_openai_tool_role_no_call_id(self):
        client = _make_client()
        msgs = client._messages_to_api([ChatMessage(role="tool", content="r")])
        assert msgs[0] == {"role": "tool", "content": "r"}

    def test_dashscope_tool_role_no_name(self):
        client = _make_client(DASHSCOPE_URL)
        msgs = client._messages_to_api([ChatMessage(role="tool", content="r")])
        assert msgs[0] == {"role": "tool", "content": "r"}

    def test_turn_images_skips_non_user(self, turn_images_cap):
        img = "data:image/png;base64,QUJD"
        _set_turn_images(lambda: (lambda: [img], lambda: None))
        client = _make_client()
        msgs = client._messages_to_api([
            ChatMessage(role="user", content="u1"),
            ChatMessage(role="assistant", content="a"),
        ])
        assert msgs[0]["content"] == [
            {"type": "text", "text": "u1"},
            {"type": "image_url", "image_url": {"url": img}},
        ]
        assert msgs[1] == {"role": "assistant", "content": "a"}

    def test_turn_images_skips_list_content(self, turn_images_cap):
        img = "data:image/png;base64,QUJD"
        _set_turn_images(lambda: (lambda: [img], lambda: None))
        client = _make_client(ANTHROPIC_URL)
        msgs = client._messages_to_api([
            ChatMessage(role="tool", content="r", tool_call_id="t1"),
        ])
        assert msgs[0]["content"][0]["type"] == "tool_result"
        assert len(msgs[0]["content"]) == 1

    def test_turn_images_anthropic_no_content(self, turn_images_cap):
        img = "data:image/png;base64,QUJD"
        _set_turn_images(lambda: (lambda: [img], lambda: None))
        client = _make_client(ANTHROPIC_URL)
        msgs = client._messages_to_api([ChatMessage(role="user")])
        assert msgs[0]["content"][0]["type"] == "image"
        assert msgs[0]["content"][0]["source"]["media_type"] == "image/png"

    def test_turn_images_openai_no_content(self, turn_images_cap):
        img = "data:image/png;base64,QUJD"
        _set_turn_images(lambda: (lambda: [img], lambda: None))
        client = _make_client()
        msgs = client._messages_to_api([ChatMessage(role="user")])
        assert msgs[0]["content"] == [{"type": "image_url", "image_url": {"url": img}}]


class TestParseImageDataurl:
    def test_data_url(self):
        assert LargeModelClient._parse_image_dataurl("data:image/png;base64,QUJD") == ("image/png", "QUJD")

    def test_data_url_empty_media(self):
        assert LargeModelClient._parse_image_dataurl("data:;base64,ABC") == ("image/jpeg", "ABC")

    def test_data_url_no_comma(self):
        assert LargeModelClient._parse_image_dataurl("data:image/png;base64") == ("image/jpeg", "data:image/png;base64")

    def test_plain(self):
        assert LargeModelClient._parse_image_dataurl("raw-b64") == ("image/jpeg", "raw-b64")

    def test_non_string(self):
        assert LargeModelClient._parse_image_dataurl(123) == ("image/jpeg", 123)


class TestParseChatResponse:
    def test_openai_format(self):
        client = _make_client()
        resp = client._parse_chat_response({
            "choices": [{"message": {"content": "c", "reasoning_content": "r",
                                       "tool_calls": [{"id": "x", "function": {"name": "f", "arguments": "{}"}}]},
                          "finish_reason": "tool_calls"}],
            "usage": {"a": 1},
        })
        assert resp.message.content == "c"
        assert resp.message.reasoning_content == "r"
        assert resp.message.tool_calls[0].id == "x"
        assert resp.finish_reason == "tool_calls"

    def test_anthropic_format(self):
        client = _make_client(ANTHROPIC_URL)
        resp = client._parse_chat_response({
            "content": [{"type": "tool_use", "id": "t", "name": "f", "input": {"x": 1}}],
            "stop_reason": "tool_use",
        })
        assert resp.message.tool_calls[0].name == "f"
        assert resp.finish_reason == "tool_calls"

    def test_dashscope_choices(self):
        client = _make_client(DASHSCOPE_URL)
        resp = client._parse_chat_response({
            "output": {"choices": [{
                "message": {"role": "assistant", "content": "c",
                            "tool_calls": [{"id": "1", "function": {"name": "f", "arguments": "{}"}}]},
                "finish_reason": "tool_calls",
            }]},
            "usage": {"total_tokens": 3},
        })
        assert resp.message.content == "c"
        assert resp.message.tool_calls[0].name == "f"
        assert resp.finish_reason == "tool_calls"
        assert resp.usage == {"total_tokens": 3}

    def test_dashscope_legacy_json_action(self):
        client = _make_client(DASHSCOPE_URL)
        tools = [{"function": {"name": "create_skill"}}]
        resp = client._parse_chat_response({"output": {"text": '{"action": "create_skill", "x": 1}'}}, tools=tools)
        assert resp.message.tool_calls[0].name == "create_skill"
        assert json.loads(resp.message.tool_calls[0].arguments)["x"] == 1
        assert resp.message.content is None

    def test_dashscope_legacy_json_role(self):
        client = _make_client(DASHSCOPE_URL)
        tools = [{"function": {"name": "find_definition"}}]
        resp = client._parse_chat_response({"output": {"text": '{"role": "find_definition", "n": 2}'}}, tools=tools)
        assert resp.message.tool_calls[0].name == "find_definition"

    def test_dashscope_legacy_json_no_match(self):
        client = _make_client(DASHSCOPE_URL)
        tools = [{"function": {"name": "other"}}]
        resp = client._parse_chat_response({"output": {"text": '{"action": "create_skill"}'}}, tools=tools)
        assert resp.message.tool_calls is None
        assert resp.message.content == '{"action": "create_skill"}'

    def test_dashscope_legacy_function_format(self):
        client = _make_client(DASHSCOPE_URL)
        tools = [{"function": {"name": "add"}}]
        resp = client._parse_chat_response(
            {"output": {"text": 'add(a="1", b=2, c={"k": "v"})'}}, tools=tools)
        assert resp.message.tool_calls[0].name == "add"
        args = json.loads(resp.message.tool_calls[0].arguments)
        assert args == {"a": "1", "b": "2", "c": '{"k": "v"}'}

    def test_dashscope_legacy_function_no_match(self):
        client = _make_client(DASHSCOPE_URL)
        tools = [{"function": {"name": "other"}}]
        resp = client._parse_chat_response({"output": {"text": "add(a=1)"}}, tools=tools)
        assert resp.message.tool_calls is None
        assert resp.message.content == "add(a=1)"

    def test_dashscope_legacy_plain_text(self):
        client = _make_client(DASHSCOPE_URL)
        resp = client._parse_chat_response({"output": {"text": "hello world"}})
        assert resp.message.content == "hello world"
        assert resp.message.tool_calls is None

    def test_dashscope_choices_no_tool_calls(self):
        client = _make_client(DASHSCOPE_URL)
        resp = client._parse_chat_response({
            "output": {"choices": [{"message": {"role": "assistant", "content": "c"},
                                    "finish_reason": "stop"}]},
        })
        assert resp.message.content == "c"
        assert resp.message.tool_calls is None

    def test_dashscope_legacy_json_no_action_role(self):
        client = _make_client(DASHSCOPE_URL)
        tools = [{"function": {"name": "add"}}]
        resp = client._parse_chat_response({"output": {"text": '{"foo": 1}'}}, tools=tools)
        assert resp.message.tool_calls is None
        assert resp.message.content == '{"foo": 1}'

    def test_dashscope_legacy_plain_with_tools(self):
        client = _make_client(DASHSCOPE_URL)
        tools = [{"function": {"name": "add"}}]
        resp = client._parse_chat_response({"output": {"text": "hello"}}, tools=tools)
        assert resp.message.content == "hello"
        assert resp.message.tool_calls is None

    def test_dashscope_legacy_parens_no_match(self):
        client = _make_client(DASHSCOPE_URL)
        tools = [{"function": {"name": "add"}}]
        resp = client._parse_chat_response({"output": {"text": "!!! (x)"}}, tools=tools)
        assert resp.message.tool_calls is None
        assert resp.message.content == "!!! (x)"

    def test_dashscope_legacy_invalid_json(self):
        client = _make_client(DASHSCOPE_URL)
        tools = [{"function": {"name": "add"}}]
        resp = client._parse_chat_response({"output": {"text": "{oops"}}, tools=tools)
        assert resp.message.tool_calls is None
        assert resp.message.content == "{oops"


# ---------------------------------------------------------------------------
# generate_stream / health_check
# ---------------------------------------------------------------------------

class TestGenerateStream:
    def test_yields_text(self):
        lines = [
            b'data: {"output": {"text": "chunk1"}}',
            b'data: [DONE]',
            b'data: {"output": {"text": "chunk2"}}',
        ]
        client = _make_client(DASHSCOPE_URL)
        _attach(client, responses=[_MockResponse(status=200, lines=lines)])

        async def go():
            out = []
            async for chunk in client.generate_stream("hi"):
                out.append(chunk)
            return out

        assert asyncio.run(go()) == ["chunk1", "chunk2"]

    def test_bad_json_skipped(self):
        lines = [b'data: not-json', b'data: {"output": {"text": "ok"}}']
        client = _make_client(DASHSCOPE_URL)
        _attach(client, responses=[_MockResponse(status=200, lines=lines)])

        async def go():
            out = []
            async for chunk in client.generate_stream("hi"):
                out.append(chunk)
            return out

        assert asyncio.run(go()) == ["ok"]

    def test_edge_lines_skipped(self):
        lines = [
            b"",
            b"event: ping",
            b'data: {"output": {"text": ""}}',
            b'data: {"output": {"text": "ok"}}',
        ]
        client = _make_client(DASHSCOPE_URL)
        _attach(client, responses=[_MockResponse(status=200, lines=lines)])

        async def go():
            out = []
            async for chunk in client.generate_stream("hi"):
                out.append(chunk)
            return out

        assert asyncio.run(go()) == ["ok"]

    def test_timeout_raises(self):
        client = _make_client()
        _attach(client, error=asyncio.TimeoutError())
        with pytest.raises(Exception, match="stream timeout"):
            asyncio.run(client.generate_stream("hi").__anext__())


class TestHealthCheck:
    def test_ok(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, data=OPENAI_OK)])
        assert asyncio.run(client.health_check()) is True

    def test_failure(self):
        client = _make_client()
        _attach(client, error=RuntimeError("down"))
        assert asyncio.run(client.health_check()) is False

    def test_empty_response_false(self):
        client = _make_client()
        _attach(client, responses=[_MockResponse(status=200, data={"choices": [{"message": {"content": ""}}]})])
        assert asyncio.run(client.health_check()) is False
