"""api_stream 扩展测试：会话管理 / 事件格式化 / think 流程 / 路由"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import modules.thinking.api_stream as stream
from modules.thinking.api_stream import (
    StreamThinkingSystem,
    ConnectionManager,
    get_thinking_system,
    initialize_system,
    _build_event,
    _safe_think,
    _ws_auth_ok,
    _thinking_system,
)


def _system():
    s = StreamThinkingSystem.__new__(StreamThinkingSystem)
    s.sessions = {}
    s._running = False
    s._lock = asyncio.Lock()
    s._orchestrator = MagicMock()
    s._session_repo = None
    return s


# ── 会话生命周期 ───────────────────────────────────────────────────────

async def test_create_session_and_start(monkeypatch):
    s = _system()
    monkeypatch.setattr(s, "_get_session_repo", lambda: None)
    s.start = AsyncMock(wraps=s.start)
    sid = await s.create_session()
    assert sid
    s.start.assert_awaited_once()


async def test_start_new_session_restores_history(monkeypatch):
    s = _system()
    repo = MagicMock()
    repo.get_recent_messages = MagicMock(return_value=[
        {"role": "user", "content": "历史", "id": "m1"},
        {"role": "thought", "content": "思考", "id": "m2"},
    ])
    repo.create_session = MagicMock()
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    monkeypatch.setattr(s, "_preload_session_memories", AsyncMock())
    await s.start("s1")
    assert "s1" in s.sessions
    assert s.sessions["s1"]["running"] is True
    # thought 被过滤
    assert len(s.sessions["s1"]["messages"]) == 1
    assert s.sessions["s1"]["messages"][0]["role"] == "user"


async def test_start_existing_session(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"running": False, "messages": []}
    monkeypatch.setattr(s, "_get_session_repo", lambda: None)
    monkeypatch.setattr(s, "_preload_session_memories", AsyncMock())
    await s.start("s1")
    assert s.sessions["s1"]["running"] is True


async def test_stop_session_and_all():
    s = _system()
    task = asyncio.create_task(asyncio.sleep(10))
    s.sessions["s1"] = {"running": True, "scheduler_task": task}
    s.sessions["s2"] = {"running": True}
    await s.stop("s1")
    assert s.sessions["s1"]["running"] is False
    await s.stop()
    assert s._running is False


# ── 消息追加 ───────────────────────────────────────────────────────────

async def test_append_message_dedup_and_persist(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"messages": [{"role": "user", "content": "重复", "id": "m1"}]}
    repo = MagicMock()
    repo.save_message = MagicMock(return_value="new_id")
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    assert await s._append_message("s1", "user", "重复") == "m1"  # 去重
    mid = await s._append_message("s1", "assistant", "回复")
    assert mid == "new_id"
    assert s.sessions["s1"]["messages"][-1]["id"] == "new_id"


async def test_append_message_no_session():
    s = _system()
    assert await s._append_message("nope", "user", "x") == ""


async def test_persist_thought(monkeypatch):
    s = _system()
    repo = MagicMock()
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    await s._persist_thought("s1", "思考", tier="large")
    repo.save_message.assert_called_once()
    s2 = _system()
    monkeypatch.setattr(s2, "_get_session_repo", lambda: None)
    await s2._persist_thought("s1", "x")  # 无 repo 直接返回


# ── token 预算截断（纯读取视图，不裁剪内存/DB） ─────────────────────────

async def test_budget_trim_over_budget(monkeypatch):
    s = _system()
    messages = [{"content": f"m{i}"} for i in range(8)]
    s.sessions["s1"] = {"messages": messages}
    engine = MagicMock()
    engine.estimate_tokens = MagicMock(return_value=999999)
    monkeypatch.setattr("modules.thinking.context.compression.get_compression_engine", lambda: engine)
    import sys, types
    cfg = sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(CONTEXT_WINDOW_SIZE=1000))
    kept = s._budget_trim(messages)
    assert len(kept) == 1          # 单条即超预算 → 至少保留最新 1 条
    assert kept[-1]["content"] == "m7"  # 保留的是最新消息
    # 关键：内存不被修改（旧实现破坏性裁剪，导致内存与 DB 分叉）
    assert len(s.sessions["s1"]["messages"]) == 8


async def test_budget_trim_under_threshold(monkeypatch):
    s = _system()
    messages = [{"content": "a"} for _ in range(5)]
    engine = MagicMock()
    engine.estimate_tokens = MagicMock(return_value=10)
    monkeypatch.setattr("modules.thinking.context.compression.get_compression_engine", lambda: engine)
    import sys, types
    cfg = sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(CONTEXT_WINDOW_SIZE=1000))
    kept = s._budget_trim(messages)
    assert len(kept) == 5


async def test_budget_trim_empty():
    s = _system()
    assert s._budget_trim([]) == []


# ── _emit / processing 状态 ────────────────────────────────────────────

async def test_emit_with_callback(monkeypatch):
    s = _system()
    cb = AsyncMock()
    monkeypatch.setattr(stream, "connection_manager", MagicMock())
    stream.connection_manager.send_json = AsyncMock()
    await s._emit("s1", {"type": "x"}, cb)
    cb.assert_awaited_once()
    stream.connection_manager.send_json.assert_awaited_once()


async def test_set_and_is_processing():
    s = _system()
    s.sessions["s1"] = {}
    await s._set_processing("s1", True)
    assert await s._is_processing("s1") is True
    assert await s._is_processing("nope") is False


# ── _format_scheduler_event ────────────────────────────────────────────

def _fmt(event):
    s = _system()
    return s._format_scheduler_event(event)


def test_format_tool_call():
    out = _fmt({"type": "tool_call", "target": "web_search", "action": "run", "success": True})
    assert "工具" in out["content"]


def test_format_model_comm_broadcast_dialog():
    event = {
        "type": "model_comm", "source": "s", "target": "t",
        "payload": {
            "msg_type": "broadcast", "sender": "m", "recipient": "b",
            "content": {"content": "对话内容", "entry_type": "thought", "round": 3, "model_id": "large_primary"},
            "metadata": {"dialog_id": "d1", "tier": "large"},
        },
    }
    out = _fmt(event)
    assert out["content"]
    assert out["data"]["identity_name"]


def test_format_model_comm_empty_dialog_returns_none():
    event = {
        "type": "model_comm",
        "payload": {
            "msg_type": "broadcast",
            "content": "   ",
            "metadata": {"dialog_id": "d1"},
        },
    }
    assert _fmt(event) is None


def test_format_model_comm_preliminary():
    event = {
        "type": "model_comm",
        "payload": {
            "msg_type": "broadcast", "sender": "s", "recipient": "t",
            "content": "初步内容", "metadata": {"event": "preliminary_response"},
        },
    }
    out = _fmt(event)
    assert "[preliminary]" in out["content"]


def test_format_model_comm_other():
    event = {
        "type": "model_comm", "source": "s", "target": "t", "action": "a",
        "payload": {"msg_type": "query", "sender": "s", "recipient": "t"},
    }
    out = _fmt(event)
    assert "s" in out["content"]


def test_format_model_stage_module_security_scheduler():
    assert "模型阶段" in _fmt({"type": "model_stage", "action": "run", "target": "t"})["content"]
    assert "模块" in _fmt({"type": "module", "target": "thinking", "action": "done", "success": True})["content"]
    sec = _fmt({"type": "security", "target": "x", "action": "审批", "payload": {"detail": "详情"}})
    assert "安全审查" in sec["content"]
    assert "调度" in _fmt({"type": "scheduler", "action": "start"})["content"]


def test_format_default():
    out = _fmt({"type": "custom_type", "action": "act", "target": "tgt"})
    assert out["content"]


# ── get_context / get_status ───────────────────────────────────────────

def test_get_context_filters_thought(monkeypatch):
    """DB 无记录时走内存兜底，并过滤非对话 role"""
    s = _system()
    s.sessions["s1"] = {"messages": [{"role": "user", "content": "a"}, {"role": "thought", "content": "b"}]}
    monkeypatch.setattr(s, "_load_dialog_from_db", lambda sid: [])  # DB 无记录 → 内存兜底
    ctx = s.get_context("s1")
    assert [m["role"] for m in ctx] == ["user"]
    assert s.get_context("none") == []


def test_load_dialog_from_db_filters_non_dialog_roles(monkeypatch):
    """DB 为唯一真源：过滤 thought/process/mental，并把 created_at 转为 timestamp"""
    from modules.thinking.context.dialog_memory import load_dialog_from_db
    repo = MagicMock()
    repo.get_messages = MagicMock(return_value=[
        {"id": "m1", "role": "user", "content": "你好", "created_at": "2026-09-01T10:00:00"},
        {"id": "m2", "role": "thought", "content": "思考中", "created_at": "2026-09-01T10:00:01"},
        {"id": "m3", "role": "assistant", "content": "hi", "created_at": "2026-09-01T10:00:02"},
        {"id": "m4", "role": "process", "content": "流程", "created_at": "2026-09-01T10:00:03"},
    ])
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: repo)
    dialog = load_dialog_from_db("s1")
    assert [m["role"] for m in dialog] == ["user", "assistant"]
    assert dialog[0]["id"] == "m1"
    assert isinstance(dialog[0]["timestamp"], float) and dialog[0]["timestamp"] > 0


def test_get_status():
    s = _system()
    s.sessions = {"s1": {"running": True}, "s2": {"running": False}}
    st = s.get_status()
    assert st["running_sessions"] == 1


# ── think 流程 ─────────────────────────────────────────────────────────

async def test_think_busy(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"processing": True, "messages": []}
    monkeypatch.setattr(s, "_is_processing", AsyncMock(return_value=True))
    s._append_message = AsyncMock(return_value="m1")
    s._emit = AsyncMock()
    assert await s.think("s1", "输入") == ""
    s._emit.assert_awaited_once()


async def test_think_success(monkeypatch):
    s = _system()
    await s.start("s1") if "s1" in s.sessions else None
    s.sessions["s1"] = {"processing": False, "messages": [], "started_at": 0, "model_id": "large_primary"}
    s._is_processing = AsyncMock(return_value=False)
    s._append_message = AsyncMock(return_value="uid")
    s._proactive_context_trim = AsyncMock()
    s._emit = AsyncMock()
    s._post_task_extraction = AsyncMock()
    s.get_context = MagicMock(return_value=[{"role": "user", "content": "你好"}])
    s._persist_thought = AsyncMock()

    result = {"module_results": [], "decisions": {"probe_signals": []}, "response": "最终回答",
              "trace_id": "t", "elapsed_ms": 1, "active_modules": ["thinking"], "focus": "x"}
    s._orchestrator.process = AsyncMock(return_value=result)

    monkeypatch.setattr(stream, "connection_manager", MagicMock())
    stream.connection_manager.send_json = AsyncMock()
    monkeypatch.setattr("modules.thinking.communication.get_message_bus", lambda: MagicMock())
    monkeypatch.setattr("modules.security_system.tool_security_gate.set_security_event_callback", lambda cb: None)
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: True)
    monkeypatch.setattr("modules.thinking.session_graph.get_session_graph_store", lambda: MagicMock())
    repo = MagicMock()
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)

    out = await s.think("s1", "你好")
    assert out == "最终回答"


async def test_think_connection_lost(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"processing": False, "messages": [], "started_at": 0}
    s._is_processing = AsyncMock(return_value=False)
    s._append_message = AsyncMock(return_value="uid")
    s._emit = AsyncMock()
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: False)
    monkeypatch.setattr("modules.thinking.communication.get_message_bus", lambda: MagicMock())
    out = await s.think("s1", "你好")
    assert out == ""


async def test_think_error(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"processing": False, "messages": [], "started_at": 0}
    s._is_processing = AsyncMock(return_value=False)
    s._append_message = AsyncMock(return_value="uid")
    s._emit = AsyncMock()
    s._orchestrator.process = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: True)
    monkeypatch.setattr("modules.thinking.communication.get_message_bus", lambda: MagicMock())
    monkeypatch.setattr(stream, "connection_manager", MagicMock())
    stream.connection_manager.send_json = AsyncMock()
    out = await s.think("s1", "你好")
    assert out == ""


# ── _safe_think ────────────────────────────────────────────────────────

async def test_safe_think_success(monkeypatch):
    s = _system()
    s.think = AsyncMock(return_value="ok")
    await _safe_think(s, "s1", "输入")  # 不抛异常


async def test_safe_think_error(monkeypatch):
    s = _system()
    async def boom(sid, inp, callback=None):
        raise RuntimeError("调度崩了")
    s.think = boom
    s._append_message = AsyncMock()
    s._emit = AsyncMock()
    await _safe_think(s, "s1", "输入")  # 兜底保存错误
    s._append_message.assert_awaited_once()


# ── 单例 / 初始化 ──────────────────────────────────────────────────────

async def test_get_thinking_system_singleton(monkeypatch):
    monkeypatch.setattr(stream, "_thinking_system", None)
    a = get_thinking_system()
    b = get_thinking_system()
    assert a is b


async def test_initialize_system(monkeypatch):
    monkeypatch.setattr(stream, "_thinking_system", None)
    sys = await initialize_system()
    assert sys is not None
    assert stream._main_event_loop is not None


# ── 路由 ───────────────────────────────────────────────────────────────

async def test_route_create_session(monkeypatch):
    s = _system()
    s.create_session = AsyncMock(return_value="sess-1")
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.create_session()
    assert out["data"]["session_id"] == "sess-1"


async def test_route_get_context(monkeypatch):
    s = _system()
    s.get_context = MagicMock(return_value=[{"role": "user", "content": "a"}])
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.get_context("s1")
    assert out["data"]["count"] == 1


async def test_route_close_session(monkeypatch):
    s = _system()
    s.stop = AsyncMock()
    s.sessions = {"s1": {}}
    repo = MagicMock()
    repo.delete_session = MagicMock()
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.close_session("s1")
    assert out["success"] is True


async def test_route_update_session_title(monkeypatch):
    s = _system()
    repo = MagicMock()
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.update_session_title("s1", {"title": "新标题"})
    assert out["data"]["title"] == "新标题"
    repo.set_session_title.assert_called_once_with("s1", "新标题")


async def test_route_delete_message(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"messages": [{"id": "m1", "role": "user"}, {"id": "m2", "role": "assistant"}]}
    repo = MagicMock()
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.delete_message("s1", "m1")
    assert len(s.sessions["s1"]["messages"]) == 1


async def test_route_update_message(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"messages": [{"id": "m1", "content": "旧"}]}
    repo = MagicMock()
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.update_message("s1", "m1", {"content": "新"})
    assert s.sessions["s1"]["messages"][0]["content"] == "新"


async def test_route_get_status(monkeypatch):
    s = _system()
    s.get_status = MagicMock(return_value={"running": False, "sessions": 0, "running_sessions": 0})
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.get_status()
    assert out["success"] is True


async def test_route_get_sessions(monkeypatch):
    s = _system()
    s.sessions = {"s1": {"running": True}}
    repo = MagicMock()
    repo.delete_empty_sessions = MagicMock()
    repo.get_all_sessions = MagicMock(return_value=[{"session_id": "s1"}, {"session_id": "s2"}])
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    monkeypatch.setattr(stream.connection_manager, "active_connections", {"ws1": object()})
    out = await stream.get_sessions()
    assert len(out["data"]) == 2


async def test_route_get_session_messages(monkeypatch):
    s = _system()
    repo = MagicMock()
    repo.get_messages = MagicMock(return_value=[{"id": "m1"}])
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.get_session_messages("s1")
    assert out["data"] == [{"id": "m1"}]


async def test_route_stop_thinking(monkeypatch):
    s = _system()
    s.stop = AsyncMock()
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.stop_thinking(session_id="s1")
    assert out["success"] is True
    s.stop.assert_awaited_once()


async def test_route_stop_all(monkeypatch):
    s = _system()
    s.stop = AsyncMock()
    s.sessions = {"s1": {}, "s2": {}}
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.stop_thinking({})
    assert out["success"] is True
    assert s.stop.await_count == 2


# ── _ws_auth_ok ────────────────────────────────────────────────────────

def test_ws_auth_ok_key(monkeypatch):
    import sys, types
    cfg = sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(SIMPLE_API_KEY="secret"))
    import hmac
    ws = MagicMock()
    ws.headers = {"x-api-key": "secret"}
    ws.query_params = {}
    assert _ws_auth_ok(ws) is True
    ws2 = MagicMock()
    ws2.headers = {}
    ws2.query_params = {"api_key": "secret"}
    assert _ws_auth_ok(ws2) is True
    ws3 = MagicMock()
    ws3.headers = {}
    ws3.query_params = {"api_key": "wrong"}
    assert _ws_auth_ok(ws3) is False


def test_ws_auth_ok_no_key(monkeypatch):
    import sys, types
    cfg = sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(SIMPLE_API_KEY=""))
    ws = MagicMock()
    assert _ws_auth_ok(ws) is True
