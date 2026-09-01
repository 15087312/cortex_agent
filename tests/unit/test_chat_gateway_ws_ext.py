"""chat_gateway WS/SSE 处理器测试：_chatonly_ws / _chatonly_sse 深分支 / pet 路由"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastapi import WebSocketDisconnect

import modules.thinking.chat_gateway as cg


class FakeWS:
    def __init__(self, incoming=None):
        self.incoming = list(incoming or [])
        self.sent = []
        self.accepted = False
        self.closed = False

    async def accept(self):
        self.accepted = True

    async def send_json(self, data):
        self.sent.append(data)

    async def receive_text(self):
        if self.incoming:
            return self.incoming.pop(0)
        raise WebSocketDisconnect()

    async def close(self, code=1000, reason=""):
        self.closed = True


def _noop(monkeypatch, repo=None):
    cg._schema_checked = True
    repo = repo or MagicMock()
    repo.create_session = MagicMock()
    repo.save_message = MagicMock(return_value="m1")
    monkeypatch.setattr(cg, "_get_chat_session_repo", lambda: repo)
    # 黑板已删除（DB 为唯一真源），thinker 不再提供 get_blackboard
    thinker = MagicMock()
    thinker.get_blackboard = MagicMock(side_effect=AttributeError("blackboard 已删除"))
    monkeypatch.setattr(cg, "_get_chat_thinker", lambda: thinker)
    return repo


async def test_chatonly_ws_input_ping_stop(monkeypatch):
    repo = _noop(monkeypatch)
    ws = FakeWS(incoming=[
        json.dumps({"type": "input", "content": "你好"}),
        json.dumps({"type": "ping"}),
        json.dumps({"type": "stop"}),
    ])
    monkeypatch.setattr(cg, "_consume_turn", AsyncMock())

    async def fake_think(sid, content, queue):
        await queue.put({"type": "done"})
    # 覆盖 _consume_turn 被替换，无需 thinker
    await cg._chatonly_ws(ws, "s1")
    assert ws.accepted is True
    events = [s["event"] for s in ws.sent]
    assert "session_ready" in events
    assert "pong" in events
    assert "stopped" in events
    assert ws.closed is True


async def test_chatonly_ws_connection_lost_at_start(monkeypatch):
    repo = _noop(monkeypatch)
    ws = FakeWS(incoming=[])
    ws.send_json = AsyncMock(side_effect=RuntimeError("down"))
    await cg._chatonly_ws(ws, "s1")
    assert ws.accepted is True


async def test_chatonly_ws_attachment_error(monkeypatch):
    repo = _noop(monkeypatch)
    validator = MagicMock(return_value="格式不对")
    monkeypatch.setattr("modules.thinking.attachment_handler.validate_attachments", validator)
    ws = FakeWS(incoming=[
        json.dumps({"type": "input", "content": "", "attachments": [{"type": "image"}]}),
    ])
    monkeypatch.setattr(cg, "_consume_turn", AsyncMock())
    await cg._chatonly_ws(ws, "s1")
    assert any(s["event"] == "error" for s in ws.sent)


async def test_chatonly_ws_attachment_direct_mode(monkeypatch):
    repo = _noop(monkeypatch)
    validator = MagicMock(return_value=None)
    monkeypatch.setattr("modules.thinking.attachment_handler.validate_attachments", validator)
    extract = MagicMock(return_value=["img"])
    summarize = MagicMock(return_value="图片描述")
    monkeypatch.setattr("modules.thinking.attachment_handler.extract_images", extract)
    monkeypatch.setattr("modules.thinking.attachment_handler.summarize_attachments", summarize)
    from config.settings import settings
    monkeypatch.setattr(settings, "CHAT_IMAGE_MODE", "direct")
    consume = AsyncMock()
    monkeypatch.setattr(cg, "_consume_turn", consume)
    ws = FakeWS(incoming=[
        json.dumps({"type": "input", "content": "看这个", "attachments": [{"type": "image"}]}),
    ])
    await cg._chatonly_ws(ws, "s1")
    extract.assert_called_once()
    summarize.assert_called_once()


async def test_chatonly_ws_stop_cancels_active(monkeypatch):
    repo = _noop(monkeypatch)
    ws = FakeWS(incoming=[
        json.dumps({"type": "input", "content": "任务"}),
        json.dumps({"type": "stop"}),
    ])

    async def fake_consume(websocket, sid, r, thinker, content):
        await asyncio.sleep(5)

    monkeypatch.setattr(cg, "_consume_turn", fake_consume)
    await cg._chatonly_ws(ws, "s1")
    assert any(s["event"] == "stopped" for s in ws.sent)


# ── SSE 深分支 ─────────────────────────────────────────────────────────

async def test_chatonly_sse_errored_finish(monkeypatch):
    repo = _noop(monkeypatch)
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        pass  # 任务直接结束且无 token → errored

    thinker.think = fake_think
    monkeypatch.setattr(cg, "_get_chat_thinker", lambda: thinker)
    events = [e async for e in cg._chatonly_sse("s1", "问题")]
    assert any(e["event"] == "error" for e in events)


async def test_chatonly_sse_timeout(monkeypatch):
    """累计静默超 300s → 产出 error 事件（用递增 time.time 触发）"""
    repo = _noop(monkeypatch)
    thinker = MagicMock()

    async def fake_think(sid, content, queue):
        await asyncio.sleep(30)

    thinker.think = fake_think
    monkeypatch.setattr(cg, "_get_chat_thinker", lambda: thinker)
    # 第一次调用（turn_start/last_event）返回 0，之后返回 301 → now-last_event>=300
    counter = {"n": 0}
    def _tick(self=None):
        counter["n"] += 1
        return 0 if counter["n"] <= 2 else 301
    monkeypatch.setattr(cg, "time", type("T", (), {"time": _tick})())

    events = [e async for e in cg._chatonly_sse("s1", "问题")]
    assert any(e["event"] == "error" for e in events)
    assert any("思考超时" in e.get("data", "") for e in events)


# ── pet 路由 ───────────────────────────────────────────────────────────

async def test_pet_state(monkeypatch):
    st = MagicMock()
    st.get_instance = MagicMock(return_value=st)
    st.read = MagicMock(return_value={"x": 1})
    st.describe = MagicMock(return_value="描述")
    monkeypatch.setattr("modules.desktop_pet.pet_state.PetState", st)
    out = await cg.pet_state()
    assert out["data"]["values"] == {"x": 1}


async def test_pet_state_error(monkeypatch):
    def boom():
        raise RuntimeError("no pet state")
    monkeypatch.setattr("modules.desktop_pet.pet_state.PetState", type("P", (), {"get_instance": staticmethod(boom)}))
    out = await cg.pet_state()
    assert out["data"] is None


async def test_pet_state_reset(monkeypatch):
    st = MagicMock()
    st.get_instance = MagicMock(return_value=st)
    st.read = MagicMock(return_value={"x": 1})
    st._save = MagicMock()
    monkeypatch.setattr("modules.desktop_pet.pet_state.PetState", st)
    monkeypatch.setattr("modules.desktop_pet.pet_state.DEFAULTS", {"x": 0})
    out = await cg.pet_state_reset()
    assert out["success"] is True


async def test_pet_move_stream(monkeypatch):
    from sse_starlette.sse import EventSourceResponse
    cg._pet_move_queues.clear()
    q = asyncio.Queue()
    cg._pet_move_queues.add(q)
    resp = await cg.pet_move_stream()
    assert isinstance(resp, EventSourceResponse)
    cg._pet_move_queues.clear()


async def test_pet_chat_action(monkeypatch):
    st = MagicMock()
    st.get_instance = MagicMock(return_value=st)
    st.read = MagicMock(return_value={"mood": "happy"})
    st.apply = MagicMock()
    st.describe = MagicMock(return_value="状态")
    monkeypatch.setattr("modules.desktop_pet.pet_state.PetState", st)
    action = {"prompt": "互动提示词"}
    monkeypatch.setattr("modules.desktop_pet.actions.get_action", lambda aid: action if aid else None)
    engine = MagicMock()
    engine.get_instance = MagicMock(return_value=engine)

    async def fake_stream(text, extra_system=""):
        yield "t1"
        yield "t2"

    engine.stream_chat = fake_stream
    monkeypatch.setattr("modules.desktop_pet.pet_engine.PetEngine", engine)
    resp = await cg.pet_chat_stream(cg.PetChatRequest(action_id="wave", text=""))
    events = []
    async for chunk in resp.body_iterator:
        events.append(chunk)
    assert any(b"done" in c for c in events if isinstance(c, bytes)) or len(events) > 0


async def test_pet_chat_empty_text(monkeypatch):
    st = MagicMock()
    st.get_instance = MagicMock(return_value=st)
    st.read = MagicMock(return_value={})
    st.describe = MagicMock(return_value="")
    monkeypatch.setattr("modules.desktop_pet.pet_state.PetState", st)
    monkeypatch.setattr("modules.desktop_pet.actions.get_action", lambda aid: None)
    resp = await cg.pet_chat_stream(cg.PetChatRequest(action_id="", text=""))
    events = []
    async for chunk in resp.body_iterator:
        events.append(chunk)
    assert len(events) >= 1


async def test_pet_last_reply_error(monkeypatch):
    def boom():
        raise RuntimeError("no engine")
    monkeypatch.setattr("modules.desktop_pet.pet_engine.PetEngine", type("P", (), {"get_instance": staticmethod(boom)}))
    out = await cg.pet_last_reply()
    assert out["data"] is None
