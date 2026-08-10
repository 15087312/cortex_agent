"""api_stream 核心工具补测：ConnectionManager / 事件构建 / 名称解析"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

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
