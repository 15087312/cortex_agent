"""chat_gateway 补测：_consume_turn 深分支 / WS 注册清理 / SSE 超时 / 路由 agent 分支"""
import asyncio
import itertools
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocketDisconnect

import modules.thinking.chat_gateway as cg


class FakeWS:
    def __init__(self, incoming=None, fail_events=()):
        self.incoming = list(incoming or [])
        self.sent = []
        self.accepted = False
        self.closed = False
        self.fail_events = set(fail_events)

    async def accept(self):
        self.accepted = True

    async def send_json(self, data):
        if isinstance(data, dict) and data.get("event") in self.fail_events:
            raise RuntimeError("send failed")
        self.sent.append(data)

    async def receive_text(self):
        if self.incoming:
            return self.incoming.pop(0)
        raise WebSocketDisconnect()

    async def close(self, code=1000, reason=""):
        self.closed = True


def _setup_repo(monkeypatch):
    repo = MagicMock()
    repo.create_session = MagicMock()
    repo.save_message = MagicMock(side_effect=lambda sid, role, c: f"id_{role}")
    monkeypatch.setattr(cg, "_get_chat_session_repo", lambda: repo)
    return repo


def _setup_thinker(monkeypatch, blackboard=None):
    # 黑板已删除（DB 为唯一真源），thinker 不再提供 get_blackboard
    thinker = MagicMock()
    thinker.get_blackboard = MagicMock(side_effect=AttributeError("blackboard 已删除"))
    monkeypatch.setattr(cg, "_get_chat_thinker", lambda: thinker)
    return thinker


def _noop(monkeypatch, repo=None):
    cg._schema_checked = True
    # 事件日志同样不进真实 DB（时辰测试靠 monkeypatch(cg.time) 控制时间，
    # 日志/建表会偷走 fake clock 的取值）
    events = MagicMock()
    monkeypatch.setattr("modules.database.session_events.get_session_event_repo", lambda: events)
    return _setup_repo(monkeypatch) if repo is None else repo


# ── _consume_turn 深分支 ───────────────────────────────────────────────

async def test_consume_turn_ack_send_fail_returns(monkeypatch):
    ws = FakeWS(fail_events=("received",))
    repo = MagicMock()
    repo.save_message = MagicMock(return_value="m1")
    thinker = MagicMock()
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: True)
    await cg._consume_turn(ws, "s1", repo, thinker, "hi")
    assert ws.sent == []


async def test_consume_turn_handshake_exception(monkeypatch):
    ws = FakeWS()
    repo = MagicMock()
    repo.save_message = MagicMock(side_effect=lambda sid, role, c: f"id_{role}")
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        await queue.put({"type": "done"})

    thinker.think = fake_think

    def boom(sid):
        raise RuntimeError("handshake down")

    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", boom)
    await cg._consume_turn(ws, "s1", repo, thinker, "hi")
    assert any(s["type"] == "done" for s in ws.sent)


async def test_consume_turn_task_abort(monkeypatch):
    """think 直接结束无终态 token → 发「任务异常终止」"""
    ws = FakeWS()
    repo = MagicMock()
    repo.save_message = MagicMock(return_value="m1")
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        pass  # 直接返回，无 token

    thinker.think = fake_think
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: True)
    await cg._consume_turn(ws, "s1", repo, thinker, "hi")
    assert any(s["event"] == "error" for s in ws.sent)


async def test_consume_turn_real_timeout(monkeypatch):
    """累计静默 >300s → 发「思考超时」并取消 think 任务"""
    ws = FakeWS()
    repo = MagicMock()
    repo.save_message = MagicMock(return_value="m1")
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        await asyncio.sleep(30)

    thinker.think = fake_think
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: True)
    times = iter(itertools.chain([0.0, 0.0], itertools.repeat(301.0)))
    monkeypatch.setattr(cg.time, "time", lambda: next(times))
    await cg._consume_turn(ws, "s1", repo, thinker, "hi")
    assert any("思考超时" in s.get("content", "") for s in ws.sent)


async def test_consume_turn_flush_and_token_heartbeat(monkeypatch):
    """done 时把残余正文补成 chunk 帧 + token 流进度心跳"""
    ws = FakeWS()
    repo = MagicMock()
    repo.save_message = MagicMock(side_effect=lambda sid, role, c: f"id_{role}")
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        await queue.put({"type": "message", "content": "部"})
        await queue.put({"type": "done"})

    thinker.think = fake_think
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: True)
    times = iter(itertools.chain([0.0, 0.0], itertools.repeat(100.0)))
    monkeypatch.setattr(cg.time, "time", lambda: next(times))
    await cg._consume_turn(ws, "s1", repo, thinker, "hi")
    chunks = [s for s in ws.sent if s["type"] == "chunk"]
    assert any(s["content"] == "部" for s in chunks)
    assert any(s["event"] == "thinking_progress" for s in ws.sent)
    # 正文不再当思考发：done 后仍只有一帧 chunk + 落定 message
    assert not any(s["type"] == "thinking" for s in ws.sent)


async def test_consume_turn_streams_chunks_incrementally(monkeypatch):
    """正文满 40 字符即推一帧 chunk（不等 done），尾部在 done 补齐"""
    ws = FakeWS()
    repo = MagicMock()
    repo.save_message = MagicMock(side_effect=lambda sid, role, c: f"id_{role}")
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        for _ in range(3):
            await queue.put({"type": "message", "content": "x" * 20})
        await queue.put({"type": "done"})

    thinker.think = fake_think
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: True)
    await cg._consume_turn(ws, "s1", repo, thinker, "hi")
    chunks = [s for s in ws.sent if s["type"] == "chunk"]
    assert [len(s["content"]) for s in chunks] == [40, 20]
    assert "".join(s["content"] for s in chunks) == "x" * 60
    # 落定消息仍是完整正文
    assert any(s.get("content") == "x" * 60 for s in ws.sent if s["type"] == "message")


async def test_consume_turn_error_flush_and_cancel(monkeypatch):
    """error 前补发残余 chunk + finally 取消未完成 think 任务"""
    ws = FakeWS()
    repo = MagicMock()
    repo.save_message = MagicMock(return_value="m1")
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        await queue.put({"type": "message", "content": "部"})
        await queue.put({"type": "error", "content": "模型失败"})
        await asyncio.sleep(30)

    thinker.think = fake_think
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: True)
    await cg._consume_turn(ws, "s1", repo, thinker, "hi")
    chunks = [s for s in ws.sent if s["type"] == "chunk"]
    assert any(s["content"] == "部" for s in chunks)
    assert any(s["type"] == "error" for s in ws.sent)


# ── _chatonly_ws 深分支 ───────────────────────────────────────────────

async def test_chatonly_ws_registration_error(monkeypatch):
    _noop(monkeypatch)
    cm = MagicMock()

    class BoomLock:
        async def __aenter__(self):
            raise RuntimeError("no cm")

        async def __aexit__(self, *a):
            pass

    cm._lock = BoomLock()
    monkeypatch.setattr("modules.thinking.api_stream.connection_manager", cm)
    ws = FakeWS(incoming=[])
    await cg._chatonly_ws(ws, "s1")  # 注册失败 → 静默
    assert ws.accepted is True


async def test_chatonly_ws_ready_fail_unregisters(monkeypatch):
    _noop(monkeypatch)
    cm = MagicMock()
    cm.active_connections = {}
    cm._lock = asyncio.Lock()
    cm._loop = None
    monkeypatch.setattr("modules.thinking.api_stream.connection_manager", cm)
    ws = FakeWS(incoming=[], fail_events=("session_ready",))
    await cg._chatonly_ws(ws, "s1")
    assert "s1" not in cm.active_connections


async def test_chatonly_ws_non_json_message(monkeypatch):
    repo = _noop(monkeypatch)
    monkeypatch.setattr(cg, "_consume_turn", AsyncMock())
    ws = FakeWS(incoming=["不是 json 文本"])
    await cg._chatonly_ws(ws, "s1")
    cg._consume_turn.assert_called_once()
    assert ws.closed is True


async def test_chatonly_ws_empty_input_continue(monkeypatch):
    _noop(monkeypatch)
    monkeypatch.setattr(cg, "_consume_turn", AsyncMock())
    ws = FakeWS(incoming=[json.dumps({"type": "input", "content": ""})])
    await cg._chatonly_ws(ws, "s1")
    cg._consume_turn.assert_not_called()


async def test_chatonly_ws_attachment_describe_mode(monkeypatch):
    repo = _noop(monkeypatch)
    monkeypatch.setattr("modules.thinking.attachment_handler.validate_attachments", lambda a: None)
    monkeypatch.setattr("modules.thinking.attachment_handler.parse_attachments", AsyncMock(return_value="附件内容"))
    monkeypatch.setattr("modules.thinking.attachment_handler.extract_images", MagicMock(return_value=["img"]))
    from config.settings import settings
    monkeypatch.setattr(settings, "CHAT_IMAGE_MODE", "describe")
    consume = AsyncMock()
    monkeypatch.setattr(cg, "_consume_turn", consume)
    ws = FakeWS(incoming=[json.dumps({"type": "input", "content": "", "attachments": [{"type": "image"}]})])
    await cg._chatonly_ws(ws, "s1")
    args, kwargs = consume.call_args
    assert "附件内容" in args[4]


async def test_chatonly_ws_attachment_parse_error(monkeypatch):
    _noop(monkeypatch)
    monkeypatch.setattr("modules.thinking.attachment_handler.validate_attachments", lambda a: None)
    monkeypatch.setattr("modules.thinking.attachment_handler.parse_attachments", AsyncMock(side_effect=RuntimeError("parse fail")))
    from config.settings import settings
    monkeypatch.setattr(settings, "CHAT_IMAGE_MODE", "describe")
    consume = AsyncMock()
    monkeypatch.setattr(cg, "_consume_turn", consume)
    ws = FakeWS(incoming=[json.dumps({"type": "input", "content": "x", "attachments": [{"type": "image"}]})])
    await cg._chatonly_ws(ws, "s1")
    assert consume.call_count == 1


async def test_chatonly_ws_new_message_cancels_old(monkeypatch):
    _noop(monkeypatch)
    started = asyncio.Event()

    async def slow_consume(websocket, sid, repo, thinker, content):
        started.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(cg, "_consume_turn", slow_consume)
    ws = FakeWS(incoming=[
        json.dumps({"type": "input", "content": "第一轮"}),
        json.dumps({"type": "input", "content": "第二轮"}),
    ])
    await cg._chatonly_ws(ws, "s1")  # 第二轮触发 cancel 旧任务
    assert ws.closed is True


async def test_chatonly_ws_ping_send_fail_breaks(monkeypatch):
    _noop(monkeypatch)
    monkeypatch.setattr(cg, "_consume_turn", AsyncMock())
    ws = FakeWS(incoming=[json.dumps({"type": "ping"})], fail_events=("pong",))
    await cg._chatonly_ws(ws, "s1")
    assert ws.closed is True


async def test_chatonly_ws_stop_send_fail_breaks(monkeypatch):
    _noop(monkeypatch)
    monkeypatch.setattr(cg, "_consume_turn", AsyncMock())
    ws = FakeWS(incoming=[json.dumps({"type": "stop"})], fail_events=("stopped",))
    await cg._chatonly_ws(ws, "s1")
    assert ws.closed is True


async def test_chatonly_ws_blackboard_clear_fail(monkeypatch):
    repo = _noop(monkeypatch)
    bb = MagicMock()
    bb.clear_session = MagicMock(side_effect=RuntimeError("clear fail"))
    _setup_thinker(monkeypatch, blackboard=bb)
    monkeypatch.setattr(cg, "_consume_turn", AsyncMock())
    ws = FakeWS(incoming=[])
    await cg._chatonly_ws(ws, "s1")  # 清理失败静默


async def test_chatonly_ws_close_fail(monkeypatch):
    repo = _noop(monkeypatch)
    cm = MagicMock()
    cm.active_connections = {}
    cm._lock = asyncio.Lock()
    cm._loop = None
    monkeypatch.setattr("modules.thinking.api_stream.connection_manager", cm)
    monkeypatch.setattr(cg, "_consume_turn", AsyncMock())
    ws = FakeWS(incoming=[])

    async def boom_close(code=1000, reason=""):
        raise RuntimeError("close fail")

    ws.close = boom_close
    await cg._chatonly_ws(ws, "s1")  # close 失败静默
    assert ws.accepted is True


# ── _chatonly_sse 深分支 ──────────────────────────────────────────────

async def test_chatonly_sse_heartbeat_and_timeout(monkeypatch):
    repo = _noop(monkeypatch)
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        await asyncio.sleep(30)

    thinker.think = fake_think
    monkeypatch.setattr(cg, "_get_chat_thinker", lambda: thinker)
    times = iter([0.0, 1.0, 1.5, 301.0, 301.5, 301.6])
    monkeypatch.setattr(cg.time, "time", lambda: next(times))
    events = [e async for e in cg._chatonly_sse("s1", "问题")]
    events_type = [e["event"] for e in events]
    assert "thinking_progress" in events_type
    assert "error" in events_type


# ── 路由：websocket ───────────────────────────────────────────────────

async def test_ws_auth_fail(monkeypatch):
    monkeypatch.setattr("modules.thinking.api_stream._ws_auth_ok", lambda ws: False)
    ws = FakeWS()
    await cg.websocket_chat(ws, "s1")
    assert ws.closed is True


async def test_ws_chatonly_route(monkeypatch):
    _noop(monkeypatch)
    monkeypatch.setattr("modules.thinking.api_stream._ws_auth_ok", lambda ws: True)
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "chatonly")
    inner = AsyncMock()
    monkeypatch.setattr(cg, "_chatonly_ws", inner)
    ws = FakeWS()
    await cg.websocket_chat(ws, "s1")
    inner.assert_awaited_once()


async def test_ws_agent_route(monkeypatch):
    monkeypatch.setattr("modules.thinking.api_stream._ws_auth_ok", lambda ws: True)
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "agent")
    aws = AsyncMock()
    monkeypatch.setattr("modules.thinking.api_stream.websocket_chat", aws)
    ws = FakeWS()
    await cg.websocket_chat(ws, "s1")
    aws.assert_awaited_once()


# ── 路由：SSE / 上下文 / 会话 ─────────────────────────────────────────

async def test_sse_chatonly_with_question(monkeypatch):
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "chatonly")

    async def fake_sse(sid, q):
        if False:
            yield {}

    monkeypatch.setattr(cg, "_chatonly_sse", fake_sse)
    resp = await cg.sse_session_get("s1", "问题")
    assert resp is not None


async def test_sse_agent_route(monkeypatch):
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "agent")
    aws = AsyncMock(return_value=object())
    monkeypatch.setattr("modules.thinking.api_stream.sse_session_get", aws)
    out = await cg.sse_session_get("s1", "问题")
    assert out is not None
    aws.assert_awaited_once()


async def test_get_context_agent(monkeypatch):
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "agent")
    aws = AsyncMock(return_value={"success": True, "data": {}})
    monkeypatch.setattr("modules.thinking.api_stream.get_context", aws)
    out = await cg.get_context("s1")
    assert out["success"] is True


async def test_close_session_agent(monkeypatch):
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "agent")
    aws = AsyncMock(return_value={"success": True})
    monkeypatch.setattr("modules.thinking.api_stream.close_session", aws)
    out = await cg.close_session("s1")
    assert out["success"] is True


async def test_close_session_chatonly_clear_fail(monkeypatch):
    _noop(monkeypatch)
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "chatonly")
    bb = MagicMock()
    bb.clear_session = MagicMock(side_effect=RuntimeError("boom"))
    _setup_thinker(monkeypatch, blackboard=bb)
    repo = _noop(monkeypatch)
    out = await cg.close_session("s1")
    assert out["success"] is True


# ── 批量删除边界 ──────────────────────────────────────────────────────

async def test_batch_delete_non_string_ids(monkeypatch):
    repo = _noop(monkeypatch)
    out = await cg.batch_delete_sessions({"session_ids": [123, "pet_main", None, "ok"]})
    assert out["data"]["deleted"] == ["pet_main", "ok"]


async def test_batch_delete_repo_error(monkeypatch):
    repo = _noop(monkeypatch)
    repo.delete_session = MagicMock(side_effect=RuntimeError("db down"))
    out = await cg.batch_delete_sessions({"session_ids": ["a"]})
    assert out["data"]["deleted"] == ["a"]


async def test_batch_delete_thinker_error(monkeypatch):
    repo = _noop(monkeypatch)
    bb = MagicMock()
    bb.clear_session = MagicMock(side_effect=RuntimeError("boom"))
    _setup_thinker(monkeypatch, blackboard=bb)
    out = await cg.batch_delete_sessions({"session_ids": ["a"]})
    assert out["data"]["deleted"] == ["a"]


# ── 会话图谱 / 主动搭话 ───────────────────────────────────────────────

async def test_get_session_graph_restore(monkeypatch):
    store = MagicMock()
    store.get_graph = MagicMock(side_effect=[{"nodes": []}, {"nodes": [{"id": 1}]}])
    store.restore = MagicMock()
    monkeypatch.setattr("modules.thinking.session_graph.get_session_graph_store", lambda: store)
    repo = _noop(monkeypatch)
    repo.get_session_metadata = MagicMock(return_value={"session_graph": {"nodes": [1]}})
    out = await cg.get_session_graph("s1")
    assert out["success"] is True
    store.restore.assert_called_once()




# ── 标题 / 消息 / 状态 / 会话 路由 ─────────────────────────────────────

async def test_update_title_empty(monkeypatch):
    _noop(monkeypatch)
    from infra.errors import AppError
    with pytest.raises(AppError):
        await cg.update_session_title("s1", {"title": "  "})


async def test_update_title_agent(monkeypatch):
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "agent")
    aws = AsyncMock(return_value={"success": True})
    monkeypatch.setattr("modules.thinking.api_stream.update_session_title", aws)
    out = await cg.update_session_title("s1", {"title": "标题"})
    assert out["success"] is True


async def test_delete_message_agent(monkeypatch):
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "agent")
    aws = AsyncMock(return_value={"success": True})
    monkeypatch.setattr("modules.thinking.api_stream.delete_message", aws)
    out = await cg.delete_message("s1", "m1")
    assert out["success"] is True


async def test_update_message_empty(monkeypatch):
    _noop(monkeypatch)
    from infra.errors import AppError
    with pytest.raises(AppError):
        await cg.update_message("s1", "m1", {"content": ""})


async def test_update_message_agent(monkeypatch):
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "agent")
    aws = AsyncMock(return_value={"success": True})
    monkeypatch.setattr("modules.thinking.api_stream.update_message", aws)
    out = await cg.update_message("s1", "m1", {"content": "新"})
    assert out["success"] is True


async def test_get_status_agent(monkeypatch):
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "agent")
    aws = AsyncMock(return_value={"success": True, "data": {}})
    monkeypatch.setattr("modules.thinking.api_stream.get_status", aws)
    out = await cg.get_status()
    assert out["success"] is True


async def test_get_sessions_agent(monkeypatch):
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "agent")
    aws = AsyncMock(return_value={"success": True, "data": []})
    monkeypatch.setattr("modules.thinking.api_stream.get_sessions", aws)
    out = await cg.get_sessions()
    assert out["success"] is True


async def test_get_session_messages_agent(monkeypatch):
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "agent")
    aws = AsyncMock(return_value={"success": True, "data": []})
    monkeypatch.setattr("modules.thinking.api_stream.get_session_messages", aws)
    out = await cg.get_session_messages("s1")
    assert out["success"] is True


async def test_stop_thinking_chatonly(monkeypatch):
    _noop(monkeypatch)
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "chatonly")
    cg._CHATONLY_TASKS.clear()
    out = await cg.stop_thinking(session_id="s1")
    assert out["success"] is True


async def test_stop_thinking_agent(monkeypatch):
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "agent")
    aws = AsyncMock(return_value={"success": True})
    monkeypatch.setattr("modules.thinking.api_stream.stop_thinking", aws)
    out = await cg.stop_thinking(session_id="s1")
    assert out["success"] is True


# ── 共享记忆库 schema 深分支 / 工具函数 ───────────────────────────────

def test_ensure_shared_schema_inner_lock_return(tmp_path, monkeypatch):
    """外层 False 内层 True → 直接返回（模拟并发已完成迁移）"""
    monkeypatch.setattr(cg, "_schema_checked", False)

    class RacingLock:
        def __enter__(self):
            cg._schema_checked = True
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(cg, "_schema_lock", RacingLock())
    cg.ensure_shared_schema()
    monkeypatch.setattr(cg, "_schema_checked", False)


def test_ensure_shared_schema_alter_error(tmp_path, monkeypatch):
    db = str(tmp_path / "mem.db")
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE chat_sessions (id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE chat_messages (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(cg, "_schema_checked", False)
    monkeypatch.setenv("MEMORY_DB_PATH", db)

    class BoomConn:
        def __init__(self, path):
            self._real = sqlite3.connect(path)

        def execute(self, sql, *a):
            if sql.startswith("ALTER"):
                raise RuntimeError("alter fail")
            return self._real.execute(sql, *a)

        def commit(self):
            self._real.commit()

        def close(self):
            self._real.close()

    import sqlite3 as _sqlite3
    real_connect = _sqlite3.connect

    class BoomConn2:
        def __init__(self, path):
            self._real = real_connect(path)

        def execute(self, sql, *a):
            if sql.startswith("ALTER"):
                raise RuntimeError("alter fail")
            return self._real.execute(sql, *a)

        def commit(self):
            self._real.commit()

        def close(self):
            self._real.close()

    monkeypatch.setattr("sqlite3.connect", BoomConn2)
    cg.ensure_shared_schema()  # 迁移异常被捕获，不置 flag
    assert cg._schema_checked is False
    monkeypatch.setattr(cg, "_schema_checked", False)


def test_get_chat_session_repo(monkeypatch):
    repo = MagicMock()
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: repo)
    assert cg._get_chat_session_repo() is repo


def test_get_chat_thinker_inner_lock_return(monkeypatch):
    """外层 None 内层非 None → 直接返回已建实例"""
    thinker = object()
    monkeypatch.setattr(cg, "_chat_thinker", None)

    class RacingLock:
        def __enter__(self):
            cg._chat_thinker = thinker
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(cg, "_chat_thinker_lock", RacingLock())
    assert cg._get_chat_thinker() is thinker
    monkeypatch.setattr(cg, "_chat_thinker", None)


# ══════════════════════════════════════════════════════════════════════════
# 以下为独立运行本文件时兜底的覆盖补充（_resolve_mode / schema / 路由 chatonly）
# ══════════════════════════════════════════════════════════════════════════

def test_resolve_mode_variants(monkeypatch):
    monkeypatch.delenv("CORTEX_MODE", raising=False)
    from config.settings import settings
    for v in ("chatonly", "chat_only", "chat-only"):
        monkeypatch.setattr(settings, "CORTEX_MODE", v)
        assert cg._resolve_mode() == "chatonly"
    monkeypatch.setattr(settings, "CORTEX_MODE", "agent")
    assert cg._resolve_mode() == "agent"
    monkeypatch.setattr(settings, "CORTEX_MODE", "")
    assert cg._resolve_mode() == "agent"


def test_ensure_shared_schema_already_checked(monkeypatch):
    monkeypatch.setattr(cg, "_schema_checked", True)
    cg.ensure_shared_schema()  # 直接返回
    monkeypatch.setattr(cg, "_schema_checked", False)


def test_ensure_shared_schema_full_migration(tmp_path, monkeypatch):
    import sqlite3
    db = str(tmp_path / "full.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE chat_sessions (id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE chat_messages (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(cg, "_schema_checked", False)
    monkeypatch.setenv("MEMORY_DB_PATH", db)
    cg.ensure_shared_schema()
    conn = sqlite3.connect(db)
    assert {r[1] for r in conn.execute("PRAGMA table_info(chat_sessions)")} >= {"execution_mode", "metadata_json"}
    assert {r[1] for r in conn.execute("PRAGMA table_info(chat_messages)")} >= {"tier", "metadata_json"}
    conn.close()
    assert cg._schema_checked is True
    monkeypatch.setattr(cg, "_schema_checked", False)


def test_ensure_shared_schema_connect_fail(monkeypatch):
    monkeypatch.setattr(cg, "_schema_checked", False)
    monkeypatch.delenv("MEMORY_DB_PATH", raising=False)
    monkeypatch.setattr("sqlite3.connect", MagicMock(side_effect=RuntimeError("no db")))
    cg.ensure_shared_schema()  # 连接失败静默
    monkeypatch.setattr(cg, "_schema_checked", False)


def test_ensure_shared_schema_missing_tables(tmp_path, monkeypatch):
    import sqlite3
    db = str(tmp_path / "nope.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE unrelated (x TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(cg, "_schema_checked", False)
    monkeypatch.setenv("MEMORY_DB_PATH", db)
    cg.ensure_shared_schema()  # 无表 → 不置 flag
    assert cg._schema_checked is False
    monkeypatch.setattr(cg, "_schema_checked", False)


# ── _consume_turn：connection_lost / thinking / mental 发送 ─────────────

async def test_consume_turn_connection_lost_sends_error(monkeypatch):
    ws = FakeWS()
    repo = MagicMock()
    repo.save_message = MagicMock(return_value="m1")
    thinker = MagicMock()
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: False)
    await cg._consume_turn(ws, "s1", repo, thinker, "hi")
    assert any(s["event"] == "connection_lost" for s in ws.sent)


async def test_consume_turn_thinking_mental_tokens(monkeypatch):
    ws = FakeWS()
    repo = MagicMock()
    repo.save_message = MagicMock(side_effect=lambda sid, role, c: f"id_{role}")
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        await queue.put({"type": "thinking", "content": "思考", "identity_name": "x", "tier": "large"})
        await queue.put({"type": "mental", "content": "独白"})
        await queue.put({"type": "done"})

    thinker.think = fake_think
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: True)
    await cg._consume_turn(ws, "s1", repo, thinker, "hi")
    types = [s["type"] for s in ws.sent]
    assert "thinking" in types
    assert "mental" in types
    # 心理活动持久化：save_message 以 role="mental" 调用（切换会话后可恢复历史）
    saved_roles = [c.args[1] for c in repo.save_message.call_args_list]
    assert "mental" in saved_roles
    assert "thought" in saved_roles  # 思考过程也持久化（与 agent 模式一致，§58 同类）


# ── _chatonly_ws：附件错误 / 停止取消 ───────────────────────────────────

async def test_chatonly_ws_attachment_format_error(monkeypatch):
    _noop(monkeypatch)
    monkeypatch.setattr("modules.thinking.attachment_handler.validate_attachments", lambda a: "格式不对")
    monkeypatch.setattr(cg, "_consume_turn", AsyncMock())
    ws = FakeWS(incoming=[json.dumps({"type": "input", "content": "", "attachments": [{"type": "image"}]})])
    await cg._chatonly_ws(ws, "s1")
    assert any(s["event"] == "error" for s in ws.sent)


async def test_chatonly_ws_stop_with_active_task(monkeypatch):
    _noop(monkeypatch)

    async def slow_consume(websocket, sid, repo, thinker, content):
        await asyncio.sleep(30)

    monkeypatch.setattr(cg, "_consume_turn", slow_consume)
    ws = FakeWS(incoming=[
        json.dumps({"type": "input", "content": "任务"}),
        json.dumps({"type": "stop"}),
    ])
    await cg._chatonly_ws(ws, "s1")
    assert any(s["event"] == "stopped" for s in ws.sent)


# ── SSE 消息流 / 任务取消 ──────────────────────────────────────────────

async def test_chatonly_sse_message_done_flow(monkeypatch):
    repo = _noop(monkeypatch)
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        await queue.put({"type": "message", "content": "答案"})
        await queue.put({"type": "done"})

    thinker.think = fake_think
    monkeypatch.setattr(cg, "_get_chat_thinker", lambda: thinker)
    events = [e async for e in cg._chatonly_sse("s1", "问题")]
    assert any(e["event"] == "assistant_message" for e in events)
    assert any(e["event"] == "done" for e in events)


async def test_chatonly_sse_streams_chunk_frames(monkeypatch):
    """SSE 路线也要边生成边推 `chunk`——整段憋到结束等于没有流式。

    与 WS 分支（`_consume_turn._flush_reply_chunk`）同语义：句末即推，
    终稿仍整段落库为 `assistant_message`（chunk 只是预览）。
    """
    repo = _noop(monkeypatch)
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        await queue.put({"type": "message", "content": "第一句话。"})
        await queue.put({"type": "message", "content": "第二句话！"})
        await queue.put({"type": "done"})

    thinker.think = fake_think
    monkeypatch.setattr(cg, "_get_chat_thinker", lambda: thinker)
    events = [e async for e in cg._chatonly_sse("s1", "问题")]

    chunks = [json.loads(e["data"])["content"] for e in events if e["event"] == "chunk"]
    assert chunks == ["第一句话。", "第二句话！"]
    finals = [json.loads(e["data"]) for e in events if e["event"] == "assistant_message"]
    assert finals and finals[0]["content"] == "第一句话。第二句话！"
    assert events[-1]["event"] == "done"


async def test_chatonly_sse_chunk_buffers_below_threshold(monkeypatch):
    """不到 40 字且无句末 → 先攒着，不逐 token 推帧（避免刷屏）。"""
    repo = _noop(monkeypatch)
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        await queue.put({"type": "message", "content": "abc"})
        await queue.put({"type": "message", "content": "def"})
        await queue.put({"type": "done"})

    thinker.think = fake_think
    monkeypatch.setattr(cg, "_get_chat_thinker", lambda: thinker)
    events = [e async for e in cg._chatonly_sse("s1", "问题")]

    chunks = [json.loads(e["data"])["content"] for e in events if e["event"] == "chunk"]
    assert chunks == ["abcdef"]          # done 时补一帧，不丢尾巴


async def test_chatonly_sse_flushes_chunk_before_error(monkeypatch):
    """出错前先把已生成的正文推出去（否则用户看不到已经生成的部分）。"""
    repo = _noop(monkeypatch)
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        await queue.put({"type": "message", "content": "半句话"})
        await queue.put({"type": "error", "content": "失败"})

    thinker.think = fake_think
    monkeypatch.setattr(cg, "_get_chat_thinker", lambda: thinker)
    events = [e async for e in cg._chatonly_sse("s1", "问题")]

    names = [e["event"] for e in events]
    assert "chunk" in names and "error" in names
    assert names.index("chunk") < names.index("error")
    assert json.loads(events[names.index("chunk")]["data"])["content"] == "半句话"


async def test_chatonly_sse_error_token(monkeypatch):
    repo = _noop(monkeypatch)
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        await queue.put({"type": "error", "content": "失败"})

    thinker.think = fake_think
    monkeypatch.setattr(cg, "_get_chat_thinker", lambda: thinker)
    events = [e async for e in cg._chatonly_sse("s1", "问题")]
    assert any(e["event"] == "error" for e in events)


async def test_chatonly_sse_errored_task_ends(monkeypatch):
    repo = _noop(monkeypatch)
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        pass  # 无 token 直接结束

    thinker.think = fake_think
    monkeypatch.setattr(cg, "_get_chat_thinker", lambda: thinker)
    events = [e async for e in cg._chatonly_sse("s1", "问题")]
    assert any(e["event"] == "error" for e in events)


# ── 路由：chatonly 全分支 ──────────────────────────────────────────────

def _set_chatonly(monkeypatch):
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "chatonly")


async def test_route_create_session_chatonly(monkeypatch):
    _set_chatonly(monkeypatch)
    repo = _noop(monkeypatch)
    monkeypatch.setattr(cg, "ensure_shared_schema", lambda: None)
    out = await cg.create_session()
    assert out["success"] is True
    assert out["data"]["session_id"].startswith("ses_")


async def test_route_create_session_agent(monkeypatch):
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "agent")
    aws = AsyncMock(return_value={"success": True, "data": {"session_id": "x"}})
    monkeypatch.setattr("modules.thinking.api_stream.create_session", aws)
    out = await cg.create_session()
    assert out["data"]["session_id"] == "x"


async def test_route_sse_chatonly(monkeypatch):
    _set_chatonly(monkeypatch)
    repo = _noop(monkeypatch)
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        await queue.put({"type": "done"})

    thinker.think = fake_think
    monkeypatch.setattr(cg, "_get_chat_thinker", lambda: thinker)
    resp = await cg.sse_session_get("s1", "问题")
    from sse_starlette.sse import EventSourceResponse
    assert isinstance(resp, EventSourceResponse)


async def test_route_get_context_db_fallback(monkeypatch):
    _set_chatonly(monkeypatch)
    # get_context 走公共层 load_dialog_from_db
    repo = MagicMock()
    repo.get_messages = MagicMock(return_value=[{"role": "user", "content": "恢复"}])
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: repo)
    out = await cg.get_context("s1")
    assert out["data"]["count"] == 1


async def test_route_get_context_db_fallback_error(monkeypatch):
    _set_chatonly(monkeypatch)
    repo = MagicMock()
    repo.get_messages = MagicMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: repo)
    out = await cg.get_context("s1")
    assert out["data"]["count"] == 0


async def test_route_get_context_agent(monkeypatch):
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "agent")
    aws = AsyncMock(return_value={"success": True, "data": {"count": 2}})
    monkeypatch.setattr("modules.thinking.api_stream.get_context", aws)
    out = await cg.get_context("s1")
    assert out["data"]["count"] == 2


async def test_route_close_session_not_protected(monkeypatch):
    repo = MagicMock()
    monkeypatch.setattr(cg, "_get_chat_session_repo", lambda: repo)
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "chatonly")
    resp = await cg.close_session("pet_main")
    assert resp.get("success") is True


async def test_route_batch_delete_invalid_body():
    resp = await cg.batch_delete_sessions({})
    assert resp.status_code == 422
    resp2 = await cg.batch_delete_sessions({"session_ids": "not-a-list"})
    assert resp2.status_code == 422


async def test_route_clear_session_messages(monkeypatch):
    repo = _noop(monkeypatch)
    repo.clear_messages = MagicMock()
    out = await cg.clear_session_messages("s1")
    assert out["success"] is True
    repo.clear_messages.assert_called_once_with("s1")


async def test_route_get_tasks_and_set_tasks(monkeypatch):
    repo = _noop(monkeypatch)
    repo.get_scheduled_tasks = MagicMock(return_value={"tasks": []})
    repo.set_scheduled_tasks = MagicMock()
    out = await cg.get_tasks("s1")
    assert out["success"] is True
    out2 = await cg.set_tasks("s1", {"tasks": {"tasks": [{"id": "t"}]}})
    assert out2["success"] is True
    resp = await cg.set_tasks("s1", {"tasks": "bad"})
    assert resp.status_code == 422



async def test_route_update_title_chatonly(monkeypatch):
    _set_chatonly(monkeypatch)
    repo = _noop(monkeypatch)
    repo.set_session_title = MagicMock()
    out = await cg.update_session_title("s1", {"title": "新标题"})
    assert out["success"] is True
    repo.set_session_title.assert_called_once_with("s1", "新标题")


async def test_route_delete_message_chatonly(monkeypatch):
    _set_chatonly(monkeypatch)
    repo = _noop(monkeypatch)
    repo.delete_message = MagicMock()
    out = await cg.delete_message("s1", "m1")
    assert out["success"] is True


async def test_route_update_message_chatonly(monkeypatch):
    _set_chatonly(monkeypatch)
    repo = _noop(monkeypatch)
    repo.update_message = MagicMock()
    out = await cg.update_message("s1", "m1", {"content": "新"})
    assert out["success"] is True


async def test_route_get_status_chatonly(monkeypatch):
    _set_chatonly(monkeypatch)
    out = await cg.get_status()
    assert out["data"]["running"] is True


async def test_route_get_sessions_chatonly(monkeypatch):
    _set_chatonly(monkeypatch)
    repo = _noop(monkeypatch)
    repo.delete_empty_sessions = MagicMock()
    repo.get_all_sessions = MagicMock(return_value=[{"session_id": "s1"}])
    monkeypatch.setattr("modules.thinking.api_stream.connection_manager", MagicMock())
    out = await cg.get_sessions()
    assert len(out["data"]) == 1


async def test_route_get_session_messages_chatonly(monkeypatch):
    _set_chatonly(monkeypatch)
    repo = _noop(monkeypatch)
    repo.get_messages = MagicMock(return_value=[{"id": "m1"}])
    out = await cg.get_session_messages("s1")
    assert out["data"] == [{"id": "m1"}]


async def test_route_stop_chatonly_with_task(monkeypatch):
    _set_chatonly(monkeypatch)
    task = asyncio.create_task(asyncio.sleep(10))
    cg._CHATONLY_TASKS["s1"] = task
    out = await cg.stop_thinking(session_id="s1")
    assert out["success"] is True
    assert out["data"]["cancelled"] is True
    cg._CHATONLY_TASKS.clear()


async def test_get_session_graph_persists_on_read(monkeypatch):
    """前端读图谱时持久化到 metadata——切换会话/重启后可恢复一致（§58 图谱链路）"""
    from modules.thinking.session_graph import get_session_graph_store
    store = get_session_graph_store()
    store.record("persist_s1", "large_primary", "总指挥", "large", "", "assistant", "你好")
    store.record("persist_s1", "code_supervisor", "代码主管", "supervisor", "large_primary", "assistant", "收到")
    meta_saved = []
    def fake_set(sid, meta):
        meta_saved.append(meta)
        return True
    from modules.database.session_repo import get_session_repo
    monkeypatch.setattr(get_session_repo(), "set_session_metadata", fake_set)
    out = await cg.get_session_graph("persist_s1")
    assert len(out["data"]["graph"]["nodes"]) == 3  # 用户 + 总指挥 + 代码主管
    assert meta_saved  # 返回前持久化到 metadata
    assert meta_saved[0]["session_graph"]["nodes"]
    store.reset("persist_s1")


async def test_get_session_graph_restore_then_persist(monkeypatch):
    """内存空时从 metadata 恢复，并再次持久化（一致性闭环）"""
    from modules.thinking.session_graph import SessionGraphStore
    fake_store = SessionGraphStore()
    snap = {"nodes": [{"id": "m1", "label": "总指挥", "tier": "large",
                       "count": 1, "last_content": "", "last_ts": 0}], "edges": []}
    meta = {"session_graph": snap}
    monkeypatch.setattr("modules.thinking.session_graph.get_session_graph_store",
                        lambda: fake_store)
    monkeypatch.setattr("modules.thinking.chat_gateway._get_chat_session_repo",
                        lambda: MagicMock(get_session_metadata=lambda sid: meta))
    monkeypatch.setattr("modules.database.session_repo.get_session_repo",
                        lambda: MagicMock(set_session_metadata=lambda sid, m: True))
    out = await cg.get_session_graph("restore_s1")
    assert out["data"]["graph"]["nodes"]
    assert out["data"]["graph"]["nodes"][0]["label"] == "总指挥"
