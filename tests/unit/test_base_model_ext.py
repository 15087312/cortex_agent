"""base_model 补充测试 — 数据类 + BaseModelClient 基类完整路径覆盖"""
import asyncio
import json
import ssl
import sys
from unittest.mock import MagicMock

import aiohttp
import pytest

from infra.model.base_model import BaseModelClient, ChatMessage, ChatResponse, ToolCall


class _Concrete(BaseModelClient):
    """最小可实例化子类（实现抽象方法）"""

    def __init__(self, *args, result="gen-result", **kwargs):
        super().__init__(*args, **kwargs)
        self._result = result

    async def generate(self, prompt: str, **kwargs) -> str:
        return self._result

    async def health_check(self) -> bool:
        return True


def _client(**kw):
    kw.setdefault("api_key", "k")
    kw.setdefault("api_url", "http://u")
    return _Concrete(**kw)


async def _noop_coro():
    return None


class TestChatResponse:
    def test_defaults(self):
        r = ChatResponse(message=ChatMessage(role="assistant"))
        assert r.finish_reason == "stop"
        assert r.usage is None

    def test_fields(self):
        msg = ChatMessage(role="assistant", content="hi")
        r = ChatResponse(message=msg, finish_reason="length", usage={"a": 1})
        assert r.message is msg
        assert r.finish_reason == "length"
        assert r.usage == {"a": 1}

    def test_chat_message_defaults(self):
        m = ChatMessage(role="user")
        assert m.content is None
        assert m.tool_calls is None
        assert m.name is None
        assert m.tool_call_id is None
        assert m.reasoning_content is None


def _bare_ctor(**kw):
    """绕过 ABC 实例化限制，直接运行 BaseModelClient.__init__ 验证逻辑"""
    obj = _Concrete.__new__(_Concrete)
    BaseModelClient.__init__(obj, **kw)
    return obj


class TestConstructor:
    def test_requires_api_key(self):
        with pytest.raises(ValueError):
            _bare_ctor(api_key="", api_url="http://u")

    def test_requires_api_url(self):
        with pytest.raises(ValueError):
            _bare_ctor(api_key="k", api_url="")

    def test_allow_empty_skips_validation(self):
        c = _bare_ctor(api_key="", api_url="", allow_empty=True)
        assert c.api_key == ""
        assert c.api_url == ""
        assert c.timeout == 30
        assert c._session is None
        assert c.supports_native_tools is False

    def test_init_state(self):
        c = _client()
        assert c.timeout == 30
        assert c._request_count == 0
        assert c._total_tokens_used == 0
        assert c._last_request_time is None


class TestAbstractFallbacks:
    def test_abstract_generate_body(self):
        c = _client()
        assert asyncio.run(BaseModelClient.generate(c, "hi")) is None

    def test_abstract_health_check_body(self):
        c = _client()
        assert asyncio.run(BaseModelClient.health_check(c)) is None

    def test_chat_falls_back_to_generate(self):
        c = _client(result="fallback")
        resp = asyncio.run(c.chat(
            [ChatMessage(role="user", content="你好")],
            system_prompt="sys",
        ))
        assert resp.message.content == "fallback"
        assert resp.message.role == "assistant"

    def test_chat_extracts_system_from_messages(self):
        c = _client(result="ok")
        resp = asyncio.run(c.chat([
            ChatMessage(role="system", content="你是助手"),
            ChatMessage(role="user", content="hi"),
        ]))
        assert resp.message.content == "ok"

    def test_chat_missing_system_raises(self):
        c = _client()
        with pytest.raises(TypeError, match="system_prompt"):
            asyncio.run(c.chat([ChatMessage(role="user", content="hi")]))

    def test_chat_picks_last_user_content(self):
        c = _client()
        calls = {}

        async def fake_gen(prompt, **kw):
            calls["prompt"] = prompt
            return "x"

        c.generate = fake_gen
        asyncio.run(c.chat([
            ChatMessage(role="user", content="first"),
            ChatMessage(role="assistant", content="mid"),
            ChatMessage(role="user", content="last"),
        ], system_prompt="s"))
        assert calls["prompt"] == "last"

    def test_chat_stream_calls_on_token(self):
        c = _client(result="hello stream")
        tokens = []
        resp = asyncio.run(c.chat_stream(
            [ChatMessage(role="user", content="hi")],
            system_prompt="s",
            on_token=tokens.append,
        ))
        assert tokens == ["hello stream"]
        assert resp.message.content == "hello stream"

    def test_chat_stream_no_on_token(self):
        c = _client(result="x")
        resp = asyncio.run(c.chat_stream(
            [ChatMessage(role="user", content="hi")],
            system_prompt="s",
        ))
        assert resp.message.content == "x"

    def test_chat_stream_content_none_skips_callback(self):
        c = _client(result=None)
        called = []
        resp = asyncio.run(c.chat_stream(
            [ChatMessage(role="user", content="hi")],
            system_prompt="s",
            on_token=lambda t: called.append(t),
        ))
        assert resp.message.content is None
        assert called == []


class TestSslContext:
    def test_creates_ssl_context(self):
        ctx = BaseModelClient._create_ssl_context()
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2
        assert ctx.maximum_version == ssl.TLSVersion.TLSv1_3
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True

    def test_certifi_missing_falls_back_default_certs(self, monkeypatch):
        called = []
        monkeypatch.setattr(ssl.SSLContext, "load_default_certs",
                            lambda self, *a, **k: called.append(1))
        fake_certifi = MagicMock()
        fake_certifi.where.side_effect = RuntimeError("no certifi")
        monkeypatch.setitem(sys.modules, "certifi", fake_certifi)
        ctx = BaseModelClient._create_ssl_context()
        assert isinstance(ctx, ssl.SSLContext)
        assert called

    def test_set_ciphers_failure_ignored(self, monkeypatch):
        def boom(self, *a, **k):
            raise ssl.SSLError("no ciphers")

        monkeypatch.setattr(ssl.SSLContext, "set_ciphers", boom)
        ctx = BaseModelClient._create_ssl_context()
        assert isinstance(ctx, ssl.SSLContext)


class TestSession:
    def test_get_session_creates_and_reuses(self):
        c = _client()

        async def go():
            s1 = await c._get_session()
            assert isinstance(s1, aiohttp.ClientSession)
            s2 = await c._get_session()
            assert s2 is s1
            await c.close()

        asyncio.run(go())

    def test_get_session_recreates_when_closed(self):
        c = _client()
        old = MagicMock()
        old.closed = True
        c._session = old

        async def go():
            s = await c._get_session()
            assert s is not old
            await c.close()

        asyncio.run(go())

    def test_reset_session_none(self):
        c = _client()
        c._reset_session()
        assert c._session is None

    def test_reset_session_closes_open(self):
        c = _client()
        session = MagicMock()
        session.closed = False

        async def close():
            session.closed = True

        session.close = close
        c._session = session

        async def go():
            c._reset_session()
            await asyncio.sleep(0)

        asyncio.run(go())
        assert c._session is None

    def test_reset_session_close_error_ignored(self):
        c = _client()
        session = MagicMock()
        session.closed = False
        session.close = MagicMock(return_value=None)  # 非 awaitable → ensure_future 抛错
        c._session = session
        c._reset_session()
        assert c._session is None

    def test_reset_session_closed_session_no_close(self):
        c = _client()
        session = MagicMock()
        session.closed = True
        c._session = session
        c._reset_session()
        assert c._session is None

    def test_close_none(self):
        c = _client()
        asyncio.run(c.close())

    def test_close_closes_session(self):
        c = _client()
        closed = []

        class FakeSess:
            closed = False

            async def close(self):
                closed.append(1)
                self.closed = True

        c._session = FakeSess()
        asyncio.run(c.close())
        assert closed
        assert c._session is None


class TestApiFormat:
    def test_detect_openai_default(self):
        assert BaseModelClient.detect_api_format("") == "openai"
        assert BaseModelClient.detect_api_format("https://example.com/x") == "openai"
        assert BaseModelClient.detect_api_format("https://api.openai.com/v1/chat/completions") == "openai"
        assert BaseModelClient.detect_api_format("https://x/v1/completions") == "openai"

    def test_detect_dashscope(self):
        assert BaseModelClient.detect_api_format("https://dashscope.aliyuncs.com/api") == "dashscope"

    def test_detect_anthropic(self):
        assert BaseModelClient.detect_api_format("https://api.anthropic.com/v1/messages") == "anthropic"
        assert BaseModelClient.detect_api_format("https://api.claude.ai/v1") == "anthropic"

    def test_build_headers_anthropic(self):
        h = _client()._build_headers("anthropic")
        assert h["x-api-key"] == "k"
        assert h["anthropic-version"] == "2023-06-01"
        assert h["content-type"] == "application/json"

    def test_build_headers_bearer(self):
        h = _client()._build_headers("openai")
        assert h["Authorization"] == "Bearer k"
        assert h["Content-Type"] == "application/json"


class TestMessagesToAnthropic:
    def test_system_and_plain(self):
        sys_text, msgs = _client()._messages_to_anthropic([
            ChatMessage(role="system", content="s"),
            ChatMessage(role="user", content="u"),
            ChatMessage(role="assistant", content="a", name="x"),
        ])
        assert sys_text == "s"
        assert msgs == [{"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}]

    def test_tool_role(self):
        _, msgs = _client()._messages_to_anthropic([
            ChatMessage(role="tool", content="res", tool_call_id="t1"),
        ])
        assert msgs == [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "res"}]}]

    def test_assistant_tool_calls(self):
        _, msgs = _client()._messages_to_anthropic([
            ChatMessage(role="assistant", content="txt",
                        tool_calls=[ToolCall(id="1", name="fn", arguments='{"a":1}')]),
        ])
        assert msgs[0]["content"][0] == {"type": "text", "text": "txt"}
        assert msgs[0]["content"][1] == {"type": "tool_use", "id": "1", "name": "fn", "input": {"a": 1}}

    def test_assistant_tool_calls_bad_json(self):
        _, msgs = _client()._messages_to_anthropic([
            ChatMessage(role="assistant", content=None,
                        tool_calls=[ToolCall(id="1", name="fn", arguments="not-json")]),
        ])
        assert msgs[0]["content"][0]["type"] == "tool_use"
        assert msgs[0]["content"][0]["input"] == {}

    def test_empty_system(self):
        sys_text, msgs = _client()._messages_to_anthropic([ChatMessage(role="system", content="")])
        assert sys_text == ""
        assert msgs == []


class TestParseAnthropicResponse:
    def test_text_only(self):
        r = BaseModelClient._parse_anthropic_response({
            "content": [{"type": "text", "text": "hi"}],
            "stop_reason": "end_turn",
        })
        assert r.message.content == "hi"
        assert r.finish_reason == "stop"
        assert r.usage is None

    def test_text_join(self):
        r = BaseModelClient._parse_anthropic_response({
            "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
        })
        assert r.message.content == "a\nb"

    def test_tool_use_and_stop_reason(self):
        r = BaseModelClient._parse_anthropic_response({
            "content": [
                {"type": "tool_use", "id": "u1", "name": "f", "input": {"x": 1}},
            ],
            "stop_reason": "tool_use",
        })
        assert r.finish_reason == "tool_calls"
        tc = r.message.tool_calls[0]
        assert tc.id == "u1" and tc.name == "f"
        assert json.loads(tc.arguments) == {"x": 1}
        assert r.message.content is None

    def test_max_tokens(self):
        r = BaseModelClient._parse_anthropic_response({
            "content": [],
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 3, "output_tokens": 2},
        })
        assert r.finish_reason == "length"
        assert r.usage == {"prompt_tokens": 3, "completion_tokens": 2}


class TestToolsToAnthropic:
    def test_with_function_wrapper(self):
        out = _client()._tools_to_anthropic([{
            "function": {"name": "f", "description": "d", "parameters": {"type": "object"}},
        }])
        assert out[0]["name"] == "f"
        assert out[0]["input_schema"] == {"type": "object"}

    def test_without_wrapper(self):
        out = _client()._tools_to_anthropic([{"name": "g", "description": "", "input_schema": {}}])
        assert out[0]["name"] == "g"
        assert out[0]["input_schema"] == {}


class TestUsageStats:
    def test_update_and_get(self):
        c = _client()
        c._update_usage_stats(10)
        c._update_usage_stats(5)
        stats = c.get_usage_stats()
        assert stats["request_count"] == 2
        assert stats["total_tokens_used"] == 15
        assert stats["last_request_time"] is not None

    def test_get_without_last_request(self):
        stats = _client().get_usage_stats()
        assert stats["last_request_time"] is None

    def test_reset(self):
        c = _client()
        c._update_usage_stats(7)
        c.reset_usage_stats()
        stats = c.get_usage_stats()
        assert stats["request_count"] == 0
        assert stats["total_tokens_used"] == 0
        assert stats["last_request_time"] is None


class TestLogging:
    def test_log_request(self):
        _client()._log_request("POST", "http://u", 10)

    def test_log_payload_no_tools(self):
        _client()._log_payload({"a": 1})

    def test_log_payload_short_tools(self):
        _client()._log_payload({"tools": [{"function": {"name": "f"}}]})

    def test_log_payload_long_tools(self):
        long_tools = [{"function": {"name": "f", "parameters": {"x": "y" * 300}}}]
        _client()._log_payload({"tools": long_tools})

    def test_log_response_body(self):
        _client()._log_response_body(200, 12.5, "ok", tokens=3)

    def test_log_response(self):
        _client()._log_response(200, 1.0, tokens=2)


class TestAsyncContextManager:
    def test_async_context_manager(self):
        c = _client()

        async def go():
            async with c as obj:
                assert obj is c
            return True

        assert asyncio.run(go()) is True


# ── ChatMessage / ToolCall 序列化（断点续思考上下文快照） ──────────────────

def test_chat_message_roundtrip():
    """ChatMessage.to_dict/from_dict 往返无损（含 tool_calls 与 reasoning_content）"""
    from infra.model.base_model import ChatMessage, ToolCall
    msg = ChatMessage(
        role="assistant",
        content="思考输出",
        tool_calls=[ToolCall(id="tc_1", name="web_search", arguments='{"q":"x"}')],
        tool_call_id="tc_1",
        reasoning_content="内部推理",
    )
    restored = ChatMessage.from_dict(msg.to_dict())
    assert restored.role == "assistant"
    assert restored.content == "思考输出"
    assert restored.reasoning_content == "内部推理"
    assert restored.tool_call_id == "tc_1"
    assert restored.tool_calls is not None
    assert restored.tool_calls[0].name == "web_search"
    assert restored.tool_calls[0].id == "tc_1"


def test_chat_message_to_dict_no_tool_calls():
    """无 tool_calls 的消息不生成 tool_calls 键"""
    from infra.model.base_model import ChatMessage
    d = ChatMessage(role="user", content="hi").to_dict()
    assert "tool_calls" not in d
    restored = ChatMessage.from_dict(d)
    assert restored.tool_calls is None
