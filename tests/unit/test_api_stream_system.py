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


async def test_emit_real_impl_callback_and_ws(monkeypatch):
    """_emit 真实实现：调 callback + 走 connection_manager.send_json"""
    s = _system()
    s.sessions["s1"] = {"messages": [], "running": True}
    import modules.thinking.api_stream as stream_mod
    cm = MagicMock()
    cm.send_json = AsyncMock()
    monkeypatch.setattr(stream_mod, "connection_manager", cm)

    called = []
    async def cb(ev):
        called.append(ev)
    await s._emit("s1", {"event": "test"}, callback=cb)
    assert called == [{"event": "test"}]
    cm.send_json.assert_awaited_once_with("s1", {"event": "test"})


async def test_emit_real_impl_no_callback(monkeypatch):
    """无 callback 时只走 WS"""
    s = _system()
    s.sessions["s1"] = {"messages": [], "running": True}
    import modules.thinking.api_stream as stream_mod
    cm = MagicMock()
    cm.send_json = AsyncMock()
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    await s._emit("s1", {"event": "x"})
    cm.send_json.assert_awaited_once_with("s1", {"event": "x"})


def test_get_session_repo_real_impl(monkeypatch):
    """_get_session_repo 真实实现：懒加载 + 缓存"""
    s = _system()
    s._session_repo = None
    import modules.database.session_repo as sr
    repo = MagicMock()
    monkeypatch.setattr(sr, "get_session_repo", lambda: repo)
    assert s._get_session_repo() is repo
    assert s._session_repo is repo  # 已缓存
    # 二次调用不再触发 get_session_repo
    assert s._get_session_repo() is repo


def test_get_session_repo_real_impl_failure(monkeypatch):
    s = _system()
    s._session_repo = None
    import modules.database.session_repo as sr
    monkeypatch.setattr(sr, "get_session_repo", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    assert s._get_session_repo() is None


async def test_proactive_context_trim_real_impl_no_session(monkeypatch):
    """无会话 → 直接返回"""
    s = _system()
    s.sessions = {}
    s._lock = asyncio.Lock()
    await s._proactive_context_trim("nope")


async def test_proactive_context_trim_real_impl_under_threshold(monkeypatch):
    """消息量未超 80% 水位 → 不裁剪"""
    s = _system()
    s.sessions = {"s1": {"messages": [{"role": "user", "content": "短"}] * 3, "running": True}}
    s._lock = asyncio.Lock()
    import modules.thinking.context.compression as comp_mod
    engine = MagicMock()
    engine.estimate_tokens.return_value = 1000  # 远低于 128000*0.8
    monkeypatch.setattr(comp_mod, "get_compression_engine", lambda: engine)
    await s._proactive_context_trim("s1")
    assert len(s.sessions["s1"]["messages"]) == 3  # 未裁剪


async def test_proactive_context_trim_real_impl_over_threshold(monkeypatch):
    """消息超水位 → 丢弃最旧 50%，保留最新"""
    s = _system()
    s.sessions = {"s1": {"messages": [{"role": "user", "content": f"内容{i}"} for i in range(8)], "running": True}}
    s._lock = asyncio.Lock()
    import modules.thinking.context.compression as comp_mod
    import importlib
    cfg_mod = importlib.import_module("config.settings")
    engine = MagicMock()
    engine.estimate_tokens.return_value = 999999  # 超阈值
    monkeypatch.setattr(comp_mod, "get_compression_engine", lambda: engine)
    monkeypatch.setattr(cfg_mod, "settings", type("S", (), {"CONTEXT_WINDOW_SIZE": 1000})())
    await s._proactive_context_trim("s1")
    assert len(s.sessions["s1"]["messages"]) == 4  # 保留最新 4 条（8//2）
