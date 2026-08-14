"""frontend_channel 测试：握手 / 持久化 / 推送 / 统一流程"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import modules.thinking.frontend_channel as fc


class FakeCM:
    def __init__(self, connections=None, loop=None):
        self.active_connections = connections or {}
        self._loop = loop

    def send_json_from_thread(self, sid, event):
        return True

    async def send_json(self, sid, event):
        return True


class FakeSession:
    def __init__(self):
        self.messages = []


class FakeSystem:
    def __init__(self):
        self.sessions = {"sess-1": FakeSession()}

    async def _append_message(self, session_id, role, content):
        session = self.sessions.get(session_id)
        if session is not None:
            session.messages.append((session_id, role, content))
        return f"mid-{len(session.messages) if session else 0}"


def _build_event(*args, **kwargs):
    return {"event": kwargs.get("event", "x"), "session_id": kwargs.get("session_id", "")}


@pytest.fixture
def env(monkeypatch):
    cm = FakeCM()
    system = FakeSystem()
    monkeypatch.setattr(fc._api_stream, "connection_manager", cm)
    monkeypatch.setattr(fc._api_stream, "_build_event", _build_event)
    monkeypatch.setattr(fc._api_stream, "get_thinking_system", lambda: system)
    monkeypatch.setattr(fc, "_main_event_loop", lambda: asyncio.get_running_loop())
    return cm, system


# ── confirm_frontend_connection ────────────────────────────────────────

def test_confirm_no_connections(env):
    assert fc.confirm_frontend_connection() is False


def test_confirm_broadcast(env):
    cm, _ = env
    cm.active_connections = {"s1": object()}
    assert fc.confirm_frontend_connection() is True


def test_confirm_specific_session(env):
    cm, _ = env
    cm.active_connections = {"s1": object(), "s2": object()}
    assert fc.confirm_frontend_connection("s2") is True


def test_confirm_exception(env, monkeypatch):
    cm, _ = env
    cm.active_connections = {"s1": object()}
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(fc, "_build_event", boom)
    assert fc.confirm_frontend_connection() is False


# ── _confirm_async ─────────────────────────────────────────────────────

async def test_confirm_async_no_connections(env):
    assert await fc._confirm_async() is False


async def test_confirm_async_success(env):
    cm, _ = env
    cm.active_connections = {"s1": object()}
    assert await fc._confirm_async() is True


async def test_confirm_async_skips_failures(env):
    cm, _ = env
    cm.active_connections = {"s1": object(), "s2": object()}
    async def send_json(sid, event):
        if sid == "s1":
            raise ConnectionError("down")
        return True
    cm.send_json = send_json
    assert await fc._confirm_async() is True


# ── _persist_message ───────────────────────────────────────────────────

async def test_persist_message_in_session(env):
    cm, system = env
    msg_id = await fc._persist_message("sess-1", "user", "内容")
    assert msg_id.startswith("mid-")
    assert system.sessions["sess-1"].messages == [("sess-1", "user", "内容")]


async def test_persist_message_chatonly(env, monkeypatch):
    cm, system = env
    repo = MagicMock()
    repo.save_message = MagicMock(return_value="db-mid")
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: repo)
    msg_id = await fc._persist_message("chatonly-1", "assistant", "落库")
    assert msg_id == "db-mid"
    repo.save_message.assert_called_once()


async def test_persist_message_exception(env, monkeypatch):
    cm, system = env
    async def boom(*a, **k):
        raise RuntimeError("append fail")
    system._append_message = boom
    msg_id = await fc._persist_message("sess-1", "user", "x")
    assert msg_id == ""


# ── push_content ───────────────────────────────────────────────────────

async def test_push_content_persist_and_send(env):
    cm, _ = env
    cm.active_connections = {"s1": object()}
    sent = await fc.push_content("sess-1", msg_type="message", event="assistant_msg", content="你好", persist=True)
    assert sent is True


async def test_push_content_no_connections(env):
    cm, _ = env
    sent = await fc.push_content("sess-1", msg_type="message", event="x", content="你好", persist=False)
    assert sent is False


async def test_push_content_persist_false(env):
    cm, _ = env
    cm.active_connections = {"s1": object()}
    sent = await fc.push_content("s1", msg_type="message", event="x", content="c", persist=False)
    assert sent is True


# ── generate_and_push ──────────────────────────────────────────────────

async def test_generate_and_push_success(env):
    cm, _ = env
    cm.active_connections = {"s1": object()}
    async def llm_fn():
        return " 生成的内容 "
    out = await fc.generate_and_push("sess-1", llm_fn, msg_type="message", event="proactive")
    assert out == "生成的内容"


async def test_generate_and_push_handshake_fail(env):
    cm, _ = env
    called = []
    async def llm_fn():
        called.append(1)
        return "x"
    out = await fc.generate_and_push("sess-1", llm_fn, msg_type="message", event="proactive")
    assert out is None
    assert called == []  # 握手失败跳过 LLM


async def test_generate_and_push_empty_text(env):
    cm, _ = env
    cm.active_connections = {"s1": object()}
    async def llm_fn():
        return "   "
    out = await fc.generate_and_push("sess-1", llm_fn, msg_type="message", event="proactive")
    assert out is None
