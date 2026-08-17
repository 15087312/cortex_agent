"""端到端：完整委托调用链路（真实 runner/thinker/blackboard/bus + mock 模型 client）

覆盖流程：
用户输入 → 发 probe_started(large) → large runner 思考 → delegate_task 委托 expert
→ probe_started(expert) → expert runner 完成 → thinking_result 回传
→ large 被唤醒 → 综合结果 → 最终回复落黑板

同时验证：委托链记录（黑板 delegations + 落库）、query_delegation / resume_delegation 工具、
断点续思考快照（黑板 resume_context + 落库）。
"""
import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from infra.model.base_model import ChatMessage, ChatResponse, ToolCall
from modules.thinking.cognition.blackboard import CognitiveBlackboard
from modules.thinking.core.model_runner import (
    ModelRunnerManager,
    get_runner_manager,
    remove_runner_manager,
)


# ── 模型 client fakes ──

def _tc(name, arguments="{}", tid="tc1"):
    return ToolCall(id=tid, name=name, arguments=arguments)


def _resp(content=None, calls=None):
    return ChatResponse(
        message=ChatMessage(content=content, role="assistant", tool_calls=calls),
        finish_reason="tool_calls" if calls else "stop",
    )


def _chat_client(*responses):
    """非流式工具 client：chat 返回预置响应序列"""
    client = MagicMock()
    client.supports_native_tools = True
    delattr(client, "chat_stream")
    client.chat = AsyncMock(side_effect=list(responses))
    return client


class _FakeFactory:
    """模型工厂替身：各角色返回带 fake client 的 ModelInstance"""

    def __init__(self, clients: dict, context_length: dict = None):
        self.clients = clients
        self.context_length = context_length or {}

    def _make(self, identity, key):
        from modules.thinking.model_factory import ModelInstance
        cl = self.context_length.get(key, 0)
        if cl:
            identity.context_length = cl
        return ModelInstance(identity=identity, client=self.clients[key], status="idle")

    def create_large(self, identity=None, **kw):
        return self._make(identity, "large")

    def create_supervisor(self, identity=None, **kw):
        return self._make(identity, "large")

    def create_expert(self, identity=None, **kw):
        return self._make(identity, "expert")


# ── 依赖替身（工具基础设施） ──

def _patch_deps(monkeypatch):
    """mock 工具基础设施 + 前端握手，返回控制工具配置"""
    import infra.mcp.factory as mcp_mod
    mcp = MagicMock()
    mcp.get_tools_for_api.return_value = [
        {"function": {"name": "calc", "description": "简单计算"}},
    ]
    mcp.execute.return_value = MagicMock(success=True, result="2")
    monkeypatch.setattr(mcp_mod, "get_mcp_tool_service", lambda: mcp)

    import modules.security_system.tool_security_gate as gate_mod
    gate = MagicMock()
    gate.check = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr(gate_mod, "get_tool_security_gate", lambda: gate)

    import types
    import sys
    engine = MagicMock()
    engine.estimate_tokens.return_value = 100
    fake_mod = types.ModuleType("modules.thinking.context.compression")
    fake_mod.get_compression_engine = lambda: engine
    monkeypatch.setitem(sys.modules, "modules.thinking.context.compression", fake_mod)

    import modules.thinking.frontend_channel as fc
    monkeypatch.setattr(fc, "confirm_frontend_connection", lambda sid: True)

    return mcp


def _patch_factory(monkeypatch, clients: dict, context_length: dict = None):
    import modules.thinking.model_factory as mf_mod
    monkeypatch.setattr(mf_mod, "get_model_factory", lambda: _FakeFactory(clients, context_length))


def _reset_bus(monkeypatch):
    """重置 MessageBus 单例，返回真实 bus"""
    import modules.thinking.communication.message_bus as mb_mod
    monkeypatch.setattr(mb_mod, "_message_bus", None)
    from modules.thinking.communication.message_bus import get_message_bus
    bus = get_message_bus()
    bus._queues.clear()
    bus._subscriptions.clear()
    bus._pending_responses.clear()
    bus._event_emitters.clear()
    return bus


async def _wait_until(predicate, timeout=10.0, interval=0.05):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


@pytest.fixture
async def e2e_env(monkeypatch, tmp_path):
    """构造端到端环境：真实 bus + runner_manager + fake 模型 client"""
    import modules.database.connection as conn

    # 临时 SQLite（黑板 persist/load 落库用）
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "e2e.db"))
    conn._db_manager = None
    conn._db_manager_lock = threading.RLock()
    conn.get_db_manager().initialize()

    bus = _reset_bus(monkeypatch)
    _patch_deps(monkeypatch)

    blackboard = CognitiveBlackboard(session_id="ses_e2e", turn_id="turn_1")
    turn_context = MagicMock()
    turn_context.session_id = "ses_e2e"

    manager = ModelRunnerManager(
        session_id="ses_e2e",
        blackboard=blackboard,
        turn_context=turn_context,
    )

    yield {
        "bus": bus,
        "manager": manager,
        "blackboard": blackboard,
    }

    # 清理（async fixture teardown 在事件循环内执行）
    for model_id in list(manager.get_active_runners()):
        try:
            runner = manager._runners.get(model_id)
            if runner is not None:
                await runner.stop()
        except Exception:
            pass
    await manager.stop_listening()
    try:
        await remove_runner_manager("ses_e2e")
    except Exception:
        pass


async def _start_large(manager, task="请分析当前玩家数量", probe_id="probe_user_input"):
    """发 probe_started 激活 large runner"""
    import modules.thinking.communication.message_bus as mb
    bus = mb.get_message_bus()
    await bus.send(mb.Message(
        msg_type=mb.MessageType.SYSTEM,
        sender="orchestrator",
        recipient=f"model_runner_manager_{manager.session_id[:8]}",
        content={
            "action": "probe_started",
            "probe_id": probe_id,
            "target_tier": "large",
            "identity_key": "orchestrator",
            "task_description": task,
            "return_to_model_id": "",
            "return_to_session_id": manager.session_id,
            "task_id": "task_root_1",
            "origin_task_id": "task_root_1",
            "caller_model_id": "orchestrator",
            "caller_tier": "large",
        },
    ))


async def test_delegation_chain_full_flow(e2e_env, monkeypatch):
    """完整委托链路：large 委托 expert → 回传 → large 综合 → 最终回复落黑板"""
    env = e2e_env
    manager, blackboard = env["manager"], env["blackboard"]

    # large：先 delegate_task，被唤醒后 continue=false 输出综合结果
    large_client = _chat_client(
        _resp(content=None, calls=[_tc(
            "delegate_task",
            '{"role": "代码实现专家", "task": "计算 1+1", "wait_seconds": 60}',
            tid="ld1",
        )]),
        _resp(content=None, calls=[_tc(
            "continue_thinking",
            '{"continue": false, "result_summary": "综合结果：专家已完成，答案是 2"}',
            tid="ld2",
        )]),
    )
    # expert：continue=false 直接输出结果并结束
    expert_client = _chat_client(
        _resp(content=None, calls=[_tc(
            "continue_thinking",
            '{"continue": false, "result_summary": "专家结果：1+1=2"}',
            tid="ed1",
        )]),
    )
    _patch_factory(monkeypatch, {"large": large_client, "expert": expert_client})

    await manager.start_listening()
    await _start_large(manager)

    # 等待 large 完成（final_response 落黑板）
    ok = await _wait_until(
        lambda: bool(blackboard.final_response) and "综合结果" in (blackboard.final_response or ""),
        timeout=15.0,
    )
    assert ok, f"large 未在超时内完成，final_response={blackboard.final_response}"

    # 断言：最终回复
    assert "综合结果" in blackboard.final_response
    assert "专家已完成" in blackboard.final_response

    # 断言：委托链已记录到黑板（probe_id 为 key）
    delegations = blackboard.delegations
    expert_dlgs = [d for d in delegations.values() if d.role == "代码实现专家"]
    assert len(expert_dlgs) == 1, f"应有一条专家委托，实际 {len(expert_dlgs)}"
    d = expert_dlgs[0]
    assert d.status == "replied", f"委托应为 replied，实际 {d.status}"
    assert d.caller_model_id.startswith("large_primary")
    assert "专家结果" in d.metadata.get("response", "")
    assert "已完成" in d.progress
    # 委托链父节点：large 的 probe_id
    assert d.parent_delegation_id == "probe_user_input"

    # 断言：黑板快照已落库（含委托链 + 断点）
    from modules.database.blackboard_repo import load_blackboard
    state = load_blackboard("ses_e2e", blackboard.blackboard_id)
    assert state is not None
    assert "probe_user_input" in state["delegations"] or len(state["delegations"]) >= 1
    assert state["final_response"] == blackboard.final_response

    # 断言：断点续思考上下文已保存
    assert blackboard.resume_context is not None
    assert len(blackboard.resume_context) >= 2
    assert blackboard.resume_context[0]["role"] == "system"


async def test_query_delegation_e2e(e2e_env, monkeypatch):
    """端到端：委托后 query_delegation 能查到进度/上下文"""
    env = e2e_env
    manager, blackboard = env["manager"], env["blackboard"]

    large_client = _chat_client(
        _resp(content=None, calls=[_tc(
            "delegate_task",
            '{"role": "代码实现专家", "task": "计算 1+1", "wait_seconds": 30}',
            tid="ld1",
        )]),
        _resp(content=None, calls=[_tc(
            "continue_thinking",
            '{"continue": false, "result_summary": "综合结果：完成"}',
            tid="ld2",
        )]),
    )
    expert_client = _chat_client(
        _resp(content=None, calls=[_tc(
            "continue_thinking",
            '{"continue": false, "result_summary": "专家结果：1+1=2"}',
            tid="ed1",
        )]),
    )
    _patch_factory(monkeypatch, {"large": large_client, "expert": expert_client})

    await manager.start_listening()
    await _start_large(manager)
    ok = await _wait_until(lambda: bool(blackboard.final_response), timeout=15.0)
    assert ok

    # 委托完成后：用 query_delegation 读取（模拟模型调用工具）
    expert_dlgs = [d for d in blackboard.delegations.values() if d.role == "代码实现专家"]
    assert expert_dlgs
    did = expert_dlgs[0].delegation_id
    text = blackboard.build_delegation_context(did, 3000)
    assert "状态=replied" in text
    assert "1+1" in text
    assert "专家结果" in text
    # 委托链
    chain = blackboard.get_delegation_chain(did)
    assert len(chain) >= 2  # large 根 + expert


async def test_resume_delegation_e2e(e2e_env, monkeypatch):
    """端到端：resume_delegation 重新委派并默认返回原委托者"""
    env = e2e_env
    manager, blackboard = env["manager"], env["blackboard"]

    # 预先在黑板记录一条委托（模拟已存在但未完成的委托）
    did = blackboard.write_delegation(
        "代码实现专家",
        "继续计算",
        probe_id="probe_stale_1",
        caller_model_id="large_primary",
        caller_tier="large",
        return_to_model_id="large_primary",
        origin_task_id="task_root_1",
        parent_delegation_id="probe_user_input",
    )

    # resume 时通过真实 manager 链路：直接构造 runner 调 _handle_resume_delegation
    import modules.thinking.core.model_runner as mr_mod
    runner = mr_mod.ModelRunner.__new__(mr_mod.ModelRunner)
    runner.blackboard = blackboard
    runner.model_id = "large_primary"
    runner.tier = "large"
    runner.session_id = "ses_e2e"
    runner._return_to_session_id = "ses_e2e"
    runner._delegation_id = "probe_user_input"
    runner._origin_task_id = "task_root_1"
    runner._task_id = "task_root_1"

    captured = {}

    import modules.thinking.core.delegation_port as dp_mod
    async def fake_delegate(self, request):
        captured["request"] = request
        return dp_mod.DelegationResult(
            success=True, probe_id="probe_resumed_1",
            metadata={"probe_id": "probe_resumed_1", "task_id": "task_root_1", "target_tier": "expert"},
        )
    monkeypatch.setattr(dp_mod.ProbeDelegationAdapter, "delegate", fake_delegate)

    out = await runner._handle_resume_delegation({"delegation_id": did})
    assert "已继续委托" in out
    # 未传 return_to → 默认返回原委托者 large_primary
    assert captured["request"].return_to_model_id == "large_primary"
    # 委托链状态更新
    d = blackboard.get_delegation(did)
    assert d["status"] == "running"
    assert "probe_resumed_1" in d["progress"]

async def test_tool_loop_context_summary_e2e(e2e_env, monkeypatch):
    """端到端：工具循环内 90% 上下文自动总结（调当前模型）并注入下轮，摘要落黑板"""
    env = e2e_env
    manager, blackboard = env["manager"], env["blackboard"]

    # large：小窗口（context_length=2 → 阈值 1，任何上下文都触发总结）
    # chat_stream 统一走：先被 _summarize 调用（返回摘要），再被主循环调用（返回结束指令）
    large_client = MagicMock()
    large_client.supports_native_tools = True
    large_client.chat_stream = AsyncMock(side_effect=[
        _resp(content="中间总结"),  # _summarize 调用
        _resp(content=None, calls=[_tc(
            "continue_thinking",
            '{"continue": false, "result_summary": "总结后完成"}',
            tid="ld1",
        )]),  # 主循环模型调用
    ])

    expert_client = _chat_client(
        _resp(content=None, calls=[_tc(
            "continue_thinking",
            '{"continue": false, "result_summary": "专家结果"}',
            tid="ed1",
        )]),
    )
    _patch_factory(monkeypatch, {"large": large_client, "expert": expert_client},
                   context_length={"large": 2, "expert": 100000})

    await manager.start_listening()
    await _start_large(manager)
    ok = await _wait_until(lambda: bool(blackboard.final_response), timeout=15.0)
    assert ok, f"large 未完成: {blackboard.final_response}"

    # 总结被触发：黑板落了一条 tool_loop_summary 观察
    summaries = [o for o in blackboard.observations
                 if o.metadata.get("context_type") == "tool_loop_summary"]
    assert len(summaries) == 1, f"应有自动总结，实际 {len(summaries)}"
    assert "中间总结" in summaries[0].content

    # 总结调用真实发生（chat_stream 被用于 _summarize）
    assert large_client.chat_stream.called

    # 最终回复正常
    assert "总结后完成" in blackboard.final_response


async def test_read_context_tool_e2e(e2e_env, monkeypatch):
    """端到端：模型在工具循环调用 read_context 读取指定轮次记忆并继续"""
    env = e2e_env
    manager, blackboard = env["manager"], env["blackboard"]

    # 预先写入几轮对话到黑板（模拟历史）
    for i in range(1, 6):
        blackboard.write_thought(f"m{i}", "large", f"第{i}轮讨论内容", round_num=i)

    large_client = _chat_client(
        _resp(content=None, calls=[_tc(
            "read_context",
            '{"round_start": 2, "round_end": 4, "context_limit": 2000}',
            tid="ld1",
        )]),
        _resp(content=None, calls=[_tc(
            "continue_thinking",
            '{"continue": false, "result_summary": "综合结论"}',
            tid="ld2",
        )]),
    )
    _patch_factory(monkeypatch, {"large": large_client, "expert": _chat_client()})

    await manager.start_listening()
    await _start_large(manager)
    ok = await _wait_until(lambda: bool(blackboard.final_response), timeout=15.0)
    assert ok, f"large 未完成: {blackboard.final_response}"
    assert "综合结论" in blackboard.final_response
    # read_context 结果作为 tool 消息回传，模型收到后继续（第 2 次 chat 调用发生）
    assert large_client.chat.call_count >= 2
