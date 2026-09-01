"""chat_gateway 测试：模式解析 / schema 对齐 / 事件流 / 路由分流"""
import asyncio
import json
import os
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import modules.thinking.chat_gateway as cg


# ── _resolve_mode ──────────────────────────────────────────────────────

def test_resolve_mode_chatonly(monkeypatch):
    import config.settings as cfg_mod
    monkeypatch.setenv("CORTEX_MODE", "chatonly")
    assert cg._resolve_mode() == "chatonly"
    monkeypatch.setenv("CORTEX_MODE", "chat_only")
    assert cg._resolve_mode() == "chatonly"
    monkeypatch.setenv("CORTEX_MODE", "chat-only")
    assert cg._resolve_mode() == "chatonly"


def test_resolve_mode_agent(monkeypatch):
    from config.settings import settings
    monkeypatch.setenv("CORTEX_MODE", "agent")
    assert cg._resolve_mode() == "agent"
    monkeypatch.setattr(settings, "CORTEX_MODE", "agent")
    monkeypatch.delenv("CORTEX_MODE", raising=False)
    assert cg._resolve_mode() == "agent"


# ── ensure_shared_schema ───────────────────────────────────────────────

def test_ensure_shared_schema_migrates(tmp_path, monkeypatch):
    db = str(tmp_path / "mem.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE chat_sessions (id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE chat_messages (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(cg, "_schema_checked", False)
    monkeypatch.setenv("MEMORY_DB_PATH", db)
    cg.ensure_shared_schema()
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chat_sessions)").fetchall()}
    assert "execution_mode" in cols and "metadata_json" in cols
    mcols = {r[1] for r in conn.execute("PRAGMA table_info(chat_messages)").fetchall()}
    assert "tier" in mcols and "metadata_json" in mcols
    conn.close()
    assert cg._schema_checked is True


def test_ensure_shared_schema_idempotent(tmp_path, monkeypatch):
    db = str(tmp_path / "mem2.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE chat_sessions (id TEXT PRIMARY KEY, execution_mode TEXT)")
    conn.execute("CREATE TABLE chat_messages (id TEXT PRIMARY KEY, tier TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(cg, "_schema_checked", False)
    monkeypatch.setenv("MEMORY_DB_PATH", db)
    cg.ensure_shared_schema()
    assert cg._schema_checked is True
    cg.ensure_shared_schema()  # 二次调用幂等


def test_ensure_shared_schema_missing_tables(tmp_path, monkeypatch):
    db = str(tmp_path / "mem3.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE unrelated (x TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(cg, "_schema_checked", False)
    monkeypatch.setenv("MEMORY_DB_PATH", db)
    cg.ensure_shared_schema()  # 表不存在 → 不置 flag
    assert cg._schema_checked is False


def test_ensure_shared_schema_error(monkeypatch):
    monkeypatch.setattr(cg, "_schema_checked", False)
    monkeypatch.setenv("MEMORY_DB_PATH", "/nonexistent/dir/x.db")
    cg.ensure_shared_schema()  # 不抛异常


# ── 工具函数 ───────────────────────────────────────────────────────────

def test_envelope():
    e = cg._envelope("s1", "ack", "received", "内容", "system", {"k": 1})
    assert e["type"] == "ack"
    assert e["data"] == {"k": 1}


async def test_safe_ws_send():
    ws = MagicMock()
    ws.send_json = AsyncMock(return_value=None)
    assert await cg._safe_ws_send(ws, {}) is True
    ws2 = MagicMock()
    ws2.send_json = AsyncMock(side_effect=RuntimeError("down"))
    assert await cg._safe_ws_send(ws2, {}) is False


def test_get_chat_thinker_lazy(monkeypatch):
    monkeypatch.setattr(cg, "_chat_thinker", None)
    thinker = MagicMock()
    monkeypatch.setattr("modules.thinking.chat_light.continuous_thinker.ContinuousThinker", lambda: thinker)
    assert cg._get_chat_thinker() is thinker
    assert cg._get_chat_thinker() is thinker


# ── _consume_turn ──────────────────────────────────────────────────────

class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)
        return None


async def test_consume_turn_message_flow(monkeypatch):
    ws = FakeWS()
    repo = MagicMock()
    repo.save_message = MagicMock(side_effect=lambda sid, role, c: f"id_{role}")
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        await queue.put({"type": "message", "content": "你"})
        await queue.put({"type": "message", "content": "好"})
        await queue.put({"type": "thinking", "content": "思考中", "identity_name": "总指挥", "tier": "large"})
        await queue.put({"type": "mental", "content": "心理活动"})
        await queue.put({"type": "done"})

    thinker.think = fake_think
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: True)
    await cg._consume_turn(ws, "s1", repo, thinker, "你好")
    types = [s["type"] for s in ws.sent]
    assert "ack" in types
    assert "message" in types
    assert "thinking" in types
    assert "mental" in types
    assert "done" in types


async def test_consume_turn_error_flow(monkeypatch):
    ws = FakeWS()
    repo = MagicMock()
    repo.save_message = MagicMock(return_value="m1")
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        await queue.put({"type": "error", "content": "模型出错"})

    thinker.think = fake_think
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: True)
    await cg._consume_turn(ws, "s1", repo, thinker, "你好")
    assert any(s["type"] == "error" for s in ws.sent)


async def test_consume_turn_connection_lost(monkeypatch):
    ws = FakeWS()
    repo = MagicMock()
    repo.save_message = MagicMock(return_value="m1")
    thinker = MagicMock()
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: False)
    await cg._consume_turn(ws, "s1", repo, thinker, "你好")
    assert any(s["event"] == "connection_lost" for s in ws.sent)


# ── _chatonly_sse ──────────────────────────────────────────────────────

async def test_chatonly_sse_flow(monkeypatch):
    repo = MagicMock()
    repo.save_message = MagicMock(return_value="m")
    monkeypatch.setattr(cg, "_get_chat_session_repo", lambda: repo)
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        await queue.put({"type": "message", "content": "答案"})
        await queue.put({"type": "done"})

    thinker.think = fake_think
    monkeypatch.setattr(cg, "_get_chat_thinker", lambda: thinker)
    events = [e async for e in cg._chatonly_sse("s1", "问题")]
    assert any(e["event"] == "assistant_message" for e in events)
    assert any(e["event"] == "done" for e in events)


async def test_chatonly_sse_error(monkeypatch):
    repo = MagicMock()
    monkeypatch.setattr(cg, "_get_chat_session_repo", lambda: repo)
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        await queue.put({"type": "error", "content": "内部错误"})

    thinker.think = fake_think
    monkeypatch.setattr(cg, "_get_chat_thinker", lambda: thinker)
    events = [e async for e in cg._chatonly_sse("s1", "问题")]
    assert any(e["event"] == "error" for e in events)


# ── 路由（chatonly 分支）──────────────────────────────────────────────

def _set_chatonly(monkeypatch):
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "chatonly")


async def test_route_create_session_chatonly(monkeypatch):
    _set_chatonly(monkeypatch)
    repo = MagicMock()
    repo.create_session = MagicMock()
    monkeypatch.setattr(cg, "_get_chat_session_repo", lambda: repo)
    monkeypatch.setattr(cg, "ensure_shared_schema", lambda: None)
    out = await cg.create_session()
    assert out["success"] is True
    assert out["data"]["session_id"].startswith("ses_")


async def test_route_create_session_agent(monkeypatch):
    monkeypatch.setattr(cg, "_resolve_mode", lambda: "agent")
    api_stream = MagicMock()
    api_stream.create_session = AsyncMock(return_value={"success": True, "data": {"session_id": "x"}})
    monkeypatch.setattr("modules.thinking.api_stream", api_stream)
    out = await cg.create_session()
    assert out["data"]["session_id"] == "x"


async def test_route_get_context_chatonly(monkeypatch):
    _set_chatonly(monkeypatch)
    # get_context 走公共层 load_dialog_from_db，需 mock 模块级 get_session_repo
    repo = MagicMock()
    repo.get_messages = MagicMock(return_value=[{"role": "user", "content": "hi"}])
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: repo)
    out = await cg.get_context("s1")
    assert out["data"]["count"] == 1


async def test_route_close_session_pet_protected(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "DESKTOP_PET_SESSION_ID", "pet_main")
    resp = await cg.close_session("pet_main")
    assert resp.status_code == 400


async def test_route_close_session_chatonly(monkeypatch):
    _set_chatonly(monkeypatch)
    repo = MagicMock()
    monkeypatch.setattr(cg, "_get_chat_session_repo", lambda: repo)
    out = await cg.close_session("s1")
    assert out["success"] is True


async def test_route_batch_delete(monkeypatch):
    repo = MagicMock()
    repo.delete_session = MagicMock()
    monkeypatch.setattr(cg, "_get_chat_session_repo", lambda: repo)
    from config.settings import settings
    monkeypatch.setattr(settings, "DESKTOP_PET_SESSION_ID", "pet_main")
    out = await cg.batch_delete_sessions({"session_ids": ["a", "pet_main", "b"]})
    assert out["data"]["deleted"] == ["a", "b"]
    resp = await cg.batch_delete_sessions({})
    assert resp.status_code == 422


async def test_route_pet_move(monkeypatch):
    cg._pet_move_queues.clear()
    q = asyncio.Queue()
    cg._pet_move_queues.add(q)
    out = await cg.pet_move(cg.PetMoveRequest(dx=1.0, dy=2.0, active=True))
    assert out["success"] is True
    m = q.get_nowait()
    assert m["dx"] == 1.0 and m["active"] is True


async def test_route_pet_last_reply(monkeypatch):
    engine = MagicMock()
    engine.get_instance = MagicMock(return_value=MagicMock(last_reply="你好"))
    monkeypatch.setattr("modules.desktop_pet.pet_engine.PetEngine", engine)
    out = await cg.pet_last_reply()
    assert out["data"] == "你好"


async def test_route_pet_actions():
    out = await cg.pet_actions()
    assert out["success"] is True


async def test_route_clear_session_messages(monkeypatch):
    repo = MagicMock()
    repo.clear_messages = MagicMock()
    monkeypatch.setattr(cg, "_get_chat_session_repo", lambda: repo)
    out = await cg.clear_session_messages("s1")
    assert out["success"] is True


async def test_route_get_tasks_set_tasks(monkeypatch):
    repo = MagicMock()
    repo.get_scheduled_tasks = MagicMock(return_value={"tasks": []})
    repo.set_scheduled_tasks = MagicMock()
    monkeypatch.setattr(cg, "_get_chat_session_repo", lambda: repo)
    out = await cg.get_tasks("s1")
    assert out["success"] is True
    out2 = await cg.set_tasks("s1", {"tasks": {"tasks": [{"id": "t"}]}})
    assert out2["success"] is True
    resp = await cg.set_tasks("s1", {"tasks": "bad"})
    assert resp.status_code == 422


async def test_route_outreach_config(monkeypatch):
    repo = MagicMock()
    repo.get_outreach_config = MagicMock(return_value={})
    repo.set_outreach_config = MagicMock(return_value=True)
    repo.get_all_sessions = MagicMock(return_value=[])
    monkeypatch.setattr(cg, "_get_chat_session_repo", lambda: repo)
    out = await cg.get_outreach_config("s1")
    assert out["success"] is True
    out2 = await cg.set_outreach_config("s1", {"outreach": {"enabled": True, "cooldown_minutes": 5, "schedule": {"time": "10:00"}, "screen": {"probability": 0.5}, "idle": {"idle_minutes": 10}, "time_windows": [{"start": "09:00", "end": "18:00"}]}})
    assert out2["success"] is True
    resp = await cg.set_outreach_config("s1", {"outreach": "bad"})
    assert resp.status_code == 422


async def test_route_proactive_logs(monkeypatch):
    monkeypatch.setattr("modules.database.proactive_repo.query_proactive_logs", lambda limit=50, session_id="": [])
    monkeypatch.setattr("modules.database.proactive_repo.count_proactive_logs", lambda: 0)
    out = await cg.get_proactive_logs()
    assert out["success"] is True


async def test_route_update_title_delete_update_message(monkeypatch):
    _set_chatonly(monkeypatch)
    repo = MagicMock()
    monkeypatch.setattr(cg, "_get_chat_session_repo", lambda: repo)
    out = await cg.update_session_title("s1", {"title": "标题"})
    assert out["success"] is True
    out2 = await cg.delete_message("s1", "m1")
    assert out2["success"] is True
    out3 = await cg.update_message("s1", "m1", {"content": "新"})
    assert out3["success"] is True


async def test_route_get_status_and_sessions(monkeypatch):
    _set_chatonly(monkeypatch)
    repo = MagicMock()
    repo.delete_empty_sessions = MagicMock()
    repo.get_all_sessions = MagicMock(return_value=[{"session_id": "s1"}])
    monkeypatch.setattr(cg, "_get_chat_session_repo", lambda: repo)
    out = await cg.get_status()
    assert out["data"]["running"] is True
    out2 = await cg.get_sessions()
    assert len(out2["data"]) == 1


async def test_route_stop_thinking(monkeypatch):
    _set_chatonly(monkeypatch)
    task = asyncio.create_task(asyncio.sleep(10))
    cg._CHATONLY_TASKS["s1"] = task
    out = await cg.stop_thinking(session_id="s1")
    assert out["success"] is True
    cg._CHATONLY_TASKS.clear()


async def test_route_sse_requires_question(monkeypatch):
    _set_chatonly(monkeypatch)
    from api.errors import AppError
    with pytest.raises(AppError):
        await cg.sse_session_get("s1", "")


async def test_route_get_session_messages(monkeypatch):
    _set_chatonly(monkeypatch)
    repo = MagicMock()
    repo.get_messages = MagicMock(return_value=[{"id": "m1"}])
    monkeypatch.setattr(cg, "_get_chat_session_repo", lambda: repo)
    out = await cg.get_session_messages("s1")
    assert out["data"] == [{"id": "m1"}]


async def test_route_get_session_graph(monkeypatch):
    store = MagicMock()
    store.get_graph = MagicMock(return_value={"nodes": [], "edges": []})
    store.restore = MagicMock()
    monkeypatch.setattr("modules.thinking.session_graph.get_session_graph_store", lambda: store)
    repo = MagicMock()
    repo.get_session_metadata = MagicMock(return_value={"session_graph": {"nodes": [1]}})
    monkeypatch.setattr(cg, "_get_chat_session_repo", lambda: repo)
    out = await cg.get_session_graph("s1")
    assert out["success"] is True
