"""api_stream 核心工具补测：ConnectionManager / 事件构建 / 名称解析"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import modules.thinking.api_stream as stream_mod
from modules.thinking.api_stream import (
    ConnectionManager,
    _build_event,
    _resolve_identity_name,
    connection_manager,
)


def test_build_event():
    ev = _build_event(session_id="s1", msg_type="message", event="assistant", content="hi", role="assistant", data={"x": 1})
    assert ev["session_id"] == "s1"
    assert ev["event"] == "assistant"
    assert ev["data"] == {"x": 1}
    assert "timestamp" in ev


def test_resolve_identity_name_no_model():
    assert _resolve_identity_name("") == ""


async def test_connection_manager_connect_send_disconnect():
    cm = ConnectionManager()
    ws = AsyncMock()
    await cm.connect("s1", ws)
    ws.accept.assert_awaited_once()
    assert "s1" in cm.active_connections

    await cm.send_json("s1", {"msg": "hi"})
    ws.send_json.assert_awaited_once_with({"msg": "hi"})

    # 无连接时静默
    await cm.send_json("nope", {"msg": "x"})

    await cm.disconnect("s1")
    assert "s1" not in cm.active_connections


async def test_connection_manager_send_closed():
    cm = ConnectionManager()
    ws = AsyncMock()
    ws.send_json = AsyncMock(side_effect=RuntimeError("closed"))
    await cm.connect("s1", ws)
    await cm.send_json("s1", {"msg": "x"})
    assert "s1" not in cm.active_connections  # 已移除


async def test_send_json_from_thread_no_loop():
    cm = ConnectionManager()
    cm._loop = None
    assert cm.send_json_from_thread("s1", {}) is False


async def test_send_json_from_thread_no_connection():
    cm = ConnectionManager()
    cm._loop = asyncio.get_running_loop()
    assert cm.send_json_from_thread("nope", {}) is False


def test_send_json_from_thread_success():
    import threading
    box = {}

    def worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        cm = ConnectionManager()
        ws = AsyncMock()
        loop.run_until_complete(cm.connect("s1", ws))
        cm._loop = loop
        box["cm"] = cm
        box["ws"] = ws
        loop.run_forever()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    while "cm" not in box:
        import time
        time.sleep(0.01)
    assert box["cm"].send_json_from_thread("s1", {"msg": "x"}) is True
    box["cm"]._loop.call_soon_threadsafe(box["cm"]._loop.stop)


async def test_send_json_from_thread_on_loop_thread_not_blocked():
    """回归：事件循环线程内调用 send_json_from_thread 不得阻塞/超时。

    曾在对话中触发：模型推理推送 thinking 事件（_push_reasoning）在事件循环
    线程内调用本方法，run_coroutine_threadsafe + future.result() 自锁，
    阻塞循环 5s 后抛 TimeoutError（空错误消息）→ 对话报错。
    """
    cm = ConnectionManager()
    ws = AsyncMock()
    await cm.connect("s1", ws)
    ok = cm.send_json_from_thread("s1", {"msg": "x"})
    assert ok is True
    await asyncio.sleep(0.05)
    ws.send_json.assert_awaited_once_with({"msg": "x"})
    await cm.disconnect("s1")


async def test_broadcast():
    cm = ConnectionManager()
    ws1 = AsyncMock()
    ws2 = AsyncMock()
    await cm.connect("s1", ws1)
    await cm.connect("s2", ws2)
    await cm.broadcast({"msg": "all"})
    ws1.send_json.assert_awaited_once()
    ws2.send_json.assert_awaited_once()


def test_resolve_identity_name_cached(monkeypatch):
    stream_mod._identity_name_cache.clear()
    stream_mod._identity_name_cache["supervisor_code_001"] = "代码主管"
    assert _resolve_identity_name("supervisor_code_001") == "代码主管"


def test_resolve_identity_name_from_identities(monkeypatch):
    stream_mod._identity_name_cache.clear()
    import modules.thinking.identity as ident_mod
    monkeypatch.setattr(ident_mod, "get_identities", lambda: {
        "code_supervisor": {"name": "代码主管", "model_id": "supervisor_code"},
    })
    assert _resolve_identity_name("supervisor_code_001") == "代码主管"


def test_resolve_identity_name_not_found(monkeypatch):
    stream_mod._identity_name_cache.clear()
    import modules.thinking.identity as ident_mod
    monkeypatch.setattr(ident_mod, "get_identities", lambda: {})
    assert _resolve_identity_name("unknown_003") == ""


def test_global_connection_manager():
    assert isinstance(connection_manager, ConnectionManager)


# ── _ws_auth_ok / _resolve_identity_name / _post_task_extraction ────────────

def test_ws_auth_ok_no_key():
    from modules.thinking import api_stream as ap
    import types
    class WS:
        headers = {}
        query_params = {}
    ws = WS()
    # 开发模式（无 SIMPLE_API_KEY）→ 放行
    from config.settings import settings as _cfg
    with patch.object(_cfg, "SIMPLE_API_KEY", ""):
        assert ap._ws_auth_ok(ws) is True


def test_ws_auth_ok_header(monkeypatch):
    from modules.thinking import api_stream as ap
    import types
    from config.settings import settings as _cfg
    monkeypatch.setattr(_cfg, "SIMPLE_API_KEY", "secret")
    class WS:
        headers = {"x-api-key": "secret"}
        query_params = {}
    assert ap._ws_auth_ok(WS()) is True
    class WS2:
        headers = {"x-api-key": "wrong"}
        query_params = {}
    assert ap._ws_auth_ok(WS2()) is False


def test_ws_auth_ok_query(monkeypatch):
    from modules.thinking import api_stream as ap
    from config.settings import settings as _cfg
    monkeypatch.setattr(_cfg, "SIMPLE_API_KEY", "secret")
    class WS:
        headers = {}
        query_params = {"api_key": "secret"}
    assert ap._ws_auth_ok(WS()) is True


def test_resolve_identity_name(monkeypatch):
    from modules.thinking import api_stream as ap
    ap._identity_name_cache = {}
    import modules.thinking.identity as ident
    monkeypatch.setattr(ident, "get_identities", lambda: {
        "supervisor_code": {"name": "代码主管"}})
    assert ap._resolve_identity_name("supervisor_code_001") == "代码主管"
    assert ap._resolve_identity_name("") == ""


@pytest.mark.asyncio
async def test_post_task_extraction(monkeypatch):
    from modules.thinking import api_stream as ap
    import modules.memory.event_reducer as er_mod
    import modules.memory.event_store as es_mod
    import modules.memory.embedding as emb_mod

    reducer = AsyncMock()
    reducer.reduce.return_value = {"events": []}
    monkeypatch.setattr(er_mod, "EventReducer", lambda **kw: reducer)
    monkeypatch.setattr(es_mod.EventStore, "get_instance", classmethod(lambda cls: MagicMock()))
    monkeypatch.setattr(emb_mod.EmbeddingEngine, "get_instance", classmethod(lambda cls: MagicMock()))
    monkeypatch.setattr(ap, "asyncio", __import__("asyncio"))
    # 避免 sleep(30)：传 owner_id 后 sleep(0)
    sys = ap.StreamThinkingSystem.__new__(ap.StreamThinkingSystem)
    sys.sessions = {"s1": {"messages": [
        {"role": "user", "content": "你好，请帮我分析一下项目的整体架构设计思路"},
        {"role": "assistant::large_primary", "content": "好的，我来分析一下项目的架构，这是一个相对复杂的系统设计。"},
    ], "_processed_hashes": set()}}
    sys.logger = __import__("utils.logger", fromlist=["setup_logger"]).setup_logger("test")
    await sys._post_task_extraction("s1", "user", "resp", owner_id="large::large_primary")
    reducer.reduce.assert_called()


@pytest.mark.asyncio
async def test_post_task_extraction_short_text(monkeypatch):
    """对话过短（<50 字符）→ 不提取"""
    from modules.thinking import api_stream as ap
    import modules.memory.event_reducer as er_mod
    reducer = AsyncMock()
    monkeypatch.setattr(er_mod, "EventReducer", lambda **kw: reducer)
    sys = ap.StreamThinkingSystem.__new__(ap.StreamThinkingSystem)
    sys.sessions = {"s1": {"messages": [{"role": "user", "content": "短"}]}}
    sys.logger = MagicMock()
    await sys._post_task_extraction("s1", "short", "resp", owner_id="x")
    reducer.reduce.assert_not_called()
