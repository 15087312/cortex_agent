"""api_stream 补测：连接管理边界 / think 全分支 / WS/SSE 全流程 / 路由错误分支"""
import asyncio
import json
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocketDisconnect

import modules.thinking.api_stream as stream
from modules.thinking.api_stream import (
    StreamThinkingSystem,
    ConnectionManager,
    _build_event,
    _safe_think,
    _post_task_extraction_helper,
    _stream_sse,
    initialize_system,
)


def _system():
    s = StreamThinkingSystem.__new__(StreamThinkingSystem)
    s.sessions = {}
    s._running = False
    s._lock = asyncio.Lock()
    s._orchestrator = MagicMock()
    s._session_repo = None
    return s


def _patch_think_deps(monkeypatch, s):
    """think 外部依赖全部 mock"""
    monkeypatch.setattr(stream, "connection_manager", MagicMock())
    stream.connection_manager.send_json = AsyncMock()
    monkeypatch.setattr("modules.thinking.communication.get_message_bus", lambda: MagicMock())
    monkeypatch.setattr("modules.security_system.tool_security_gate.set_security_event_callback", lambda cb: None)
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: True)
    monkeypatch.setattr("modules.thinking.session_graph.get_session_graph_store", lambda: MagicMock())
    repo = MagicMock()
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    return repo


def _emitted(s):
    """从 _emit.call_args_list 提取 envelope（args[1]）"""
    return [c.args[1] for c in s._emit.call_args_list if len(c.args) >= 2]


# ── ConnectionManager 边界 ─────────────────────────────────────────────

async def test_connect_replaces_old(monkeypatch):
    cm = ConnectionManager()
    old_ws = AsyncMock()
    new_ws = AsyncMock()
    await cm.connect("s1", old_ws)
    await cm.connect("s1", new_ws)
    assert cm.active_connections["s1"] is new_ws
    # fire-and-forget 关闭旧连接
    await asyncio.sleep(0.05)
    assert old_ws.close.await_count >= 1


async def test_close_old_connection_ok():
    cm = ConnectionManager()
    old = AsyncMock()
    await cm._close_old_connection(old)
    old.close.assert_awaited_once()


async def test_close_old_connection_error():
    cm = ConnectionManager()
    old = AsyncMock()
    old.close = AsyncMock(side_effect=RuntimeError("down"))
    await cm._close_old_connection(old)  # 不抛异常


async def test_send_json_from_thread_future_error(monkeypatch):
    cm = ConnectionManager()
    loop = asyncio.new_event_loop()
    cm._loop = loop
    cm.active_connections["s1"] = AsyncMock()
    loop2 = MagicMock()
    loop2.run_coroutine_threadsafe = MagicMock(side_effect=RuntimeError("loop dead"))
    monkeypatch.setattr(stream.asyncio, "run_coroutine_threadsafe", loop2.run_coroutine_threadsafe)
    assert cm.send_json_from_thread("s1", {}) is False
    loop.close()


async def test_send_json_from_thread_send_error():
    cm = ConnectionManager()
    ws = AsyncMock()
    ws.send_json = AsyncMock(side_effect=RuntimeError("closed"))
    await cm.connect("s1", ws)
    ok = cm.send_json_from_thread("s1", {"msg": "x"})
    assert ok is True  # fire-and-forget
    await asyncio.sleep(0.05)
    assert "s1" not in cm.active_connections


async def test_broadcast_empty():
    cm = ConnectionManager()
    await cm.broadcast({"x": 1})  # 无连接不抛异常


# ── _resolve_identity_name 反向匹配 / 异常 ─────────────────────────────

def test_resolve_identity_name_reverse_match(monkeypatch):
    stream._identity_name_cache.clear()
    monkeypatch.setattr("modules.thinking.identity.get_identities", lambda: {
        "code_supervisor": {"name": "代码主管", "model_id": "supervisor_code"},
    })
    assert stream._resolve_identity_name("supervisor_code_001") == "代码主管"


def test_resolve_identity_name_reverse_no_match(monkeypatch):
    stream._identity_name_cache.clear()
    monkeypatch.setattr("modules.thinking.identity.get_identities", lambda: {
        "a": {"name": "A", "model_id": "xxx"},
    })
    assert stream._resolve_identity_name("supervisor_code_001") == ""


def test_resolve_identity_name_exception(monkeypatch):
    stream._identity_name_cache.clear()
    monkeypatch.setattr("modules.thinking.identity.get_identities",
                        MagicMock(side_effect=RuntimeError("ident down")))
    assert stream._resolve_identity_name("supervisor_code_001") == ""


# ── 会话生命周期 ───────────────────────────────────────────────────────

async def test_get_session_repo_fail(monkeypatch):
    s = _system()
    monkeypatch.setattr("modules.database.session_repo.get_session_repo",
                        MagicMock(side_effect=RuntimeError("db down")))
    assert s._get_session_repo() is None


async def test_start_history_restore_error(monkeypatch):
    s = _system()
    repo = MagicMock()
    repo.get_recent_messages = MagicMock(side_effect=RuntimeError("read fail"))
    repo.create_session = MagicMock()
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    monkeypatch.setattr(s, "_preload_session_memories", AsyncMock())
    await s.start("s1")  # 恢复失败静默
    assert "s1" in s.sessions


async def test_start_create_session_error(monkeypatch):
    s = _system()
    repo = MagicMock()
    repo.get_recent_messages = MagicMock(return_value=[])
    repo.create_session = MagicMock(side_effect=RuntimeError("create fail"))
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    monkeypatch.setattr(s, "_preload_session_memories", AsyncMock())
    await s.start("s1")  # 创建记录失败静默


async def test_stop_cancels_task():
    s = _system()
    task = asyncio.create_task(asyncio.sleep(30))
    s.sessions["s1"] = {"running": True, "scheduler_task": task}
    await s.stop("s1")
    assert s.sessions["s1"]["running"] is False
    task.cancel()


async def test_append_message_save_error(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"messages": []}
    repo = MagicMock()
    repo.save_message = MagicMock(side_effect=RuntimeError("save fail"))
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    assert await s._append_message("s1", "assistant", "x") == ""


async def test_append_message_backfills_id(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"messages": []}
    repo = MagicMock()
    repo.save_message = MagicMock(return_value="mid")
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    mid = await s._append_message("s1", "assistant", "x")
    assert mid == "mid"
    assert s.sessions["s1"]["messages"][0]["id"] == "mid"


async def test_persist_thought_error(monkeypatch):
    s = _system()
    repo = MagicMock()
    repo.save_message = MagicMock(side_effect=RuntimeError("save fail"))
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    await s._persist_thought("s1", "x")  # 不抛异常


async def test_proactive_context_trim_settings_fail(monkeypatch):
    from types import SimpleNamespace
    s = _system()
    s.sessions["s1"] = {"messages": [{"content": f"m{i}"} for i in range(8)]}
    engine = MagicMock()
    engine.estimate_tokens = MagicMock(return_value=999999)
    monkeypatch.setattr("modules.thinking.context.compression.get_compression_engine", lambda: engine)
    # 无 CONTEXT_WINDOW_SIZE 属性 → 走 except 默认 128000
    import sys
    cfg_mod = sys.modules["config.settings"]
    monkeypatch.setattr(cfg_mod, "settings", SimpleNamespace())
    await s._proactive_context_trim("s1")
    assert len(s.sessions["s1"]["messages"]) == 4


async def test_emit_send_error(monkeypatch):
    s = _system()
    cm = MagicMock()
    cm.send_json = AsyncMock(side_effect=RuntimeError("ws down"))
    monkeypatch.setattr(stream, "connection_manager", cm)
    await s._emit("s1", {"type": "x"})  # 不抛异常


# ── _format_scheduler_event 深分支 ─────────────────────────────────────

def _fmt(event):
    s = _system()
    return s._format_scheduler_event(event)


def test_format_expert_parent_and_truncate(monkeypatch):
    monkeypatch.setattr("modules.thinking.identity.get_identities", lambda: {
        "large": {"name": "总指挥", "model_id": "large_primary"},
        "sup": {"name": "主管", "model_id": "pm_1"},
    })
    event = {
        "type": "model_comm",
        "payload": {
            "msg_type": "broadcast", "sender": "s", "recipient": "t",
            "content": {"content": "x" * 3000, "entry_type": "response", "round": 2,
                        "model_id": "ex_1", "metadata": {"return_to_model_id": "pm_1"}},
            "metadata": {"dialog_id": "d1", "tier": "expert"},
        },
    }
    out = _fmt(event)
    assert len(out["content"]) <= 2100
    assert out["data"]["identity_name"]


def test_format_broadcast_unknown_identity(monkeypatch):
    stream._identity_name_cache.clear()
    monkeypatch.setattr("modules.thinking.identity.get_identities", lambda: {})
    event = {
        "type": "model_comm",
        "payload": {
            "msg_type": "broadcast",
            "content": {"content": "内容", "entry_type": "thought", "round": 1},
            "metadata": {"dialog_id": "d1", "tier": "supervisor"},
        },
    }
    out = _fmt(event)
    assert out["data"]["dialog_tier"] == "supervisor"
    assert out["data"]["identity_name"]


def test_format_preliminary_non_dict(monkeypatch):
    event = {
        "type": "model_comm",
        "payload": {
            "msg_type": "broadcast", "sender": "s", "recipient": "t",
            "content": "初步结果", "metadata": {"event": "preliminary_response"},
        },
    }
    out = _fmt(event)
    assert "[preliminary] 初步结果" == out["content"]


def test_format_model_comm_with_detail():
    event = {
        "type": "model_comm",
        "payload": {"msg_type": "query", "sender": "s", "recipient": "t",
                    "action": "ask", "detail": "非常长的详情" * 20},
    }
    out = _fmt(event)
    assert "非常长的详情" in out["content"]


def test_format_security_with_duration_and_approval(monkeypatch):
    logs = []
    monkeypatch.setattr(stream.logger, "info", lambda msg, *a, **k: logs.append(str(msg)))
    out = _fmt({"type": "security", "target": "t", "action": "等待用户审批",
                "payload": {"detail": "d", "duration_ms": 123, "request_id": "r1"}})
    assert "123ms" in out["content"]
    assert any("r1" in l for l in logs)


def test_format_security_no_detail():
    out = _fmt({"type": "security", "target": "t", "action": "审查", "payload": {}})
    assert out["content"]


# ── think 全分支 ───────────────────────────────────────────────────────

async def test_think_starts_new_session(monkeypatch):
    s = _system()
    s.start = AsyncMock()
    s._is_processing = AsyncMock(return_value=True)
    s._append_message = AsyncMock(return_value="m")
    s._emit = AsyncMock()
    assert await s.think("new_sid", "输入") == ""
    s.start.assert_awaited_once()


async def test_think_busy_append_error(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"processing": True, "messages": []}
    s._is_processing = AsyncMock(return_value=True)
    s._append_message = AsyncMock(side_effect=RuntimeError("save fail"))
    s._emit = AsyncMock()
    assert await s.think("s1", "输入") == ""


async def test_think_handshake_exception(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"processing": False, "messages": [], "started_at": 0}
    s._is_processing = AsyncMock(return_value=False)
    s._append_message = AsyncMock(return_value="uid")
    s._emit = AsyncMock()
    _patch_think_deps(monkeypatch, s)

    def boom(sid):
        raise RuntimeError("handshake down")

    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", boom)
    out = await s.think("s1", "你好")
    assert out == ""  # 握手异常降级继续


async def test_think_connection_lost(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"processing": False, "messages": [], "started_at": 0}
    s._is_processing = AsyncMock(return_value=False)
    s._append_message = AsyncMock(return_value="uid")
    s._emit = AsyncMock()
    _patch_think_deps(monkeypatch, s)
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: False)
    out = await s.think("s1", "你好")
    assert out == ""
    emitted = _emitted(s)
    assert any(e["event"] == "connection_lost" for e in emitted)


async def test_think_message_bus_error(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"processing": False, "messages": [], "started_at": 0}
    s._is_processing = AsyncMock(return_value=False)
    s._append_message = AsyncMock(return_value="uid")
    s._emit = AsyncMock()
    s.get_context = MagicMock(return_value=[])
    s._post_task_extraction = AsyncMock()
    _patch_think_deps(monkeypatch, s)
    monkeypatch.setattr("modules.thinking.communication.get_message_bus",
                        MagicMock(side_effect=RuntimeError("bus down")))
    monkeypatch.setattr("modules.security_system.tool_security_gate.set_security_event_callback",
                        MagicMock(side_effect=RuntimeError("gate down")))
    s._orchestrator.process = AsyncMock(return_value={
        "module_results": [], "decisions": {}, "response": "ok"})
    out = await s.think("s1", "你好")
    assert out == "ok"


async def test_think_module_results_thinking_history(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"processing": False, "messages": [], "started_at": 0, "model_id": "large_primary"}
    s._is_processing = AsyncMock(return_value=False)
    s._append_message = AsyncMock(return_value="uid")
    s._proactive_context_trim = AsyncMock()
    s._emit = AsyncMock()
    s._post_task_extraction = AsyncMock()
    s.get_context = MagicMock(return_value=[{"role": "user", "content": "你好"}])
    s._persist_thought = AsyncMock()
    _patch_think_deps(monkeypatch, s)

    async def real_append(session_id, role, content, tier=""):
        s.sessions[session_id]["messages"].append(
            {"role": role, "content": content, "id": "mid_" + role, "timestamp": 0})
        return "mid_" + role

    s._append_message = real_append

    async def fake_process(user_input, context, short_term, callback, session_id, model_id=None):
        return {
            "module_results": [
                {"module": "thinking", "success": True,
                 "output": {"thinking_history": [
                     {"type": "plan", "content": "步骤一", "model": "large"},
                     {"type": "action", "content": "", "model": "large"},  # 空内容跳过
                 ]},
                 "latency_ms": 10},
                {"module": "sub", "success": True,
                 "output": {"sub_sessions": [{"id": 1}]}},
            ],
            "decisions": {"probe_signals": [{"signal": "需要深挖"}]},
            "response": "最终回答",
            "trace_id": "t1",
            "elapsed_ms": 5,
            "active_modules": ["thinking"],
            "focus": "x",
        }

    s._orchestrator.process = fake_process
    out = await s.think("s1", "你好")
    assert out == "最终回答"
    emitted = _emitted(s)
    types = [e["type"] for e in emitted]
    assert "thinking" in types
    assert "message" in types
    assert "done" in types


async def test_think_output_mode_silent(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"processing": False, "messages": [], "started_at": 0, "model_id": "large_primary"}
    s._is_processing = AsyncMock(return_value=False)
    s._append_message = AsyncMock(return_value="uid")
    s._proactive_context_trim = AsyncMock()
    s._emit = AsyncMock()
    s._post_task_extraction = AsyncMock()
    s.get_context = MagicMock(return_value=[])
    s._persist_thought = AsyncMock()
    _patch_think_deps(monkeypatch, s)
    s._orchestrator.process = AsyncMock(return_value={
        "module_results": [], "decisions": {}, "response": "前缀【输出模式】silent"})
    out = await s.think("s1", "你好")
    assert out == "前缀"
    emitted = _emitted(s)
    assert any(e["event"] == "silent_thinking" for e in emitted)


async def test_think_cancelled(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"processing": False, "messages": [], "started_at": 0}
    s._is_processing = AsyncMock(return_value=False)
    s._append_message = AsyncMock(return_value="uid")
    s._emit = AsyncMock()
    s._post_task_extraction = AsyncMock()
    s.get_context = MagicMock(return_value=[])
    _patch_think_deps(monkeypatch, s)
    import modules.thinking.core.model_runner as mr_mod
    rm = MagicMock()
    rm.blackboard.final_response = "部分输出"
    monkeypatch.setattr(mr_mod, "_runner_managers", {"s1": rm})
    monkeypatch.setattr(mr_mod, "_runner_managers_lock", threading.Lock())

    async def cancelling_process(*a, **k):
        raise asyncio.CancelledError()

    s._orchestrator.process = cancelling_process
    out = await s.think("s1", "你好")
    assert out == "stopped"
    emitted = _emitted(s)
    assert any(e["event"] == "stopped" for e in emitted)


async def test_think_streams_stage_events(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"processing": False, "messages": [], "started_at": 0, "model_id": "large_primary"}
    s._is_processing = AsyncMock(return_value=False)
    s._append_message = AsyncMock(return_value="uid")
    s._proactive_context_trim = AsyncMock()
    s._emit = AsyncMock()
    s._post_task_extraction = AsyncMock()
    s.get_context = MagicMock(return_value=[])
    s._persist_thought = AsyncMock()
    store = MagicMock()
    store.record = MagicMock()
    _patch_think_deps(monkeypatch, s)
    monkeypatch.setattr("modules.thinking.session_graph.get_session_graph_store", lambda: store)

    async def fake_process(user_input, context, short_term, callback, session_id, model_id=None):
        callback({"type": "tool_call", "target": "web_search", "action": "run", "success": True})
        callback({"type": "model_comm", "source": "s", "target": "t", "payload": {
            "msg_type": "broadcast", "sender": "m", "recipient": "b",
            "content": {"content": "对话内容", "entry_type": "response", "round": 1,
                        "model_id": "large_primary", "metadata": {}},
            "metadata": {"dialog_id": "d1", "tier": "large"},
        }})
        callback({"type": "model_comm", "payload": {"msg_type": "broadcast",
                  "content": {"content": "   ", "entry_type": "thought"},
                  "metadata": {"dialog_id": "d1"}}})  # 空内容 → formatted None → continue
        return {"module_results": [], "decisions": {}, "response": "ok"}

    s._orchestrator.process = fake_process
    out = await s.think("s1", "你好")
    assert out == "ok"
    emitted = _emitted(s)
    assert any(e["event"] == "thinking_step" for e in emitted)
    store.record.assert_called_once()


async def test_think_runner_status_collection(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"processing": False, "messages": [], "started_at": 0, "model_id": "large_primary"}
    s._is_processing = AsyncMock(return_value=False)
    s._append_message = AsyncMock(return_value="uid")
    s._proactive_context_trim = AsyncMock()
    s._emit = AsyncMock()
    s._post_task_extraction = AsyncMock()
    s.get_context = MagicMock(return_value=[])
    s._persist_thought = AsyncMock()
    _patch_think_deps(monkeypatch, s)
    import modules.thinking.core.model_runner as mr_mod
    runners = [
        {"tier": "large", "running": True, "name": "总指挥", "role": "large",
         "model_id": "large_primary", "context_tokens": 10, "context_window_size": 1000,
         "active_skill": "sk", "status": "s", "status_detail": "d", "round": 1,
         "max_turns": 5, "react_loop": False, "think_loop": True, "last_thought": "t"},
        {"tier": "supervisor", "running": True, "name": "主管", "role": "sup",
         "model_id": "pm_1", "status": "s"},
        {"tier": "expert", "running": True, "name": "专家", "role": "exp",
         "model_id": "ex_1", "supervisor": "pm_1", "status": "s"},
        {"tier": "large", "running": False, "name": "空闲", "role": "large", "model_id": "x"},
    ]
    rm = MagicMock()
    rm.list_runners.return_value = runners
    monkeypatch.setattr(mr_mod, "_runner_managers", {"s1": rm})
    monkeypatch.setattr(mr_mod, "_runner_managers_lock", threading.Lock())

    async def slow_process(*a, **k):
        await asyncio.sleep(1.2)
        return {"module_results": [], "decisions": {}, "response": "ok"}

    s._orchestrator.process = slow_process
    out = await s.think("s1", "你好")
    assert out == "ok"
    emitted = _emitted(s)
    progress = [e for e in emitted if e["event"] == "thinking_progress"]
    assert progress
    data = progress[0]["data"]
    assert data["active_experts"][0]["supervisor"] == "pm_1"
    assert data["context_tokens"] == 10
    assert data["context_window_size"] == 1000


# ── _safe_think ────────────────────────────────────────────────────────

async def test_safe_think_cancelled_rethrow(monkeypatch):
    s = _system()

    async def boom(sid, inp, callback=None):
        raise asyncio.CancelledError()

    s.think = boom
    with pytest.raises(asyncio.CancelledError):
        await _safe_think(s, "s1", "输入")


async def test_safe_think_error_append_fail(monkeypatch):
    s = _system()

    async def boom(sid, inp, callback=None):
        raise RuntimeError("调度崩了")

    s.think = boom
    s._append_message = AsyncMock(side_effect=RuntimeError("save fail"))
    s._emit = AsyncMock()
    await _safe_think(s, "s1", "输入")  # 兜底也失败 → 静默


async def test_safe_think_error_emit_fail(monkeypatch):
    s = _system()

    async def boom(sid, inp, callback=None):
        raise RuntimeError("调度崩了")

    s.think = boom
    s._append_message = AsyncMock()
    s._emit = AsyncMock(side_effect=RuntimeError("emit fail"))
    await _safe_think(s, "s1", "输入")  # emit 失败 → 静默


# ── _post_task_extraction ──────────────────────────────────────────────

async def test_post_task_extraction_no_session():
    s = _system()
    s.sessions = {}
    await s._post_task_extraction("nope", "u", "r", owner_id="x")  # 直接返回


async def test_post_task_extraction_hash_dup(monkeypatch):
    import modules.memory.event_reducer as er_mod
    from config.settings import settings as _cfg
    monkeypatch.setattr(_cfg, "MEMORY_SUMMARY_ENABLED", True)
    reducer = AsyncMock()
    monkeypatch.setattr(er_mod, "EventReducer", lambda **kw: reducer)
    s = _system()
    s.sessions = {"s1": {"messages": [
        {"role": "user", "content": "这是一个足够长的用户提问内容测试"},
        {"role": "assistant::large_primary", "content": "这是一个足够长的模型回复内容测试"},
    ], "_processed_hashes": set()}}
    s.logger = MagicMock()
    await s._post_task_extraction("s1", "u", "r", owner_id="x")
    await s._post_task_extraction("s1", "u", "r", owner_id="x")  # hash 去重
    assert reducer.reduce.await_count == 1


async def test_post_task_extraction_infer_owner(monkeypatch):
    import modules.memory.event_reducer as er_mod
    from config.settings import settings as _cfg
    monkeypatch.setattr(_cfg, "MEMORY_SUMMARY_ENABLED", True)
    reducer = AsyncMock()
    reducer.reduce.return_value = {"events": [1]}
    monkeypatch.setattr(er_mod, "EventReducer", lambda **kw: reducer)
    s = _system()
    s.sessions = {"s1": {"messages": [
        {"role": "user", "content": "这个用户消息足够长，用于测试自动推断"},
        {"role": "supervisor_code_001", "content": "这个模型消息足够长，用于测试自动推断"},
        {"role": "assistant::large_primary", "content": "这个模型消息足够长，用于测试自动推断"},
    ], "_processed_hashes": set()}}
    s.logger = MagicMock()
    async def noop_sleep(*a, **k):
        return None
    monkeypatch.setattr(stream.asyncio, "sleep", noop_sleep)
    await s._post_task_extraction("s1", "u", "r")  # 无 owner_id → 自动推断
    assert reducer.reduce.await_count == 1


async def test_post_task_extraction_model_client_fail(monkeypatch):
    import modules.memory.event_reducer as er_mod
    from config.settings import settings as _cfg
    monkeypatch.setattr(_cfg, "MEMORY_SUMMARY_ENABLED", True)
    reducer = AsyncMock()
    reducer.reduce.return_value = []
    monkeypatch.setattr(er_mod, "EventReducer", lambda **kw: reducer)
    monkeypatch.setattr("infra.model.small_model_client.SmallModelClient",
                        MagicMock(side_effect=RuntimeError("no client")))
    s = _system()
    s.sessions = {"s1": {"messages": [
        {"role": "user", "content": "足够长的用户消息内容用于测试"},
        {"role": "assistant", "content": "足够长的助手消息内容用于测试"},
    ], "_processed_hashes": set()}}
    s.logger = MagicMock()
    await s._post_task_extraction("s1", "u", "r", owner_id="x")  # 客户端创建失败不崩


async def test_post_task_extraction_reducer_error(monkeypatch):
    import modules.memory.event_reducer as er_mod
    from config.settings import settings as _cfg
    monkeypatch.setattr(_cfg, "MEMORY_SUMMARY_ENABLED", True)
    reducer = AsyncMock()
    reducer.reduce.side_effect = RuntimeError("reduce fail")
    monkeypatch.setattr(er_mod, "EventReducer", lambda **kw: reducer)
    s = _system()
    s.sessions = {"s1": {"messages": [
        {"role": "user", "content": "足够长的用户消息内容用于测试"},
        {"role": "assistant", "content": "足够长的助手消息内容用于测试"},
    ], "_processed_hashes": set()}}
    s.logger = MagicMock()
    await s._post_task_extraction("s1", "u", "r", owner_id="x")  # 提取异常静默


# ── 初始化 / 辅助 ──────────────────────────────────────────────────────

async def test_initialize_system_sets_loop(monkeypatch):
    monkeypatch.setattr(stream, "_thinking_system", None)
    monkeypatch.setattr(stream, "_main_event_loop", None)
    sys = await initialize_system()
    assert sys is not None
    assert stream._main_event_loop is not None


async def test_post_task_extraction_helper(monkeypatch):
    s = _system()
    s._post_task_extraction = AsyncMock()
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    await _post_task_extraction_helper("s1", "u", "r", "owner")
    s._post_task_extraction.assert_awaited_once()


# ── SSE 流 ─────────────────────────────────────────────────────────────

async def test_stream_sse_flow(monkeypatch):
    s = _system()
    s.start = AsyncMock()
    events_seen = []

    async def fake_safe_think(system, sid, q, callback=None):
        await callback(_build_event(session_id=sid, msg_type="message", event="assistant_message",
                                    content="回答", role="main"))

    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    monkeypatch.setattr(stream, "_safe_think", fake_safe_think)
    async for chunk in _stream_sse("s1", "问题"):
        events_seen.append(chunk)
        break
    assert events_seen[0]["event"] == "assistant_message"


async def test_stream_sse_error(monkeypatch):
    s = _system()
    s.start = AsyncMock()

    async def boom(system, sid, q, callback=None):
        raise RuntimeError("think fail")

    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    monkeypatch.setattr(stream, "_safe_think", boom)
    events = [e async for e in _stream_sse("s1", "问题")]
    assert any(e["event"] == "error" for e in events)


# ── 路由：错误 / 边界 ──────────────────────────────────────────────────

async def test_route_sse_requires_question():
    from api.errors import AppError
    with pytest.raises(AppError):
        await stream.sse_session_get("s1", "")


async def test_route_close_session_repo_error(monkeypatch):
    s = _system()
    s.stop = AsyncMock()
    s.sessions = {"s1": {}}
    repo = MagicMock()
    repo.delete_session = MagicMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.close_session("s1")
    assert out["success"] is True


async def test_route_update_title_empty():
    from api.errors import AppError
    with pytest.raises(AppError):
        await stream.update_session_title("s1", {"title": " "})


async def test_route_update_title_repo_error(monkeypatch):
    s = _system()
    repo = MagicMock()
    repo.set_session_title = MagicMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.update_session_title("s1", {"title": "新"})
    assert out["success"] is True


async def test_route_delete_message_repo_error(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"messages": [{"id": "m1"}]}
    repo = MagicMock()
    repo.delete_message = MagicMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.delete_message("s1", "m1")
    assert out["data"]["removed"] is True


async def test_route_update_message_empty():
    from api.errors import AppError
    with pytest.raises(AppError):
        await stream.update_message("s1", "m1", {"content": " "})


async def test_route_update_message_repo_error(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"messages": [{"id": "m1", "content": "旧"}]}
    repo = MagicMock()
    repo.update_message = MagicMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.update_message("s1", "m1", {"content": "新"})
    assert out["data"]["message"] == "消息已更新"


async def test_route_get_sessions_repo_errors(monkeypatch):
    s = _system()
    s.sessions = {}
    repo = MagicMock()
    repo.delete_empty_sessions = MagicMock(side_effect=RuntimeError("clean fail"))
    repo.get_all_sessions = MagicMock(side_effect=RuntimeError("query fail"))
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.get_sessions()
    assert out["success"] is True
    assert out["data"] == []


async def test_route_get_session_messages_no_repo(monkeypatch):
    s = _system()
    monkeypatch.setattr(s, "_get_session_repo", lambda: None)
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.get_session_messages("s1")
    assert out["data"] == []


async def test_route_get_session_messages_memory_fallback(monkeypatch):
    s = _system()
    repo = MagicMock()
    repo.get_messages = MagicMock(return_value=[])
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    s.sessions["s1"] = {"messages": [{"id": "m1", "role": "user", "content": "内存", "timestamp": 0}]}
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.get_session_messages("s1")
    assert out["data"][0]["content"] == "内存"


async def test_route_get_session_messages_memory_error(monkeypatch):
    s = _system()
    repo = MagicMock()
    repo.get_messages = MagicMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    s.sessions = {}

    class BoomDict(dict):
        def get(self, k, d=None):
            raise RuntimeError("mem down")

    monkeypatch.setattr(s, "sessions", BoomDict())
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.get_session_messages("s1")
    assert out["data"] == []


# ── websocket_chat 全流程 ──────────────────────────────────────────────

class FakeWS:
    def __init__(self, incoming=None):
        self.incoming = list(incoming or [])
        self.sent = []
        self.accepted = False
        self.closed = None

    async def accept(self):
        self.accepted = True

    async def send_json(self, data):
        self.sent.append(data)

    async def receive_text(self):
        if self.incoming:
            return self.incoming.pop(0)
        raise WebSocketDisconnect()

    async def close(self, code=1000, reason=""):
        self.closed = code


def _ws_env(monkeypatch):
    monkeypatch.setattr(stream, "_ws_auth_ok", lambda ws: True)
    s = _system()
    s.start = AsyncMock()
    s.stop = AsyncMock()
    s.think = AsyncMock(return_value="ok")
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    safe = AsyncMock()
    monkeypatch.setattr(stream, "_safe_think", safe)
    return s, safe


async def test_ws_auth_fail_closes():
    ws = FakeWS()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(stream, "_ws_auth_ok", lambda ws: False)
    try:
        await stream.websocket_chat(ws, "s1")
    finally:
        monkeypatch.undo()
    assert ws.closed == 4401


async def test_ws_flow_input_ping_stop_unsupported(monkeypatch):
    s, safe = _ws_env(monkeypatch)
    monkeypatch.setattr(stream.connection_manager, "active_connections", {})
    ws = FakeWS(incoming=[
        json.dumps({"type": "input", "content": "你好"}),
        json.dumps({"type": "ping"}),
        json.dumps({"type": "stop"}),
        json.dumps({"type": "unknown_type"}),
    ])
    await stream.websocket_chat(ws, "s1")
    await asyncio.sleep(0.05)
    safe.assert_called_once()
    events = [m["event"] for m in ws.sent]
    assert "pong" in events
    assert "stopped" in events
    assert "unsupported_type" in events
    s.start.assert_awaited()
    s.stop.assert_awaited_once()


async def test_ws_flow_attachment_error(monkeypatch):
    s, safe = _ws_env(monkeypatch)
    monkeypatch.setattr("modules.thinking.attachment_handler.validate_attachments", lambda a: "格式错误")
    ws = FakeWS(incoming=[json.dumps({"type": "input", "content": "看", "attachments": [{"type": "image"}]})])
    await stream.websocket_chat(ws, "s1")
    assert any(m["event"] == "attachment_error" for m in ws.sent)
    safe.assert_not_awaited()


async def test_ws_flow_attachment_direct(monkeypatch):
    s, safe = _ws_env(monkeypatch)
    monkeypatch.setattr("modules.thinking.attachment_handler.validate_attachments", lambda a: None)
    extract = MagicMock(return_value=["img"])
    summarize = MagicMock(return_value="图片描述")
    monkeypatch.setattr("modules.thinking.attachment_handler.extract_images", extract)
    monkeypatch.setattr("modules.thinking.attachment_handler.summarize_attachments", summarize)
    monkeypatch.setattr("modules.thinking.attachment_handler.parse_attachments", AsyncMock(return_value=""))
    from config.settings import settings
    monkeypatch.setattr(settings, "CHAT_IMAGE_MODE", "direct")
    ws = FakeWS(incoming=[json.dumps({"type": "input", "content": "看", "attachments": [{"type": "image"}]})])
    await stream.websocket_chat(ws, "s1")
    await asyncio.sleep(0.05)
    extract.assert_called_once()
    safe.assert_called_once()


async def test_ws_flow_attachment_parse_error(monkeypatch):
    s, safe = _ws_env(monkeypatch)
    monkeypatch.setattr("modules.thinking.attachment_handler.validate_attachments", lambda a: None)
    monkeypatch.setattr("modules.thinking.attachment_handler.extract_images",
                        MagicMock(side_effect=RuntimeError("extract fail")))
    monkeypatch.setattr("modules.thinking.attachment_handler.summarize_attachments",
                        MagicMock(side_effect=RuntimeError("summarize fail")))
    from config.settings import settings
    monkeypatch.setattr(settings, "CHAT_IMAGE_MODE", "direct")
    ws = FakeWS(incoming=[json.dumps({"type": "input", "content": "看", "attachments": [{"type": "image"}]})])
    await stream.websocket_chat(ws, "s1")
    await asyncio.sleep(0.05)
    safe.assert_called_once()


async def test_ws_flow_security_response(monkeypatch):
    s, safe = _ws_env(monkeypatch)
    gate = MagicMock()
    monkeypatch.setattr("modules.security_system.tool_security_gate.ToolSecurityGate", gate)
    ws = FakeWS(incoming=[json.dumps({"type": "security_response", "request_id": "r1", "approved": True})])
    await stream.websocket_chat(ws, "s1")
    gate.resolve_review.assert_called_once_with("r1", True, "")


async def test_ws_flow_security_response_error(monkeypatch):
    s, safe = _ws_env(monkeypatch)
    gate = MagicMock()
    gate.resolve_review = MagicMock(side_effect=RuntimeError("resolve fail"))
    monkeypatch.setattr("modules.security_system.tool_security_gate.ToolSecurityGate", gate)
    ws = FakeWS(incoming=[json.dumps({"type": "security_response", "request_id": "r1"})])
    await stream.websocket_chat(ws, "s1")  # 错误被捕获，连接正常断开


async def test_ws_flow_interactive_response(monkeypatch):
    s, safe = _ws_env(monkeypatch)
    import modules.thinking.core.model_runner as mr_mod
    mgr = MagicMock()
    mgr.resolve_user_response = MagicMock(return_value=True)
    monkeypatch.setattr(mr_mod, "_runner_managers", {"s1": mgr})
    monkeypatch.setattr(mr_mod, "_runner_managers_lock", threading.Lock())
    ws = FakeWS(incoming=[json.dumps({"type": "interactive_response", "request_id": "r1", "answer": "是"})])
    await stream.websocket_chat(ws, "s1")
    mgr.resolve_user_response.assert_called_once()


async def test_ws_flow_interactive_response_not_found(monkeypatch):
    s, safe = _ws_env(monkeypatch)
    import modules.thinking.core.model_runner as mr_mod
    mgr = MagicMock()
    mgr.resolve_user_response = MagicMock(return_value=False)
    monkeypatch.setattr(mr_mod, "_runner_managers", {"s1": mgr})
    monkeypatch.setattr(mr_mod, "_runner_managers_lock", threading.Lock())
    ws = FakeWS(incoming=[json.dumps({"type": "interactive_response", "request_id": "r1"})])
    await stream.websocket_chat(ws, "s1")  # 未找到 → 警告，不崩溃


async def test_ws_flow_non_json(monkeypatch):
    s, safe = _ws_env(monkeypatch)
    ws = FakeWS(incoming=["裸文本消息"])
    await stream.websocket_chat(ws, "s1")
    await asyncio.sleep(0.05)
    safe.assert_called_once()
