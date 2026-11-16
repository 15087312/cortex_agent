"""ReAct 工具内循环工作流 (ReActSubflow) 单元测试

验证 ReAct 状态图推理、工具调用分类、安全门与执行回环逻辑。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from infra.model.base_model import ChatMessage
from modules.thinking.core.workflow_react import (
    ReActInferenceNode,
    ReActLoopState,
    ReActSubflow,
    ReActToolDispatchNode,
    ReActToolExecutionNode,
)


def _run(coro):
    return asyncio.run(coro)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.call_count = 0
        self.max_tokens = 2048

    async def chat_stream(self, on_token=None, on_reasoning=None, **kwargs):
        resp = self.responses[self.call_count]
        self.call_count += 1
        content = resp.message.content or ""
        if on_token and content:
            on_token(content)
        return resp


def _make_msg(content="", tool_calls=None, reasoning=""):
    return type("Resp", (), {
        "message": type("Msg", (), {
            "content": content,
            "tool_calls": tool_calls or [],
            "reasoning_content": reasoning,
        })()
    })()


def test_react_subflow_direct_text_output():
    """验证模型无工具调用时直接结束并返回文本"""
    client = FakeClient([
        _make_msg(content="直接返回回答结果"),
    ])
    subflow = ReActSubflow(client=client)

    result = _run(subflow.run({
        "session_id": "s_direct",
        "model_id": "large_01",
        "tier": "large",
        "messages": [ChatMessage(role="user", content="你好")],
        "tools_with_control": [],
        "max_turns": 5,
    }))

    assert result == "直接返回回答结果"
    assert client.call_count == 1


def test_react_subflow_tool_execution_loop():
    """验证模型发起工具调用 -> 执行工具 -> 回环再次推理产出最终文本"""
    tc = type("ToolCall", (), {
        "id": "call_mock_1",
        "name": "mock_echo",
        "arguments": '{"msg": "hello_tool"}',
    })()

    # 注册一个测试用假工具
    from infra.tool_manager.tool_registry import ToolRegistry
    ToolRegistry.provide("mock_echo", lambda msg="": f"ECHO: {msg}")

    client = FakeClient([
        # 第 1 轮：模型输出调用 mock_echo
        _make_msg(content="", tool_calls=[tc]),
        # 第 2 轮：模型看到 tool 回包后输出最终答案
        _make_msg(content="工具已执行完毕，结果是 ECHO: hello_tool"),
    ])

    subflow = ReActSubflow(client=client)
    result = _run(subflow.run({
        "session_id": "s_tool",
        "model_id": "large_01",
        "tier": "large",
        "messages": [ChatMessage(role="user", content="请调用 echo 工具")],
        "tools_with_control": [{"function": {"name": "mock_echo"}}],
        "max_turns": 5,
    }))

    assert "ECHO: hello_tool" in result
    assert client.call_count == 2


def test_react_subflow_delegate_stops():
    """验证触发 delegate_task 时正确记录委托并结束子工作流"""
    del_tc = type("ToolCall", (), {
        "id": "call_del_1",
        "name": "delegate_task",
        "arguments": '{"role": "code_expert", "task": "编写脚本"}',
    })()

    mock_runner = MagicMock()
    mock_runner._thinker = MagicMock()

    client = FakeClient([
        _make_msg(content="", tool_calls=[del_tc]),
    ])

    subflow = ReActSubflow(client=client, runner_ref=mock_runner)
    result = _run(subflow.run({
        "session_id": "s_del",
        "model_id": "large_01",
        "tier": "large",
        "messages": [],
        "tools_with_control": [],
        "max_turns": 5,
    }))

    assert "已委托" in result
    mock_runner._thinker.record_delegation.assert_called_once()


def test_model_runner_workflow_react_full_pipeline(monkeypatch):
    """端到端验证 ModelRunner._generate_with_tools 在 workflow 模式下完整对接前端推流、安全门控与工具执行。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from config.settings import settings
    from modules.thinking.core.model_runner import ModelRunner
    import modules.thinking.core.model_runner as mr_mod

    monkeypatch.setattr(settings, "THINKING_ENGINE_MODE", "workflow")

    runner = ModelRunner.__new__(ModelRunner)
    runner.model_id = "large_e2e"
    runner.tier = "large"
    runner.session_id = "sess_wf_e2e"
    runner.identity = SimpleNamespace(role="orchestrator", tier="large", tool_whitelist=[])
    runner._thinker = MagicMock()
    runner.blackboard = None
    runner._task_description = "测试端到端流式"
    runner._return_to_model_id = None
    runner._return_to_session_id = None
    runner._task_id = "task_wf"
    runner._active_skill = None
    runner._active_skill_tool_rules = None
    runner._last_known_mode = "edit"
    runner._current_streaming_content = ""
    runner._react_loop = None
    runner._status = "idle"
    runner._status_detail = ""
    runner._activity = MagicMock()

    streamed_chunks = []
    reasoning_deltas = []
    tool_calls_pushed = []
    tool_results_pushed = []
    recorded_usages = []

    runner._save_resume_context = MagicMock()
    runner._maybe_summarize_context = AsyncMock(return_value=False)
    runner._emit_streaming_content = MagicMock()
    runner._push_stream_chunk = lambda chunk, turn=0: streamed_chunks.append(chunk)
    runner._push_reasoning_delta = lambda chunk, turn=0: reasoning_deltas.append(chunk)
    runner._push_reasoning = MagicMock()
    runner._persist_reasoning = MagicMock()
    runner._push_expert_output = MagicMock()
    runner._push_tool_call = lambda tc, args: tool_calls_pushed.append((tc.name, args))
    runner._push_tool_result = lambda tc, res, **kw: tool_results_pushed.append((tc.name, res, kw.get("status")))
    runner._record_usage = lambda u: recorded_usages.append(u)
    runner._missing_required_tool_args = lambda name, args: []
    runner._check_messages = AsyncMock(return_value=[])
    runner._build_system_prompt_for_mode = MagicMock(return_value="SYS")

    # Mock 安全门控
    fake_gate = MagicMock()
    fake_gate.check = AsyncMock(return_value=(True, "ok"))
    monkeypatch.setattr(mr_mod, "get_tool_security_gate", lambda: fake_gate)

    # Mock MCP 工具服务
    import infra.mcp.factory as mcp_factory_mod
    fake_mcp = MagicMock()
    fake_mcp.get_tools_for_api = MagicMock(
        return_value=[{"type": "function", "function": {"name": "echo_tool", "description": "echo"}}]
    )
    fake_mcp.execute = MagicMock(return_value=SimpleNamespace(success=True, result="42", error=None))
    monkeypatch.setattr(mcp_factory_mod, "get_mcp_tool_service", lambda: fake_mcp)

    # Mock Stream Client：第一轮调用工具并推流 reasoning，第二轮逐字推流最终文本
    turn_idx = {"i": 0}
    tc1 = SimpleNamespace(id="call_1", name="echo_tool", arguments='{"msg": "hi"}')

    class FakeStreamClient:
        max_tokens = 2048

        async def chat_stream(self, on_token=None, on_reasoning=None, **kwargs):
            idx = turn_idx["i"]
            turn_idx["i"] += 1
            if idx == 0:
                if on_reasoning:
                    on_reasoning("思考：需要调工具。")
                return SimpleNamespace(
                    message=SimpleNamespace(content="", tool_calls=[tc1], reasoning_content="思考：需要调工具。"),
                    usage={"total_tokens": 10},
                )
            else:
                for tok in ["你好，", "结果是 42。"]:
                    if on_token:
                        on_token(tok)
                return SimpleNamespace(
                    message=SimpleNamespace(content="你好，结果是 42。", tool_calls=[], reasoning_content=""),
                    usage={"total_tokens": 20},
                )

    runner._get_client = lambda: FakeStreamClient()

    out = _run(runner._generate_with_tools("SYS", "计算结果", FakeStreamClient()))

    assert out == "你好，结果是 42。"
    assert "".join(streamed_chunks) == "你好，结果是 42。"
    assert reasoning_deltas == ["思考：需要调工具。"]
    assert tool_calls_pushed == [("echo_tool", {"msg": "hi"})]
    assert tool_results_pushed == [("echo_tool", "42", "ok")]
    assert len(recorded_usages) == 2
    fake_gate.check.assert_awaited_once()
    fake_mcp.execute.assert_called_once()
