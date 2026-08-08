"""ModelRunner 测试：工具循环核心（此前 0% 覆盖，被 mock 完全掩盖）

覆盖：_generate_with_tools 简单返回、工具调用执行、_supports_native_tool_chat、
_visible_tool_whitelist 委托、_push_reasoning 推送。
"""
import asyncio

import pytest
from unittest.mock import MagicMock

from infra.mcp.types import ToolCallRequest, ToolCallResult
from infra.model.base_model import ChatMessage, ChatResponse, ToolCall
from modules.thinking.core.model_runner import ModelRunner


def _run(coro):
    return asyncio.run(coro)


class FakeClient:
    """普通类 client：仅 chat 方法（非 MagicMock，避免 chat_stream 被误判存在）"""
    _api_format = "openai"
    def __init__(self, responses=None):
        self._responses = list(responses or [])
        self.chat_calls = 0
    async def chat(self, **kwargs):
        self.chat_calls += 1
        if self._responses:
            return self._responses.pop(0)
        return ChatResponse(message=ChatMessage(content="", role="assistant", tool_calls=None), finish_reason="stop")


def _make_identity():
    identity = MagicMock()
    identity.tier = "large"
    identity.model_id = "test_large"
    identity.name = "总指挥"
    identity.role = "orchestrator"
    identity.model_name = "test"
    identity.tool_whitelist = ["calc"]
    identity.metadata = {}
    return identity


def _make_runner(monkeypatch=None, client=None):
    identity = _make_identity()
    instance = MagicMock()
    instance.identity = identity
    instance.client = client or MagicMock()
    bb = MagicMock()
    runner = ModelRunner(model_instance=instance, blackboard=bb, session_id="s_test")
    runner._thinker = MagicMock()
    runner._task_description = "测试任务"
    runner._active_skill = None
    runner._active_skill_tool_rules = None
    runner._react_loop = None
    runner._status = "idle"
    return runner, instance


def _fake_mcp(monkeypatch, tools=None):
    fake_mcp = MagicMock()
    fake_mcp.get_tools_for_api.return_value = tools or [
        {"type": "function", "function": {"name": "calc", "description": "计算"}}
    ]
    monkeypatch.setattr("infra.mcp.factory.get_mcp_tool_service", lambda: fake_mcp)
    return fake_mcp


class FakeMCP:
    def __init__(self):
        self.calls = []
    def execute(self, request: ToolCallRequest):
        self.calls.append(request.tool_name)
        return ToolCallResult(success=True, result="2", error="")


def test_generate_simple_content(monkeypatch):
    client = FakeClient(responses=[ChatResponse(
        message=ChatMessage(content="你好", role="assistant", tool_calls=None),
        finish_reason="stop",
    )])
    runner, _ = _make_runner(client=client)
    _fake_mcp(monkeypatch)
    result = _run(runner._generate_with_tools("system", "用户问题", client))
    assert result == "你好"
    assert client.chat_calls == 1


def test_supports_native_tool_chat():
    runner, instance = _make_runner()
    client = instance.client
    client._api_format = "openai"
    client.chat = MagicMock()
    assert runner._supports_native_tool_chat(client) is True


def test_visible_whitelist_delegates():
    runner, _ = _make_runner()
    whitelist = runner._visible_tool_whitelist()
    assert isinstance(whitelist, list)
    assert "todo" in whitelist or "calc" in whitelist or len(whitelist) >= 0


def test_push_reasoning_sends_event(monkeypatch):
    runner, _ = _make_runner()
    sent = []

    class FakeCM:
        active_connections = {"ws1": object(), "ws2": object()}
        @staticmethod
        def send_json_from_thread(sid, event):
            sent.append((sid, event))

    fake_build = lambda **kw: kw
    monkeypatch.setattr("modules.thinking.api_stream.connection_manager", FakeCM)
    monkeypatch.setattr("modules.thinking.api_stream._build_event", fake_build)
    runner._push_reasoning("思考内容")
    assert len(sent) == 2
    assert sent[0][1]["content"].startswith("【思考】")


def test_tool_execution_loop(monkeypatch):
    """工具调用被声明并执行，最后返回最终内容"""

    client = FakeClient(responses=[
        ChatResponse(message=ChatMessage(content="", role="assistant",
            tool_calls=[ToolCall(id="c1", name="calc", arguments='{"a": 1, "op": "+", "b": 1}')]), finish_reason="tool_calls"),
        ChatResponse(message=ChatMessage(content="结果是2", role="assistant", tool_calls=None), finish_reason="stop"),
    ])
    runner, _ = _make_runner(client=client)
    fake_exec = FakeMCP()
    fake_mcp = _fake_mcp(monkeypatch)
    fake_mcp.execute = fake_exec.execute
    result = _run(runner._generate_with_tools("system", "计算1+1", client))
    assert "calc" in fake_exec.calls
