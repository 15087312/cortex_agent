"""LargeModelClient 流式解析测试（此前零覆盖：_parse_openai/anthropic/dashscope_stream）"""
import json

import pytest

from infra.model.large_model_client import LargeModelClient
from infra.model.base_model import ChatMessage, ToolCall


class _FakeContent:
    """模拟 aiohttp response.content（async 逐行字节）"""

    def __init__(self, lines):
        self._lines = [l if isinstance(l, bytes) else l.encode() for l in lines]
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._lines):
            raise StopAsyncIteration
        l = self._lines[self._i]
        self._i += 1
        return l


class _FakeResponse:
    def __init__(self, lines):
        self.content = _FakeContent(lines)


def _client():
    c = LargeModelClient.__new__(LargeModelClient)
    c._api_format = "openai"
    return c


def _run(coro):
    import asyncio
    return asyncio.run(coro)


# ── OpenAI 流 ───────────────────────────────────────────────────────────────

def test_openai_stream_text_and_reasoning():
    c = _client()
    tokens = []
    resp = _FakeResponse([
        'data: {"choices":[{"delta":{"content":"你","reasoning_content":"想"}}]}',
        'data: {"choices":[{"delta":{"content":"好"}}]}',
        'data: [DONE]',
        'data: {"choices":[{"delta":{"content":"被忽略"}}]}',
    ])
    r = _run(c._parse_openai_stream(resp, tokens.append))
    assert r.message.content == "你好"
    assert r.message.reasoning_content == "想"
    assert tokens == ["你", "好"]


def test_openai_stream_tool_calls_accumulate():
    c = _client()
    resp = _FakeResponse([
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"calc","arguments":"{\\"a\\":"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"1}"}}]},"finish_reason":"tool_calls"}]}',
        'data: [DONE]',
    ])
    r = _run(c._parse_openai_stream(resp, None))
    assert r.finish_reason == "tool_calls"
    assert r.message.tool_calls == [ToolCall(id="c1", name="calc", arguments='{"a":1}')]


def test_openai_stream_finish_reason_length():
    c = _client()
    resp = _FakeResponse([
        'data: {"choices":[{"delta":{"content":"x"},"finish_reason":"length"}]}',
        'data: [DONE]',
    ])
    r = _run(c._parse_openai_stream(resp, None))
    assert r.finish_reason == "length"


def test_openai_stream_skips_bad_json():
    c = _client()
    resp = _FakeResponse([
        'data: not-json',
        'data: {"choices":[{"delta":{"content":"好"}}]}',
        'data: [DONE]',
    ])
    r = _run(c._parse_openai_stream(resp, None))
    assert r.message.content == "好"


# ── Anthropic 流 ────────────────────────────────────────────────────────────

def test_anthropic_stream_text_and_stop():
    c = _client()
    c._api_format = "anthropic"
    resp = _FakeResponse([
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"你"}}',
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"好"}}',
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
    ])
    r = _run(c._parse_anthropic_stream(resp, None))
    assert r.message.content == "你好"
    assert r.finish_reason == "stop"  # anthropic end_turn → 客户端映射为 stop


def test_anthropic_stream_ignores_non_text_delta():
    c = _client()
    c._api_format = "anthropic"
    resp = _FakeResponse([
        'data: {"type":"content_block_delta","delta":{"type":"input_json_delta","partial_json":"{}"}}',
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
    ])
    r = _run(c._parse_anthropic_stream(resp, None))
    assert r.message.content is None


# ── DashScope 流 ────────────────────────────────────────────────────────────

def test_dashscope_stream_text():
    c = _client()
    c._api_format = "dashscope"
    # DashScope SSE 每次发送累积文本，解析器取增量
    resp = _FakeResponse([
        'data: {"output":{"text":"你好"}}',
        'data: {"output":{"text":"你好世界"}}',
    ])
    r = _run(c._parse_dashscope_stream(resp, None))
    assert r.message.content == "你好世界"
