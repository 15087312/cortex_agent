"""model_runner 纯方法测试（此前 18% 覆盖）"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import modules.thinking.core.model_runner as mr_mod
from modules.thinking.core.model_runner import ModelRunner


def test_get_tool_security_gate(monkeypatch):
    import modules.security_system.tool_security_gate as tsg
    fake = MagicMock()
    monkeypatch.setattr(tsg, "get_tool_security_gate", lambda: fake)
    assert mr_mod.get_tool_security_gate() is fake


def _runner(**kw):
    inst = MagicMock()
    ident = MagicMock()
    ident.model_id = kw.get("model_id", "large_primary")
    ident.tier = kw.get("tier", "large")
    inst.identity = ident
    r = ModelRunner.__new__(ModelRunner)
    r.instance = inst
    r.identity = ident
    r.model_id = ident.model_id
    r.tier = ident.tier
    r.blackboard = MagicMock()
    r.session_id = kw.get("session_id", "s1")
    r.manager = None
    r._running = False
    r._task = None
    r._task_description = ""
    r._task_id = ""
    r._return_to_model_id = ""
    r._return_to_session_id = ""
    r._started_at = 0.0
    r._status = "idle"
    r._status_detail = ""
    r._react_loop = None
    r._think_loop_state = None
    r._pending_guidance = []
    r._thinker = None
    r._active_skill = None
    r._active_skill_tool_rules = None
    r._wakeup_event = None
    return r


def test_context_tokens():
    r = _runner()
    assert r.context_tokens == 0
    r._thinker = MagicMock()
    r._thinker._context_tokens = 100
    assert r.context_tokens == 100


def test_context_window_size():
    r = _runner()
    assert r.context_window_size == 128000
    r._thinker = MagicMock()
    r._thinker._context_window_size = 64000
    assert r.context_window_size == 64000


def test_supervisor_property():
    r = _runner()
    assert r.supervisor == ""
    r._return_to_model_id = "supervisor_x"
    assert r.supervisor == "supervisor_x"


def test_inject_guidance():
    r = _runner()
    r.inject_guidance("引导")
    assert r._pending_guidance == ["引导"]


def test_update_loop_state():
    r = _runner()
    r._update_loop_state(think_round=3, think_max=10, think_wait=2.5)
    assert r._think_loop_state == {"round": 3, "max": 10, "wait": 2.5}
    assert r._react_loop is None
    r._react_loop = {"turn": 1}
    r._update_loop_state(think_round=0)
    assert r._think_loop_state == {"round": 3, "max": 10, "wait": 2.5}
    assert r._react_loop is None


def test_build_awakening_progress():
    r = _runner()
    prompt = r._build_awakening_prompt("【进度汇报】专家正常")
    assert "进度汇报" in prompt
    assert "继续等待" in prompt


def test_build_awakening_timeout():
    r = _runner()
    prompt = r._build_awakening_prompt("【等待超时】任务超时")
    assert "等待超时" in prompt


def test_build_awakening_source_tier():
    r = _runner()
    prompt = r._build_awakening_prompt("source_tier=expert 任务完成")
    assert "专家" in prompt


def test_build_awakening_has_results():
    r = _runner()
    thinker = MagicMock()
    thinker._pending_delegations = {"d1": {"status": "completed", "result_received": True}}
    r._thinker = thinker
    prompt = r._build_awakening_prompt("结果来了")
    assert "任务已有结果" in prompt


def test_build_awakening_no_results():
    r = _runner()
    thinker = MagicMock()
    thinker._pending_delegations = {"d1": {"status": "pending", "result_received": False}}
    r._thinker = thinker
    prompt = r._build_awakening_prompt("还在执行")
    assert "任务状态" in prompt


def test_collect_expert_progress_empty(monkeypatch):
    r = _runner()
    import modules.thinking.core.model_runner as mod
    monkeypatch.setattr(mod, "_runner_managers", {})
    assert asyncio.run(r._collect_expert_progress()) == ""


def test_emit_streaming_content(monkeypatch):
    r = _runner()
    import modules.thinking.communication.message_bus as mb
    bus = MagicMock()
    sent = []
    async def fake_broadcast(msg):
        sent.append(msg)
    bus.broadcast = fake_broadcast
    monkeypatch.setattr(mb, "get_message_bus", lambda: bus)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(loop.create_task(_emit(r)))
    loop.close()


async def _emit(r):
    r._emit_streaming_content("增量", 1)
    await asyncio.sleep(0)


def test_emit_streaming_no_loop(monkeypatch):
    r = _runner()
    import modules.thinking.communication.message_bus as mb
    bus = MagicMock()
    bus.broadcast = MagicMock(side_effect=RuntimeError)
    monkeypatch.setattr(mb, "get_message_bus", lambda: bus)
    r._emit_streaming_content("x", 1)  # 无 running loop 时安全


def test_save_partial_result_no_thinker():
    r = _runner()
    r._thinker = None
    import asyncio
    asyncio.run(r._save_partial_result())  # 直接返回


def test_save_partial_result_saves(monkeypatch):
    r = _runner()
    thinker = MagicMock()
    thinker.history_thoughts = ["第一轮", "第二轮"]
    r._thinker = thinker
    r._current_streaming_content = "未完成内容"
    bb = MagicMock()
    r.blackboard = bb
    r._notify_thinking_complete = MagicMock(return_value=None)
    async def fake_notify():
        return None
    r._notify_thinking_complete = fake_notify
    import asyncio
    asyncio.run(r._save_partial_result())
    assert bb.set_final_response.called
    assert bb.add_observation.called
    assert r._current_streaming_content == ""


def test_run_task_cleanup(monkeypatch):
    r = _runner()
    r._running = True
    r.manager = None
    async def fake_loop():
        return None
    r._think_loop = fake_loop
    r._get_runtime_expert_class = lambda role: None
    import asyncio
    asyncio.run(r._run_task())
    assert r._running is False
    assert r._thinker is None


def test_run_task_error_status(monkeypatch):
    r = _runner()
    r._running = True
    r.manager = None
    async def bad_loop():
        raise RuntimeError("崩溃")
    r._think_loop = bad_loop
    r._get_runtime_expert_class = lambda role: None
    import asyncio
    asyncio.run(r._run_task())
    assert r._status == "error"
    assert r._running is False


def test_run_task_cancelled(monkeypatch):
    r = _runner()
    r._running = True
    r.manager = None
    async def cancel_loop():
        raise asyncio.CancelledError()
    r._think_loop = cancel_loop
    r._get_runtime_expert_class = lambda role: None
    import asyncio
    asyncio.run(r._run_task())
    assert r._status == "completed"


def test_run_task_manager_cleanup(monkeypatch):
    r = _runner()
    r._running = True
    r.manager = None
    mgr = MagicMock()
    mgr._lock = __import__("threading").RLock()
    mgr._runners = {"large_primary": r}
    mgr._count_by_tier = {"large": 1}
    r.manager = mgr
    async def fake_loop():
        return None
    r._think_loop = fake_loop
    r._get_runtime_expert_class = lambda role: None
    import asyncio
    asyncio.run(r._run_task())
    assert "large_primary" not in mgr._runners
    assert mgr._count_by_tier["large"] == 0


def test_get_runtime_expert_class(monkeypatch):
    import modules.thinking.runtime_expert as re_mod
    fake = MagicMock()
    monkeypatch.setattr(re_mod, "get_runtime_expert_class", lambda role: fake)
    assert ModelRunner._get_runtime_expert_class("code_writer") is fake


def test_think_loop_expert_single_round(monkeypatch):
    r = _runner(tier="expert")
    r._running = True
    r._task_description = "任务"
    r._task_id = "t1"
    r._return_to_model_id = ""
    r._return_to_session_id = "s1"
    r.identity_key = "code_writer"
    thinker = MagicMock()
    thinker.continuous_think = AsyncMock(return_value=[])
    thinker._pending_delegations = {}
    thinker._running = False
    CT = MagicMock(return_value=thinker)
    import modules.thinking.core.continuous_thinker as ct_mod
    monkeypatch.setattr(ct_mod, "ContinuousThinker", CT)
    import modules.thinking.communication.interface as iface_mod
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)
    r._write_final_result = AsyncMock()
    r._notify_thinking_complete = AsyncMock()
    import asyncio
    asyncio.run(r._think_loop())
    assert r._thinker is thinker
    thinker.continuous_think.assert_awaited_once()
    bus.subscribe.assert_awaited_once()
    r._notify_thinking_complete.assert_awaited_once()


def test_think_loop_large_with_pending_waits(monkeypatch):
    r = _runner(tier="large")
    r._running = True
    r._task_description = "任务"
    r._task_id = "t1"
    r._return_to_model_id = ""
    r._return_to_session_id = "s1"
    r.identity_key = ""
    thinker = MagicMock()
    thinker.continuous_think = AsyncMock(return_value=[{"thought": "x"}])
    thinker._pending_delegations = {"d1": {"status": "pending"}}
    thinker._running = True
    CT = MagicMock(return_value=thinker)
    import modules.thinking.core.continuous_thinker as ct_mod
    monkeypatch.setattr(ct_mod, "ContinuousThinker", CT)
    import modules.thinking.communication.interface as iface_mod
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)
    r._write_final_result = AsyncMock()
    r._notify_thinking_complete = AsyncMock()
    # _wait_for_wakeup_event 返回 None（无委托待处理后退出）
    r._wait_for_wakeup_event = AsyncMock(return_value=None)
    # 第一轮后无 pending → 退出（避免死循环）：模拟 continuous_think 一轮后 pending 清空
    thinker._pending_delegations = {}
    import asyncio
    asyncio.run(r._think_loop())
    r._notify_thinking_complete.assert_awaited_once()


def test_think_loop_cancelled_saves(monkeypatch):
    import asyncio as _aio
    r = _runner(tier="expert")
    r._running = True
    r._task_description = "任务"
    r._task_id = "t1"
    r._return_to_model_id = ""
    r._return_to_session_id = "s1"
    r.identity_key = ""
    thinker = MagicMock()
    thinker.continuous_think = AsyncMock(side_effect=_aio.CancelledError())
    thinker._running = True
    CT = MagicMock(return_value=thinker)
    import modules.thinking.core.continuous_thinker as ct_mod
    monkeypatch.setattr(ct_mod, "ContinuousThinker", CT)
    import modules.thinking.communication.interface as iface_mod
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)
    r._write_final_result = AsyncMock()
    r._notify_thinking_complete = AsyncMock()
    r._save_partial_result = AsyncMock()
    import asyncio
    asyncio.run(r._think_loop())
    r._save_partial_result.assert_awaited_once()


def _gen_runner(**kw):
    r = _runner(**kw)
    r._build_system_prompt_for_mode = lambda: "system"
    r._build_time_context = lambda: "时间"
    r._generate_with_tools = AsyncMock(return_value="工具结果")
    r.THINK_TIMEOUT = 60.0
    r.GENERATE_RETRIES = 2
    r.GENERATE_RETRY_DELAY = 0.01
    return r


def test_generate_traditional(monkeypatch):
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda session_id=None: True)  # 模拟前端在线
    r = _gen_runner()
    client = MagicMock()
    client.generate = AsyncMock(return_value="生成结果")
    client.supports_native_tools = False
    r.instance.client = client
    r._supports_native_tool_chat = lambda c: False
    import asyncio
    out = asyncio.run(r._generate("提示"))
    assert out == "生成结果"


def test_generate_retry_then_success(monkeypatch):
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda session_id=None: True)  # 模拟前端在线
    r = _gen_runner()
    client = MagicMock()
    async def gen(prompt, max_tokens=4096):
        if gen.n == 0:
            gen.n += 1
            raise RuntimeError("网络错误")
        return "重试成功"
    gen.n = 0
    client.generate = gen
    r.instance.client = client
    r._supports_native_tool_chat = lambda c: False
    import asyncio
    out = asyncio.run(r._generate("提示"))
    assert out == "重试成功"


def test_generate_503_after_retries(monkeypatch):
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda session_id=None: True)  # 模拟前端在线
    r = _gen_runner()
    client = MagicMock()
    async def gen(prompt, max_tokens=4096):
        raise RuntimeError("503 Service Unavailable")
    client.generate = gen
    r.instance.client = client
    r._supports_native_tool_chat = lambda c: False
    import asyncio
    out = asyncio.run(r._generate("提示"))
    assert "503" in out


def test_generate_failure_message(monkeypatch):
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda session_id=None: True)  # 模拟前端在线
    r = _gen_runner()
    client = MagicMock()
    async def gen(prompt, max_tokens=4096):
        raise RuntimeError("API 挂了")
    client.generate = gen
    r.instance.client = client
    r._supports_native_tool_chat = lambda c: False
    import asyncio
    out = asyncio.run(r._generate("提示"))
    assert "模型调用失败" in out


def test_generate_native_tools(monkeypatch):
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda session_id=None: True)  # 模拟前端在线
    r = _gen_runner()
    client = MagicMock()
    r.instance.client = client
    r._supports_native_tool_chat = lambda c: True
    import asyncio
    out = asyncio.run(r._generate("提示"))
    assert out == "工具结果"
    r._generate_with_tools.assert_awaited_once()


def test_visible_tool_whitelist(monkeypatch):
    r = _runner()
    r._active_skill_tool_rules = None
    import modules.security_system.tool_permission_controller as tpc
    ctrl = MagicMock()
    ctrl.get_visible_tools.return_value = ["tool_a"]
    monkeypatch.setattr(tpc, "get_tool_permission_controller", lambda: ctrl)
    import importlib
    cfg_mod = importlib.import_module("config.settings")
    old = cfg_mod.settings
    from types import SimpleNamespace
    cfg_mod.settings = SimpleNamespace(effective_execution_mode="edit")
    try:
        out = r._visible_tool_whitelist()
        assert out == ["tool_a"]
    finally:
        cfg_mod.settings = old


def test_generate_with_tools_final_text(monkeypatch):
    r = _runner(tier="large")
    r._visible_tool_whitelist = lambda: ["tool_a"]
    r.GENERATE_RETRIES = 1
    r.MAX_CHAT_TOOL_TURNS = 25
    r._react_loop = None
    r._status = ""
    r._status_detail = ""
    r._current_streaming_content = ""
    r._thinker = MagicMock()
    r._last_known_mode = ""

    import infra.mcp.factory as mcp_mod
    mcp = MagicMock()
    mcp.get_tools_for_api.return_value = [{"function": {"name": "tool_a", "description": "工具"}}]
    monkeypatch.setattr(mcp_mod, "get_mcp_tool_service", lambda: mcp)

    import modules.security_system.tool_permission_controller as tpc
    ctrl = MagicMock()
    ctrl.get_control_tools.return_value = []
    monkeypatch.setattr(tpc, "get_tool_permission_controller", lambda: ctrl)

    client = MagicMock()
    delattr(client, 'chat_stream')  # 走 chat() 而非 chat_stream()
    msg = MagicMock()
    msg.message.content = "最终回复"
    msg.message.tool_calls = None
    msg.message.reasoning_content = ""
    client.chat = AsyncMock(return_value=msg)
    r.instance.client = client
    r._push_reasoning = MagicMock()

    import asyncio
    out = asyncio.run(r._generate_with_tools("system", "user", client))
    assert out == "最终回复"
    client.chat.assert_awaited_once()


def test_generate_with_tools_no_tools(monkeypatch):
    r = _runner(tier="large")
    r._visible_tool_whitelist = lambda: []
    import infra.mcp.factory as mcp_mod
    mcp = MagicMock()
    mcp.get_tools_for_api.return_value = []
    monkeypatch.setattr(mcp_mod, "get_mcp_tool_service", lambda: mcp)
    client = MagicMock()
    r.instance.client = client
    import asyncio
    try:
        asyncio.run(r._generate_with_tools("s", "u", client))
        assert False
    except RuntimeError as e:
        assert "无可用工具" in str(e)


def test_generate_with_tools_expert_final(monkeypatch):
    r = _runner(tier="expert")
    r._visible_tool_whitelist = lambda: ["tool_a"]
    r.GENERATE_RETRIES = 1
    r.MAX_CHAT_TOOL_TURNS = 25
    r._react_loop = None
    r._status = ""
    r._status_detail = ""
    r._current_streaming_content = ""
    r._thinker = MagicMock()
    r._last_known_mode = ""

    import infra.mcp.factory as mcp_mod
    mcp = MagicMock()
    mcp.get_tools_for_api.return_value = [{"function": {"name": "tool_a", "description": "工具"}}]
    monkeypatch.setattr(mcp_mod, "get_mcp_tool_service", lambda: mcp)
    import modules.security_system.tool_permission_controller as tpc
    ctrl = MagicMock()
    ctrl.get_control_tools.return_value = []
    monkeypatch.setattr(tpc, "get_tool_permission_controller", lambda: ctrl)

    client = MagicMock()
    delattr(client, 'chat_stream')
    msg = MagicMock()
    msg.message.content = "专家完成"
    msg.message.tool_calls = None
    msg.message.reasoning_content = ""
    client.chat = AsyncMock(return_value=msg)
    r.instance.client = client
    r._push_reasoning = MagicMock()
    import asyncio
    out = asyncio.run(r._generate_with_tools("s", "u", client))
    assert out == "专家完成"
    r._thinker.record_control_decision.assert_called_once()


def _final_runner(**kw):
    r = _runner(**kw)
    r._thinker = kw.get("thinker", None)
    r._task_description = "任务描述"
    r._task_id = "t1"
    return r


def test_write_final_result_no_thinker():
    r = _final_runner()
    r.blackboard = MagicMock()
    import asyncio
    asyncio.run(r._write_final_result())  # 无 thinker → 无结果跳过


def test_write_final_result_large(monkeypatch):
    r = _final_runner(tier="large")
    snap = MagicMock()
    snap.final_result = "最终结果"
    cd = MagicMock()
    cd.result_summary = "精炼结果"
    snap.control_decision = cd
    thinker = MagicMock()
    thinker.get_process_snapshot.return_value = snap
    r._thinker = thinker
    bb = MagicMock()
    r.blackboard = bb
    import asyncio
    asyncio.run(r._write_final_result())
    bb.set_final_response.assert_called_once_with("精炼结果")


def test_write_final_result_large_no_summary(monkeypatch):
    r = _final_runner(tier="large")
    snap = MagicMock()
    snap.final_result = "结果"
    snap.control_decision = None
    thinker = MagicMock()
    thinker.get_process_snapshot.return_value = snap
    r._thinker = thinker
    bb = MagicMock()
    r.blackboard = bb
    import asyncio
    asyncio.run(r._write_final_result())
    bb.set_final_response.assert_not_called()


def test_write_final_result_supervisor(monkeypatch):
    r = _final_runner(tier="supervisor")
    snap = MagicMock()
    snap.final_result = "发现"
    cd = MagicMock()
    cd.result_summary = "发现"
    snap.control_decision = cd
    thinker = MagicMock()
    thinker.get_process_snapshot.return_value = snap
    r._thinker = thinker
    bb = MagicMock()
    bb.write_expert_finding.return_value = "f1"
    r.blackboard = bb
    import asyncio
    asyncio.run(r._write_final_result())
    bb.write_expert_finding.assert_called_once()


def test_write_final_result_expert(monkeypatch):
    r = _final_runner(tier="expert")
    snap = MagicMock()
    snap.final_result = "观察内容"
    snap.control_decision = None
    thinker = MagicMock()
    thinker.get_process_snapshot.return_value = snap
    r._thinker = thinker
    bb = MagicMock()
    r.blackboard = bb
    import modules.thinking.api_stream as stream_mod
    monkeypatch.setattr(stream_mod, "_post_task_extraction_helper", AsyncMock())
    import asyncio
    asyncio.run(r._write_final_result())
    bb.add_observation.assert_called_once()


def test_notify_thinking_complete(monkeypatch):
    r = _runner()
    r._task_id = "t1"
    import modules.thinking.communication.interface as iface_mod
    bus = MagicMock()
    sent = []
    async def fake_send(msg):
        sent.append(msg)
    bus.send = fake_send
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)
    import asyncio
    asyncio.run(r._notify_thinking_complete())
    assert len(sent) == 1
    assert sent[0].content["action"] == "thinking_complete"


def _tool_runner(monkeypatch, tier="large"):
    r = _runner(tier=tier)
    r._visible_tool_whitelist = lambda: ["tool_a"]
    r.GENERATE_RETRIES = 1
    r.MAX_CHAT_TOOL_TURNS = 25
    r._react_loop = None
    r._status = ""
    r._status_detail = ""
    r._current_streaming_content = ""
    r._thinker = MagicMock()
    r._last_known_mode = "edit"
    r._push_reasoning = MagicMock()

    import infra.mcp.factory as mcp_mod
    mcp = MagicMock()
    mcp.get_tools_for_api.return_value = [{"function": {"name": "tool_a", "description": "工具"}}]
    monkeypatch.setattr(mcp_mod, "get_mcp_tool_service", lambda: mcp)
    import modules.security_system.tool_permission_controller as tpc
    ctrl = MagicMock()
    ctrl.get_control_tools.return_value = [{"function": {"name": "continue_thinking", "description": "控制"}}]
    monkeypatch.setattr(tpc, "get_tool_permission_controller", lambda: ctrl)
    return r


def _resp(content=None, tool_calls=None):
    class TC:
        def __init__(self, name, arguments="{}"):
            self.name = name
            self.arguments = arguments
            self.id = "tc1"
    msg = MagicMock()
    msg.message.content = content
    msg.message.tool_calls = tool_calls
    msg.message.reasoning_content = ""
    return msg


def test_generate_with_tools_control_roundtrip(monkeypatch):
    r = _tool_runner(monkeypatch, tier="large")
    client = MagicMock()
    delattr(client, 'chat_stream')
    first = _resp(content=None, tool_calls=[_resp.TC if hasattr(_resp, "TC") else type("TC", (), {"name": "continue_thinking", "arguments": '{"continue": true, "wait_seconds": 2}', "id": "t1"})()])
    first.message.tool_calls = [type("TC", (), {"name": "continue_thinking", "arguments": '{"continue": true, "wait_seconds": 2}', "id": "t1"})()]
    second = _resp(content="工具循环完成")
    client.chat = AsyncMock(side_effect=[first, second])
    r.instance.client = client
    import asyncio
    out = asyncio.run(r._generate_with_tools("s", "u", client))
    assert "思考控制" in out  # continue=true → 控制摘要返回
    assert client.chat.await_count == 1
    r._thinker.record_control_decision.assert_called_once_with({"continue": True, "wait_seconds": 2})
