"""api_stream StreamThinkingSystem 方法补测"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import modules.thinking.api_stream as stream_mod
from modules.thinking.api_stream import StreamThinkingSystem


def _system():
    s = StreamThinkingSystem.__new__(StreamThinkingSystem)
    s.sessions = {}
    s._running = False
    s._lock = asyncio.Lock()
    s._orchestrator = MagicMock()
    s._session_repo = None
    return s


async def test_append_message_dedup(monkeypatch):
    s = _system()
    s.sessions["s1"] = {"messages": [{"role": "user", "content": "hi", "id": "m1"}], "running": True}
    repo = MagicMock()
    s._get_session_repo = lambda: repo
    mid = await s._append_message("s1", "user", "hi")
    assert mid == "m1"  # 去重返回已有 id
    await s._append_message("s1", "assistant", "回答")
    assert len(s.sessions["s1"]["messages"]) == 2


async def test_append_message_new_session_noop():
    s = _system()
    assert await s._append_message("nope", "user", "x") == ""


async def test_persist_thought(monkeypatch):
    s = _system()
    repo = MagicMock()
    s._get_session_repo = lambda: repo
    await s._persist_thought("s1", "思考", tier="large")
    repo.save_message.assert_called_once_with("s1", "thought", "思考", tier="large")


async def test_persist_thought_no_repo():
    s = _system()
    s._get_session_repo = lambda: None
    await s._persist_thought("s1", "思考")


async def test_processing_flag():
    s = _system()
    s.sessions["s1"] = {"messages": [], "processing": False, "running": True}
    assert await s._is_processing("s1") is False
    await s._set_processing("s1", True)
    assert await s._is_processing("s1") is True
    assert await s._is_processing("nope") is False


def test_format_scheduler_event_tool_call():
    s = _system()
    ev = {"type": "tool_call", "action": "run", "target": "read_file", "success": True}
    out = s._format_scheduler_event(ev)
    assert "工具 read_file run 成功" in out["content"]


def test_format_scheduler_event_model_comm_broadcast(monkeypatch):
    s = _system()
    stream_mod._identity_name_cache.clear()
    import modules.thinking.identity as ident_mod
    monkeypatch.setattr(ident_mod, "get_identities", lambda: {})
    ev = {
        "type": "model_comm",
        "action": "broadcast",
        "source": "expert_x",
        "target": "x",
        "payload": {
            "msg_type": "broadcast",
            "tier": "expert",
            "metadata": {"dialog_id": "d1"},
            "content": {"content": "专家发现", "entry_type": "response", "round": 1},
        },
    }
    out = s._format_scheduler_event(ev)
    assert out is not None
    assert "专家发现" in out["content"]


def test_format_scheduler_event_model_comm_skip_empty(monkeypatch):
    s = _system()
    stream_mod._identity_name_cache.clear()
    import modules.thinking.identity as ident_mod
    monkeypatch.setattr(ident_mod, "get_identities", lambda: {})
    ev = {
        "type": "model_comm",
        "action": "broadcast",
        "payload": {
            "msg_type": "broadcast",
            "metadata": {"dialog_id": "d1"},
            "content": {"content": "   ", "entry_type": "thought", "round": 1},
        },
    }
    assert s._format_scheduler_event(ev) is None


def test_format_scheduler_event_security(monkeypatch):
    s = _system()
    ev = {"type": "security", "action": "scan", "target": "input", "payload": {"detail": "风险", "duration_ms": 12}}
    out = s._format_scheduler_event(ev)
    assert "安全审查" in out["content"]


def test_format_scheduler_event_unknown():
    s = _system()
    ev = {"source": "abc", "action": "do", "target": "x"}
    out = s._format_scheduler_event(ev)
    assert "abc" in out["content"]


def test_stop_session(monkeypatch):
    s = _system()
    task = MagicMock()
    task.done.return_value = False
    s.sessions["s1"] = {"running": True, "scheduler_task": task, "messages": []}
    async def go():
        await s.stop("s1")
    asyncio.run(go())
    assert s.sessions["s1"]["running"] is False
    task.cancel.assert_called_once()
