"""api_stream 补测：连接管理 / 身份解析 / 会话生命周期 / think 深层防御分支 / WS 边界 / 路由 repo=None 分支"""
import asyncio
import json
import threading
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocketDisconnect
from sse_starlette.sse import EventSourceResponse

import modules.thinking.api_stream as stream
from modules.thinking.api_stream import (
    StreamThinkingSystem,
    ConnectionManager,
    _build_event,
    _resolve_identity_name,
    _ws_auth_ok,
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
    monkeypatch.setattr(stream, "connection_manager", MagicMock())
    stream.connection_manager.send_json = AsyncMock()
    monkeypatch.setattr("modules.thinking.communication.get_message_bus", lambda: MagicMock())
    monkeypatch.setattr("modules.security_system.tool_security_gate.set_security_event_callback", lambda cb: None)
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: True)
    monkeypatch.setattr("modules.thinking.session_graph.get_session_graph_store", lambda: MagicMock())
    repo = MagicMock()
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    return repo


def _think_ready(monkeypatch):
    """返回已就绪的 think 系统（processing=False + 依赖 mock）"""
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
    return s


# ── ConnectionManager 防御分支 ─────────────────────────────────────────

async def test_connect_create_task_error(monkeypatch):
    cm = ConnectionManager()
    old_ws = AsyncMock()
    await cm.connect("s1", old_ws)

    def boom(coro):
        raise RuntimeError("create_task down")

    monkeypatch.setattr(stream.asyncio, "create_task", boom)
    new_ws = AsyncMock()
    await cm.connect("s1", new_ws)  # 接管失败降级：仍完成注册
    assert cm.active_connections["s1"] is new_ws


async def test_disconnect_unknown_session():
    cm = ConnectionManager()
    await cm.disconnect("ghost")  # 不在活跃连接 → 直接返回


async def test_send_json_no_websocket():
    cm = ConnectionManager()
    await cm.send_json("ghost", {"type": "x"})  # 无连接 → 空操作


async def test_send_json_from_thread_ws_gone(monkeypatch):
    cm = ConnectionManager()
    cm._loop = MagicMock()
    cm._loop.is_closed.return_value = False
    cm.active_connections["s1"] = AsyncMock()

    running = asyncio.get_running_loop()

    def fake_threadsafe(coro, loop):
        cm.active_connections.clear()  # 校验与执行之间连接被移除
        fut = running.create_task(coro)
        return fut

    monkeypatch.setattr(stream.asyncio, "run_coroutine_threadsafe", fake_threadsafe)
    assert cm.send_json_from_thread("s1", {"a": 1}) is False


async def test_send_json_from_thread_cancelled_error(monkeypatch):
    cm = ConnectionManager()
    ws = AsyncMock()

    async def boom(*a, **k):
        raise asyncio.CancelledError()

    ws.send_json = boom
    await cm.connect("s1", ws)  # _loop = 当前运行 loop
    ok = cm.send_json_from_thread("s1", {"msg": "x"})
    assert ok is True  # fire-and-forget
    await asyncio.sleep(0.05)  # BaseException 被捕获，连接保留
    assert "s1" in cm.active_connections


# ── _ws_auth_ok 导入异常 ────────────────────────────────────────────────

def test_ws_auth_ok_import_error(monkeypatch):
    import sys
    import types

    proxy = types.ModuleType("config.settings")

    def _no_settings(name):
        raise RuntimeError("settings 不可用")

    proxy.__getattr__ = _no_settings
    monkeypatch.setitem(sys.modules, "config.settings", proxy)
    assert _ws_auth_ok(MagicMock()) is True  # 降级放行


# ── _resolve_identity_name 空名称 / 空 model_id ─────────────────────────

def test_resolve_identity_name_empty_name_and_tmid(monkeypatch):
    stream._identity_name_cache.clear()
    monkeypatch.setattr("modules.thinking.identity.get_identities", lambda: {
        "zz": {"name": "B", "model_id": ""},                      # tmid 为空 → continue
        "supervisor_code": {"name": "", "model_id": "supervisor_code"},  # 直查命中但 name 为空
    })
    assert _resolve_identity_name("supervisor_code_001") == ""


# ── 会话生命周期 ───────────────────────────────────────────────────────

async def test_stop_unknown_session():
    s = _system()
    s.sessions["s1"] = {"running": True}
    await s.stop("ghost")  # 未注册会话 → 静默返回


async def test_stop_done_task():
    s = _system()
    task = asyncio.create_task(asyncio.sleep(0))
    await asyncio.sleep(0.01)  # 让任务完成
    s.sessions["s1"] = {"running": True, "scheduler_task": task}
    await s.stop("s1")  # task.done() → 不再 cancel
    assert s.sessions["s1"]["running"] is False


async def test_append_message_repo_none(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"messages": []}
    monkeypatch.setattr(s, "_get_session_repo", lambda: None)
    assert await s._append_message("s1", "assistant", "x") == ""


async def test_append_message_backfill_index_guard(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"messages": []}
    repo = MagicMock()

    def save(sid, role, content, tier=""):
        s.sessions["s1"]["messages"].clear()  # 模拟并发裁剪
        return "mid"

    repo.save_message = save
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    assert await s._append_message("s1", "assistant", "x") == "mid"


async def test_set_processing_unknown_session():
    s = _system()
    await s._set_processing("ghost", True)  # 未注册 → 空操作


# ── _format_scheduler_event 深层防御分支 ────────────────────────────────

def _fmt(s, event):
    return s._format_scheduler_event(event)


def test_format_expert_parent_empty_name(monkeypatch):
    stream._identity_name_cache.clear()
    monkeypatch.setattr("modules.thinking.identity.get_identities", lambda: {
        "pm": {"name": "", "model_id": "pm_1"},
        "expert": {"name": "专家A", "model_id": "expert_1"},
    })
    s = _system()
    event = {
        "type": "model_comm",
        "payload": {
            "msg_type": "broadcast", "sender": "s", "recipient": "t",
            "content": {"content": "结果", "entry_type": "response", "round": 1,
                        "model_id": "expert_1", "metadata": {"return_to_model_id": "pm_1"}},
            "metadata": {"dialog_id": "d1", "tier": "expert"},
        },
    }
    out = _fmt(s, event)
    assert "[专家A]" in out["content"]  # 父级名称解析失败 → 保留自身 label
    assert "→" not in out["content"]


def test_format_broadcast_unknown_entry_type(monkeypatch):
    stream._identity_name_cache.clear()
    monkeypatch.setattr("modules.thinking.identity.get_identities", lambda: {})
    s = _system()
    event = {
        "type": "model_comm",
        "payload": {
            "msg_type": "broadcast", "sender": "s", "recipient": "t",
            "content": {"content": "草稿文本", "entry_type": "draft", "round": 0,
                        "model_id": "m1", "metadata": {}},
            "metadata": {"dialog_id": "d1", "tier": "expert"},
        },
    }
    out = _fmt(s, event)
    assert "草稿文本" in out["content"]  # 无 type_tag → else 分支


def test_format_preliminary_dict_no_content():
    s = _system()
    event = {
        "type": "model_comm",
        "payload": {
            "msg_type": "comm", "sender": "s", "recipient": "t",
            "content": {"foo": "bar"},
            "metadata": {"event": "preliminary_response"},
        },
    }
    out = _fmt(s, event)
    assert "[preliminary]" in out["content"]


# ── think 内部防御分支 ──────────────────────────────────────────────────

async def test_think_runner_other_tier(monkeypatch):
    s = _think_ready(monkeypatch)
    import modules.thinking.core.model_runner as mr_mod
    rm = MagicMock()
    rm.list_runners.return_value = [
        {"tier": "other", "running": True, "name": "未知", "role": "x", "model_id": "x"},
    ]
    monkeypatch.setattr(mr_mod, "_runner_managers", {"s1": rm})
    monkeypatch.setattr(mr_mod, "_runner_managers_lock", threading.Lock())

    async def slow_process(*a, **k):
        await asyncio.sleep(1.1)
        return {"module_results": [], "decisions": {}, "response": "ok"}

    s._orchestrator.process = slow_process
    out = await s.think("s1", "你好")
    assert out == "ok"


async def test_think_runner_collection_error(monkeypatch):
    s = _think_ready(monkeypatch)
    import modules.thinking.core.model_runner as mr_mod
    rm = MagicMock()
    rm.list_runners = MagicMock(side_effect=RuntimeError("list fail"))
    monkeypatch.setattr(mr_mod, "_runner_managers", {"s1": rm})
    monkeypatch.setattr(mr_mod, "_runner_managers_lock", threading.Lock())

    async def slow_process(*a, **k):
        await asyncio.sleep(1.1)
        return {"module_results": [], "decisions": {}, "response": "ok"}

    s._orchestrator.process = slow_process
    out = await s.think("s1", "你好")
    assert out == "ok"


async def test_think_stage_event_record_error(monkeypatch):
    s = _think_ready(monkeypatch)
    store = MagicMock()
    store.record = MagicMock(side_effect=RuntimeError("graph down"))
    monkeypatch.setattr("modules.thinking.session_graph.get_session_graph_store", lambda: store)

    async def fake_process(user_input, context, short_term, callback, session_id, model_id=None):
        callback({"type": "model_comm", "source": "s", "target": "t", "payload": {
            "msg_type": "broadcast", "sender": "m", "recipient": "b",
            "content": {"content": "对话内容", "entry_type": "response", "round": 1,
                        "model_id": "large_primary", "metadata": {}},
            "metadata": {"dialog_id": "d1", "tier": "large"},
        }})
        return {"module_results": [], "decisions": {}, "response": "ok"}

    s._orchestrator.process = fake_process
    out = await s.think("s1", "你好")
    assert out == "ok"


async def test_think_persist_thought_error(monkeypatch):
    s = _think_ready(monkeypatch)
    s._persist_thought = AsyncMock(side_effect=RuntimeError("db down"))
    store = MagicMock()
    monkeypatch.setattr("modules.thinking.session_graph.get_session_graph_store", lambda: store)

    async def fake_process(user_input, context, short_term, callback, session_id, model_id=None):
        callback({"type": "model_comm", "source": "s", "target": "t", "payload": {
            "msg_type": "broadcast", "sender": "m", "recipient": "b",
            "content": {"content": "对话内容", "entry_type": "response", "round": 1,
                        "model_id": "large_primary", "metadata": {}},
            "metadata": {"dialog_id": "d1", "tier": "large"},
        }})
        return {"module_results": [], "decisions": {}, "response": "ok"}

    s._orchestrator.process = fake_process
    out = await s.think("s1", "你好")
    assert out == "ok"


async def test_think_cancelled_no_partial(monkeypatch):
    s = _think_ready(monkeypatch)
    import modules.thinking.core.model_runner as mr_mod
    monkeypatch.setattr(mr_mod, "_runner_managers", {})  # rm 为空 → 无部分输出
    monkeypatch.setattr(mr_mod, "_runner_managers_lock", threading.Lock())

    async def cancelling_process(*a, **k):
        raise asyncio.CancelledError()

    s._orchestrator.process = cancelling_process
    out = await s.think("s1", "你好")
    assert out == "stopped"


async def test_think_cancelled_blackboard_error(monkeypatch):
    s = _think_ready(monkeypatch)
    import modules.thinking.core.model_runner as mr_mod
    rm = MagicMock()
    rm.blackboard = MagicMock(side_effect=RuntimeError("blackboard down"))
    monkeypatch.setattr(mr_mod, "_runner_managers", {"s1": rm})
    monkeypatch.setattr(mr_mod, "_runner_managers_lock", threading.Lock())

    async def cancelling_process(*a, **k):
        raise asyncio.CancelledError()

    s._orchestrator.process = cancelling_process
    out = await s.think("s1", "你好")
    assert out == "stopped"


async def test_think_cancelled_partial_append_error(monkeypatch):
    s = _think_ready(monkeypatch)
    import modules.thinking.core.model_runner as mr_mod
    rm = MagicMock()
    rm.blackboard.final_response = "部分输出"
    monkeypatch.setattr(mr_mod, "_runner_managers", {"s1": rm})
    monkeypatch.setattr(mr_mod, "_runner_managers_lock", threading.Lock())

    async def append(session_id, role, content, tier=""):
        if role == "assistant":
            raise RuntimeError("save fail")
        return "uid"

    s._append_message = append

    async def cancelling_process(*a, **k):
        raise asyncio.CancelledError()

    s._orchestrator.process = cancelling_process
    out = await s.think("s1", "你好")
    assert out == "stopped"


async def test_think_meta_collection_errors(monkeypatch):
    s = _think_ready(monkeypatch)
    boom_get = MagicMock()
    boom_get.get = MagicMock(side_effect=RuntimeError("meta down"))
    monkeypatch.setattr("modules.thinking.probes.probe_tools._session_guidance", boom_get)
    monkeypatch.setattr("modules.thinking.core.model_runner._session_memory_context", boom_get)
    import modules.thinking.multi_model_orchestrator as mmo

    class BoomCtx:
        def __enter__(self):
            raise RuntimeError("lock down")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mmo, "_session_dialog_history", {"s1": "历史"})
    monkeypatch.setattr(mmo, "_session_dialog_history_lock", BoomCtx())

    s._orchestrator.process = AsyncMock(return_value={
        "module_results": [], "decisions": {}, "response": "ok"})
    out = await s.think("s1", "你好")
    assert out == "ok"


async def test_think_finally_snapshot_error(monkeypatch):
    s = _think_ready(monkeypatch)
    snap_store = MagicMock()
    snap_store.snapshot = MagicMock(side_effect=RuntimeError("snap down"))
    monkeypatch.setattr("modules.thinking.session_graph.get_session_graph_store", lambda: snap_store)
    s._orchestrator.process = AsyncMock(return_value={
        "module_results": [], "decisions": {}, "response": "ok"})
    out = await s.think("s1", "你好")
    assert out == "ok"


# ── _post_task_extraction 防御分支（超 50 字阈值）──────────────────────

def _long_session():
    s = _system()
    s.sessions = {"s1": {"messages": [
        {"role": "user", "content": "这是一个足够长的用户提问内容用于触发后处理流程"},
        {"role": "assistant", "content": "这是一个足够长的模型回复内容用于触发后处理流程"},
    ], "_processed_hashes": set()}}
    return s


async def _patched_reducer(monkeypatch, reducer=None):
    import modules.memory.event_reducer as er_mod
    from config.settings import settings as _cfg
    monkeypatch.setattr(_cfg, "MEMORY_SUMMARY_ENABLED", True)
    reducer = reducer or AsyncMock()
    reducer.reduce.return_value = []
    monkeypatch.setattr(er_mod, "EventReducer", lambda **kw: reducer)

    async def noop_sleep(*a, **k):
        return None

    monkeypatch.setattr(stream.asyncio, "sleep", noop_sleep)
    return reducer


async def test_post_task_extraction_empty_content_skip(monkeypatch):
    s = _system()
    s.sessions = {"s1": {"messages": [
        {"role": "user", "content": "这是一个足够长的用户提问内容用于触发后处理流程"},
        {"role": "user", "content": ""},  # 空内容 → continue
        {"role": "user", "content": "这是另一条足够长的用户内容用于凑足字符数量"},
    ], "_processed_hashes": set()}}
    reducer = await _patched_reducer(monkeypatch)
    await s._post_task_extraction("s1", "u", "r", owner_id="x")
    assert reducer.reduce.await_count == 1


async def test_post_task_extraction_owner_infer_default(monkeypatch):
    s = _system()
    s.sessions = {"s1": {"messages": [
        {"role": "user", "content": "这是一个足够长的用户提问内容用于触发后处理流程"},
        {"role": "system", "content": "这是一个足够长的系统提示内容用于凑足字符数量哦"},
    ], "_processed_hashes": set()}}
    reducer = await _patched_reducer(monkeypatch)
    await s._post_task_extraction("s1", "u", "r")  # 无 owner 匹配 → 默认 large
    assert reducer.reduce.await_count == 1


async def test_post_task_extraction_owner_assistant_model_id(monkeypatch):
    s = _system()
    s.sessions = {"s1": {"messages": [
        {"role": "user", "content": "这是一个足够长的用户提问内容用于触发后处理流程"},
        {"role": "assistant", "content": "这是一个足够长的模型回复内容用于触发后处理流程"},
    ], "_processed_hashes": set(), "model_id": "supervisor::pm_1"}}
    reducer = await _patched_reducer(monkeypatch)
    await s._post_task_extraction("s1", "u", "r")  # assistant 角色 → session model_id
    assert reducer.reduce.await_count == 1


async def test_post_task_extraction_client_fail_long(monkeypatch):
    reducer = await _patched_reducer(monkeypatch)
    monkeypatch.setattr("infra.model.small_model_client.SmallModelClient",
                        MagicMock(side_effect=RuntimeError("no client")))
    s = _long_session()
    await s._post_task_extraction("s1", "u", "r", owner_id="x")
    assert reducer.reduce.await_count == 1


async def test_post_task_extraction_get_reducer_sync_error(monkeypatch):
    import modules.memory.event_reducer as er_mod
    reducer = await _patched_reducer(monkeypatch)
    monkeypatch.setattr(er_mod, "get_reducer", MagicMock(side_effect=RuntimeError("reducer down")))
    s = _long_session()
    await s._post_task_extraction("s1", "u", "r", owner_id="x")
    assert reducer.reduce.await_count == 1


async def test_post_task_extraction_reduce_error_long(monkeypatch):
    import modules.memory.event_reducer as er_mod
    reducer = AsyncMock()
    reducer.reduce.side_effect = RuntimeError("reduce fail")
    monkeypatch.setattr(er_mod, "EventReducer", lambda **kw: reducer)

    async def noop_sleep(*a, **k):
        return None

    monkeypatch.setattr(stream.asyncio, "sleep", noop_sleep)
    s = _long_session()
    await s._post_task_extraction("s1", "u", "r", owner_id="x")  # 提取异常静默


# ── initialize_system 无事件循环 ───────────────────────────────────────

def test_initialize_system_no_loop(monkeypatch):
    fake = types.SimpleNamespace(
        get_running_loop=lambda: (_ for _ in ()).throw(RuntimeError("no loop")),
        Lock=asyncio.Lock,
    )
    monkeypatch.setattr(stream, "asyncio", fake)
    monkeypatch.setattr(stream, "_thinking_system", None)
    monkeypatch.setattr(stream, "_main_event_loop", None)
    asyncio.run(initialize_system())
    assert stream._main_event_loop is None


# ── WebSocket 边界 ─────────────────────────────────────────────────────

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


async def test_ws_flow_empty_input(monkeypatch):
    s, safe = _ws_env(monkeypatch)
    monkeypatch.setattr(stream.connection_manager, "active_connections", {})
    ws = FakeWS(incoming=[json.dumps({"type": "input", "content": "  ", "attachments": []})])
    await stream.websocket_chat(ws, "s1")
    safe.assert_not_awaited()


async def test_ws_flow_attachment_non_direct(monkeypatch):
    s, safe = _ws_env(monkeypatch)
    monkeypatch.setattr("modules.thinking.attachment_handler.validate_attachments", lambda a: None)
    monkeypatch.setattr("modules.thinking.attachment_handler.parse_attachments",
                        AsyncMock(return_value="附件解析文本"))
    from config.settings import settings
    monkeypatch.setattr(settings, "CHAT_IMAGE_MODE", "describe")
    ws = FakeWS(incoming=[json.dumps({"type": "input", "content": "看", "attachments": [{"type": "image"}]})])
    await stream.websocket_chat(ws, "s1")
    await asyncio.sleep(0.05)
    safe.assert_called_once()


async def test_ws_flow_attachment_non_direct_empty(monkeypatch):
    s, safe = _ws_env(monkeypatch)
    monkeypatch.setattr("modules.thinking.attachment_handler.validate_attachments", lambda a: None)
    monkeypatch.setattr("modules.thinking.attachment_handler.parse_attachments", AsyncMock(return_value=""))
    from config.settings import settings
    monkeypatch.setattr(settings, "CHAT_IMAGE_MODE", "describe")
    ws = FakeWS(incoming=[json.dumps({"type": "input", "content": "看", "attachments": [{"type": "image"}]})])
    await stream.websocket_chat(ws, "s1")
    await asyncio.sleep(0.05)
    safe.assert_called_once()


async def test_ws_flow_security_response_no_request_id(monkeypatch):
    s, safe = _ws_env(monkeypatch)
    ws = FakeWS(incoming=[json.dumps({"type": "security_response", "approved": True})])
    await stream.websocket_chat(ws, "s1")
    safe.assert_not_awaited()


async def test_ws_flow_interactive_response_no_request_id(monkeypatch):
    s, safe = _ws_env(monkeypatch)
    ws = FakeWS(incoming=[json.dumps({"type": "interactive_response", "answer": "是"})])
    await stream.websocket_chat(ws, "s1")
    safe.assert_not_awaited()


async def test_ws_flow_interactive_response_error(monkeypatch):
    s, safe = _ws_env(monkeypatch)
    import modules.thinking.core.model_runner as mr_mod
    mgr = MagicMock()
    mgr.resolve_user_response = MagicMock(side_effect=RuntimeError("resolve fail"))
    monkeypatch.setattr(mr_mod, "_runner_managers", {"s1": mgr})
    monkeypatch.setattr(mr_mod, "_runner_managers_lock", threading.Lock())
    ws = FakeWS(incoming=[json.dumps({"type": "interactive_response", "request_id": "r1"})])
    await stream.websocket_chat(ws, "s1")  # 错误被捕获


async def test_ws_flow_finally_reject_reviews_error(monkeypatch):
    s, safe = _ws_env(monkeypatch)
    gate = MagicMock()
    gate.reject_session_reviews = MagicMock(side_effect=RuntimeError("reject fail"))
    monkeypatch.setattr("modules.security_system.tool_security_gate.ToolSecurityGate", gate)
    ws = FakeWS(incoming=[])
    await stream.websocket_chat(ws, "s1")  # 断开时清理失败静默
    assert ws.closed is None


async def test_ws_flow_finally_reject_responses_error(monkeypatch):
    s, safe = _ws_env(monkeypatch)
    import modules.thinking.core.model_runner as mr_mod
    monkeypatch.setattr(mr_mod, "reject_session_user_responses",
                        MagicMock(side_effect=RuntimeError("reject fail")))
    ws = FakeWS(incoming=[])
    await stream.websocket_chat(ws, "s1")  # 断开时清理失败静默


# ── 路由：repo=None / 成功路径 ─────────────────────────────────────────

async def test_route_sse_with_question(monkeypatch):
    s = _system()
    s.start = AsyncMock()
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    res = await stream.sse_session_get("s1", "问题")
    assert isinstance(res, EventSourceResponse)


async def test_route_close_session_repo_none(monkeypatch):
    s = _system()
    s.stop = AsyncMock()
    monkeypatch.setattr(s, "_get_session_repo", lambda: None)
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.close_session("s1")
    assert out["success"] is True


async def test_route_update_title_repo_none(monkeypatch):
    s = _system()
    monkeypatch.setattr(s, "_get_session_repo", lambda: None)
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.update_session_title("s1", {"title": "新"})
    assert out["success"] is True


async def test_route_delete_message_repo_none(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"messages": []}
    monkeypatch.setattr(s, "_get_session_repo", lambda: None)
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.delete_message("s1", "m1")
    assert out["data"]["removed"] is True


async def test_route_update_message_repo_none(monkeypatch):
    s = _system()
    monkeypatch.setattr(s, "_get_session_repo", lambda: None)
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.update_message("s1", "m1", {"content": "新"})
    assert out["data"]["message"] == "消息已更新"


async def test_route_update_message_no_match(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"messages": [{"id": "m1", "content": "旧"}]}
    repo = MagicMock()
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.update_message("s1", "ghost", {"content": "新"})  # 未匹配 id → 循环自然结束
    assert out["data"]["message"] == "消息已更新"


async def test_route_get_sessions_repo_none(monkeypatch):
    s = _system()
    monkeypatch.setattr(s, "_get_session_repo", lambda: None)
    monkeypatch.setattr(stream, "get_thinking_system", lambda: s)
    out = await stream.get_sessions()
    assert out["success"] is True
    assert out["data"] == []
