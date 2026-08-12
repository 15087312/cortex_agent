"""模型客户端 chat/generate/chat_stream 完整路径测试（此前仅 dataclass）"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from infra.model.large_model_client import LargeModelClient
from infra.model.base_model import ChatMessage, ToolCall


class _FakeResp:
    status = 200
    headers = {"content-type": "application/json"}

    def __init__(self, data=None, status=200, text_body=None):
        self._data = data or {}
        self.status = status
        self._text = text_body

    async def json(self):
        return self._data

    async def text(self):
        import json
        return self._text if self._text is not None else json.dumps(self._data, ensure_ascii=False)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.post_calls = []

    def post(self, url, **kw):
        self.post_calls.append((url, kw))
        r = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        if isinstance(r, Exception):
            raise r
        return r

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _make_client():
    c = LargeModelClient.__new__(LargeModelClient)
    c._api_format = "openai"
    c.max_tokens = 100
    c.temperature = 0.5
    c.model_name = "m"
    c.api_key = "k"
    c.api_url = "https://api.deepseek.com/v1"
    from config.providers.registry import get_provider
    c._provider = get_provider("m", "k", "https://api.deepseek.com/v1", "openai")
    c._chat_url = c._provider.chat_url()
    c.timeout = 30
    c.logger = MagicMock()
    c._session = None
    c._request_count = 0
    c._last_request_time = None
    c._total_tokens_used = 0
    return c


def _openai_resp(content="你好", reasoning="思考", usage=None):
    return {"choices": [{"message": {"content": content, "reasoning_content": reasoning}, "finish_reason": "stop"}],
            "usage": usage or {"prompt_tokens": 5, "completion_tokens": 3}}


# ── chat：成功 / 错误 / 超时重试 ────────────────────────────────────────────

def test_chat_success_openai():
    c = _make_client()
    c._get_session = AsyncMock(return_value=_FakeSession(_FakeResp(data=_openai_resp())))
    resp = asyncio.run(c.chat([ChatMessage(role="user", content="hi")]))
    assert resp.message.content == "你好"
    assert resp.message.reasoning_content == "思考"
    assert resp.finish_reason == "stop"


def test_chat_with_tools():
    c = _make_client()
    c._get_session = AsyncMock(return_value=_FakeSession(_FakeResp(data={
        "choices": [{"message": {"content": None, "tool_calls": [{"id": "c1", "function": {"name": "calc", "arguments": "{\"a\":1}"}}]}, "finish_reason": "tool_calls"}]
    })))
    resp = asyncio.run(c.chat([ChatMessage(role="user", content="算")], tools=[{"function": {"name": "calc"}}]))
    assert resp.message.tool_calls == [ToolCall(id="c1", name="calc", arguments='{"a":1}')]
    assert resp.finish_reason == "tool_calls"


def test_chat_non_200_raises():
    c = _make_client()
    c._get_session = AsyncMock(return_value=_FakeSession(_FakeResp(data={}, status=500, text_body="err")))
    with pytest.raises(Exception):
        asyncio.run(c.chat([ChatMessage(role="user", content="hi")]))


def test_chat_timeout_then_retry():
    c = _make_client()
    s = _FakeSession(asyncio.TimeoutError("timeout"), _FakeResp(data=_openai_resp()))
    c._get_session = AsyncMock(return_value=s)
    resp = asyncio.run(c.chat([ChatMessage(role="user", content="hi")], max_retries=2))
    assert resp.message.content == "你好"


def test_chat_url_normalized():
    c = _make_client()
    assert c._chat_url == "https://api.deepseek.com/v1/chat/completions"


# ── generate：成功 / reasoning 回退 / anthropic ─────────────────────────────

def test_generate_success():
    c = _make_client()
    c._get_session = AsyncMock(return_value=_FakeSession(_FakeResp(data=_openai_resp())))
    out = asyncio.run(c.generate("hi", system_prompt="你", max_retries=1))
    assert out == "你好"


def test_generate_reasoning_fallback():
    c = _make_client()
    data = {"choices": [{"message": {"content": "", "reasoning_content": "推理内容"}, "finish_reason": "stop"}]}
    c._get_session = AsyncMock(return_value=_FakeSession(_FakeResp(data=data)))
    out = asyncio.run(c.generate("hi", system_prompt="你", max_retries=1))
    assert out == "推理内容"


def test_generate_anthropic_format():
    c = _make_client()
    c._api_format = "anthropic"
    from config.providers.registry import get_provider
    c._provider = get_provider("m", "k", "https://api.anthropic.com/v1", "anthropic")
    c._chat_url = c._provider.chat_url()
    data = {"content": [{"type": "text", "text": "claude 回答"}]}
    c._get_session = AsyncMock(return_value=_FakeSession(_FakeResp(data=data)))
    out = asyncio.run(c.generate("hi", system_prompt="你", max_retries=1))
    assert out == "claude 回答"


def test_generate_requires_system_prompt():
    c = _make_client()
    with pytest.raises(TypeError):
        asyncio.run(c.generate("hi"))
