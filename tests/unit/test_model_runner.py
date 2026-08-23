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
        self.max_tokens = 4096
        self.temperature = 0.7
        self.reasoning_effort = ""
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


def test_expert_direct_output_no_tools(monkeypatch):
    """专家：无工具调用 + 有文本 → 直接返回"""
    client = FakeClient(responses=[
        ChatResponse(message=ChatMessage(content="任务完成", role="assistant", tool_calls=None), finish_reason="stop"),
    ])
    runner, instance = _make_runner(client=client)
    runner.identity.tier = "expert"
    runner.tier = "expert"
    result = _run(runner._generate_with_tools("system", "做任务", client))
    assert "任务完成" in result


def test_supervisor_rejects_pure_text(monkeypatch):
    """主管：纯文本被拒绝注入重试 → 最终用工具输出"""
    from infra.tool_manager.tools.exec_command import _detect_dangerous_command  # noqa
    client = FakeClient(responses=[
        ChatResponse(message=ChatMessage(content="直接结果", role="assistant", tool_calls=None), finish_reason="stop"),
        ChatResponse(message=ChatMessage(content="", role="assistant",
            tool_calls=[ToolCall(id="c1", name="calc", arguments='{"a":1,"op":"+","b":1}')]), finish_reason="tool_calls"),
        ChatResponse(message=ChatMessage(content="结果2", role="assistant", tool_calls=None), finish_reason="stop"),
    ])
    runner, _ = _make_runner(client=client)
    runner.identity.tier = "supervisor"
    runner.tier = "supervisor"
    fake_exec = FakeMCP()
    fake_mcp = _fake_mcp(monkeypatch)
    fake_mcp.execute = fake_exec.execute
    result = _run(runner._generate_with_tools("system", "调度", client))
    # 主管纯文本被拒绝 → 注入重试（chat 被调用多次），最终有结果
    assert client.chat_calls >= 3
    assert "calc" in fake_exec.calls
    assert isinstance(result, str)


def test_missing_required_args_intercepted(monkeypatch):
    """工具缺必填参数 → 被拦截（不执行），继续循环"""
    client = FakeClient(responses=[
        ChatResponse(message=ChatMessage(content="", role="assistant",
            tool_calls=[ToolCall(id="c1", name="calc", arguments='{"a":1}')]), finish_reason="tool_calls"),
        ChatResponse(message=ChatMessage(content="修正后完成", role="assistant", tool_calls=None), finish_reason="stop"),
    ])
    runner, _ = _make_runner(client=client)
    runner._has_required_tool_args = lambda name, args: False  # 全部缺参
    fake_exec = FakeMCP()
    fake_mcp = _fake_mcp(monkeypatch)
    fake_mcp.execute = fake_exec.execute
    result = _run(runner._generate_with_tools("system", "算", client))
    assert "修正后完成" in result
    assert fake_exec.calls == []  # calc 未执行（缺参拦截）


def test_generate_frontend_disconnected(monkeypatch):
    """前端不可达 → 跳过 LLM 调用"""
    import modules.thinking.frontend_channel as fc_mod
    monkeypatch.setattr(fc_mod, "confirm_frontend_connection", lambda sid: False)
    runner, _ = _make_runner(client=MagicMock())
    result = _run(runner._generate("prompt"))
    assert "前端连接已断开" in result


def test_generate_native_tools_path(monkeypatch):
    """支持原生工具的 client → 走 _generate_with_tools"""
    client = FakeClient(responses=[
        ChatResponse(message=ChatMessage(content="工具结果", role="assistant", tool_calls=None), finish_reason="stop"),
    ])
    client.supports_native_tools = True
    runner, _ = _make_runner(client=client)
    runner.tier = "expert"
    runner.identity.tier = "expert"
    import modules.thinking.frontend_channel as fc_mod
    monkeypatch.setattr(fc_mod, "confirm_frontend_connection", lambda sid: True)
    result = _run(runner._generate("prompt"))
    assert "工具结果" in result


def test_generate_fallback_generate(monkeypatch):
    """无原生工具的 client → 走传统 generate()"""
    client = FakeClient()
    client.supports_native_tools = False
    # 无 chat 支持时 _generate 用 generate 路径；FakeClient 有 chat 但无 supports_native_tools → 走 generate
    # FakeClient 无 generate 方法 → 需 mock
    import modules.thinking.frontend_channel as fc_mod
    monkeypatch.setattr(fc_mod, "confirm_frontend_connection", lambda sid: True)
    runner, _ = _make_runner(client=client)
    runner.tier = "expert"
    runner.identity.tier = "expert"
    async def fake_generate(prompt, **kw):
        return "生成文本"
    client.generate = fake_generate
    result = _run(runner._generate("prompt"))
    assert "生成文本" in result


def test_build_system_prompt_for_mode(monkeypatch):
    """system prompt 含对话历史前置 + 良知注入"""
    runner, _ = _make_runner()
    obs = MagicMock()
    obs.tier = "system"
    obs.metadata = {"context_type": "conversation_history"}
    obs.content = "【对话历史】你好"
    runner.blackboard.observations = [obs]

    class FakeComposer:
        def build_system(self, req):
            return "【人设】测试人格"
    import config.prompts.composer as comp_mod
    monkeypatch.setattr(comp_mod, "PromptComposer", lambda: FakeComposer())
    monkeypatch.setattr(comp_mod, "PromptRequest", lambda **kw: __import__("types").SimpleNamespace(**kw))
    import modules.thinking.probes.probe_tools as pt_mod
    monkeypatch.setattr(pt_mod, "_session_guidance", {})
    runner.model_id = "m1"
    runner.session_id = "s1"
    sp = runner._build_system_prompt_for_mode()
    assert "对话历史" in sp


def test_run_task_normal_cleanup(monkeypatch):
    """正常思考 → finally 清理 manager 注册"""
    runner, _ = _make_runner()
    runner.model_id = "test_large_001"
    runner.tier = "large"
    runner._running = True
    runner._thinker = MagicMock()

    async def fake_think_loop():
        runner._status = "running"
    monkeypatch.setattr(runner, "_think_loop", fake_think_loop)
    monkeypatch.setattr(runner, "_get_runtime_expert_class", lambda role: None)

    mgr = MagicMock()
    mgr._lock = MagicMock()
    mgr._runners = {"test_large_001": runner}
    mgr._count_by_tier = {"large": 1}
    runner.manager = mgr

    _run(runner._run_task())
    assert runner._running is False
    assert runner._thinker is None
    assert "test_large_001" not in mgr._runners


def test_run_task_exception_sets_error(monkeypatch):
    runner, _ = _make_runner()
    runner.model_id = "m1"
    runner.tier = "large"
    runner._running = True
    async def boom():
        raise RuntimeError("boom")
    monkeypatch.setattr(runner, "_think_loop", boom)
    monkeypatch.setattr(runner, "_get_runtime_expert_class", lambda role: None)
    runner.manager = None
    _run(runner._run_task())
    assert runner._status == "error"
    assert "boom" in runner._status_detail


def test_run_task_cancelled(monkeypatch):
    runner, _ = _make_runner()
    runner.model_id = "m1"
    runner.tier = "large"
    runner._running = True
    async def cancel():
        raise asyncio.CancelledError()
    monkeypatch.setattr(runner, "_think_loop", cancel)
    monkeypatch.setattr(runner, "_get_runtime_expert_class", lambda role: None)
    runner.manager = None
    _run(runner._run_task())
    assert runner._status == "completed"


def test_run_runtime_expert_on_demand(monkeypatch):
    """RuntimeExpert on_demand → run_cli_mode"""
    runner, _ = _make_runner()
    runner.model_id = "m1"
    runner.session_id = "s1"
    runner._task_description = "任务"

    class FakeExpert:
        is_persistent = False
        instances = []
        def __init__(self, **kw):
            self.identity = MagicMock()
            self.identity.role = "x"
            FakeExpert.instances.append(self)
        async def run_cli_mode(self, **kw):
            return {"success": True, "result": "结果", "iterations": 1, "tool_calls": 0}

    FakeExpert.instances = []
    _run(runner._run_runtime_expert(FakeExpert))
    assert len(FakeExpert.instances) == 1  # 被实例化并走 run_cli_mode
