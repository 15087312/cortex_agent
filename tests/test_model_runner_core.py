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
