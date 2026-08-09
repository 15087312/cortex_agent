"""frontend_channel 统一推送出口测试：握手 / 连续持久化 / 统一流程"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import modules.thinking.frontend_channel as fc


def _patch_api(monkeypatch, active=None, loop=None):
    """替换 api_stream 模块属性，让 frontend_channel 通过模块引用取到测试值"""
    import modules.thinking.api_stream as stream_mod
    cm = MagicMock()
    cm.active_connections = active or {}
    cm._loop = loop
    cm.send_json_from_thread.return_value = True
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    monkeypatch.setattr(stream_mod, "_build_event", lambda **kw: {
        "type": kw.get("msg_type"), "event": kw.get("event"), "content": kw.get("content"),
        "session_id": kw.get("session_id"), "data": kw.get("data") or {},
    })
    return cm


# ── confirm_frontend_connection ──

def test_confirm_no_connections(monkeypatch):
    cm = _patch_api(monkeypatch, active={})
    assert fc.confirm_frontend_connection() is False
    cm.send_json_from_thread.assert_not_called()


def test_confirm_broadcast_success(monkeypatch):
    cm = _patch_api(monkeypatch, active={"s1": object(), "s2": object()})
    assert fc.confirm_frontend_connection() is True
    assert cm.send_json_from_thread.call_count == 1  # 任一送达即 True


def test_confirm_broadcast_all_fail(monkeypatch):
    cm = _patch_api(monkeypatch, active={"s1": object(), "s2": object()})
    cm.send_json_from_thread.return_value = False
    assert fc.confirm_frontend_connection() is False
    assert cm.send_json_from_thread.call_count == 2


def test_confirm_by_session_hit(monkeypatch):
    cm = _patch_api(monkeypatch, active={"s1": object(), "s2": object()})
    cm.send_json_from_thread.side_effect = lambda sid, ev: sid in cm.active_connections
    assert fc.confirm_frontend_connection("s2") is True
    assert cm.send_json_from_thread.call_count == 1
    assert cm.send_json_from_thread.call_args[0][0] == "s2"


def test_confirm_by_session_miss(monkeypatch):
    cm = _patch_api(monkeypatch, active={"s1": object()})
    cm.send_json_from_thread.side_effect = lambda sid, ev: sid in cm.active_connections
    assert fc.confirm_frontend_connection("nope") is False
    assert fc.confirm_frontend_connection("s1") is True


# ── push_content（连续持久化 + 推送）──

async def test_push_content_agent_session_persist(monkeypatch):
    """agent 会话：提交主 loop 持久化 + WS 推送"""
    _patch_api(monkeypatch, active={"s1": object()})
    import modules.thinking.api_stream as stream_mod
    system = MagicMock()
    system.sessions = {"s1": {}}
    system._append_message = AsyncMock(return_value="mid1")
    monkeypatch.setattr(stream_mod, "get_thinking_system", lambda: system)
    monkeypatch.setattr(stream_mod, "_main_event_loop", None)
    monkeypatch.setattr(fc, "_run_async", lambda coro: "mid1")

    sent = await fc.push_content("s1", msg_type="proactive", event="test", content="内容")
    assert sent is True
    system._append_message.assert_called_once_with("s1", "assistant", "内容")


async def test_push_content_chatonly_db_persist(monkeypatch):
    """chatonly 会话：直接落 DB + 推送"""
    cm = _patch_api(monkeypatch, active={"s1": object()})
    import modules.thinking.api_stream as stream_mod
    system = MagicMock()
    system.sessions = {}
    monkeypatch.setattr(stream_mod, "get_thinking_system", lambda: system)
    import modules.database.session_repo as sr
    repo = MagicMock()
    repo.save_message.return_value = "mid1"
    monkeypatch.setattr(sr, "get_session_repo", lambda: repo)

    sent = await fc.push_content("s1", msg_type="proactive", event="test", content="内容")
    assert sent is True
    repo.save_message.assert_called_once_with("s1", "assistant", "内容")
    assert cm.send_json_from_thread.called


async def test_push_content_no_connections_persists(monkeypatch):
    """无活跃连接：消息仍持久化（防中途退出丢失），返回 False"""
    _patch_api(monkeypatch, active={})
    import modules.thinking.api_stream as stream_mod
    system = MagicMock()
    system.sessions = {}
    monkeypatch.setattr(stream_mod, "get_thinking_system", lambda: system)
    import modules.database.session_repo as sr
    repo = MagicMock()
    repo.save_message.return_value = "mid1"
    monkeypatch.setattr(sr, "get_session_repo", lambda: repo)

    sent = await fc.push_content("s1", msg_type="proactive", event="test", content="内容")
    assert sent is False
    repo.save_message.assert_called_once()  # 仍持久化


async def test_push_content_persist_false(monkeypatch):
    """persist=False：只推送不落库"""
    cm = _patch_api(monkeypatch, active={"s1": object()})
    import modules.thinking.api_stream as stream_mod
    system = MagicMock()
    system.sessions = {}
    monkeypatch.setattr(stream_mod, "get_thinking_system", lambda: system)
    import modules.database.session_repo as sr
    repo = MagicMock()
    monkeypatch.setattr(sr, "get_session_repo", lambda: repo)

    await fc.push_content("s1", msg_type="pet", event="pet_reply", content="回复", persist=False)
    repo.save_message.assert_not_called()
    assert cm.send_json_from_thread.called


# ── generate_and_push（统一流程）──

async def test_generate_and_push_success(monkeypatch):
    _patch_api(monkeypatch, active={"s1": object()})
    pushed = []
    async def fake_push(sid, *, msg_type, event, content, role="assistant", data=None, persist=True):
        pushed.append(content)
        return True
    monkeypatch.setattr(fc, "push_content", fake_push)

    async def llm():
        return "生成内容"
    out = await fc.generate_and_push("s1", llm, msg_type="proactive", event="test")
    assert out == "生成内容"
    assert pushed == ["生成内容"]


async def test_generate_and_push_handshake_fail_skips_llm(monkeypatch):
    """握手失败 → 跳过 LLM（不调 llm_fn、不推送）"""
    _patch_api(monkeypatch, active={})
    monkeypatch.setattr(fc, "confirm_frontend_connection", lambda session_id=None: False)
    pushed = []
    async def fake_push(sid, *, msg_type, event, content, role="assistant", data=None, persist=True):
        pushed.append(content)
    monkeypatch.setattr(fc, "push_content", fake_push)

    called = {"llm": 0}
    async def llm():
        called["llm"] += 1
        return "内容"
    out = await fc.generate_and_push("s1", llm, msg_type="proactive", event="test")
    assert out is None
    assert called["llm"] == 0
    assert pushed == []


async def test_generate_and_push_empty_llm(monkeypatch):
    """LLM 返回空 → 不推送"""
    _patch_api(monkeypatch, active={"s1": object()})
    pushed = []
    async def fake_push(sid, *, msg_type, event, content, role="assistant", data=None, persist=True):
        pushed.append(content)
    monkeypatch.setattr(fc, "push_content", fake_push)

    async def llm():
        return ""
    out = await fc.generate_and_push("s1", llm, msg_type="proactive", event="test")
    assert out is None
    assert pushed == []


async def test_generate_and_push_broadcast_handshake(monkeypatch):
    """主动搭话/定时任务：握手广播（默认 None）——目标 session 无连接但其他连接在线仍触发。

    回归防护：此前 generate_and_push 用 session_id 握手，目标 session 非前端当前连接时
    握手失败导致主动搭话从不触发、无记录。
    """
    _patch_api(monkeypatch, active={"frontend_current": object()})
    confirm = MagicMock(return_value=True)
    monkeypatch.setattr(fc, "confirm_frontend_connection", confirm)
    pushed = []
    async def fake_push(sid, *, msg_type, event, content, role="assistant", data=None, persist=True):
        pushed.append(content)
        return True
    monkeypatch.setattr(fc, "push_content", fake_push)

    called = {"llm": 0}
    async def llm():
        called["llm"] += 1
        return "主动消息"
    # session_id="target_session"（目标会话，可能无连接），握手默认广播 → 仍触发
    out = await fc.generate_and_push(
        "target_session", llm, msg_type="proactive", event="proactive_outreach"
    )
    assert out == "主动消息"
    assert called["llm"] == 1
    assert pushed == ["主动消息"]
    # 握手确认用的是广播（None），而非 target_session
    confirm.assert_called_once_with(None)
