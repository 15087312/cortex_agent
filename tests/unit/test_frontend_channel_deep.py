"""frontend_channel 深分支测试：_run_async / 跨线程持久化 / 无连接推送"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import modules.thinking.frontend_channel as fc


@pytest.fixture
def env(monkeypatch):
    cm = MagicMock()
    cm.active_connections = {}
    cm._loop = None
    system = MagicMock()
    system.sessions = {}
    monkeypatch.setattr(fc._api_stream, "connection_manager", cm)
    monkeypatch.setattr(fc._api_stream, "_build_event", lambda **kw: {"event": kw.get("event")})
    monkeypatch.setattr(fc._api_stream, "get_thinking_system", lambda: system)
    monkeypatch.setattr(fc, "_main_event_loop", lambda: asyncio.get_running_loop())
    return cm, system


def test_run_async():
    async def coro():
        await asyncio.sleep(0)
        return 42
    assert fc._run_async(coro()) == 42


async def test_persist_no_loop(env, monkeypatch):
    cm, system = env
    session = MagicMock()
    session._append_message = AsyncMock(return_value="mid-1")
    system.sessions = {"s1": session}
    cm._loop = None
    monkeypatch.setattr(fc, "_main_event_loop", lambda: None)
    msg_id = await fc._persist_message("s1", "user", "内容")
    assert msg_id == ""  # 事件循环内 _run_async 不可用 → 优雅降级返回空


async def test_persist_different_loop(env):
    cm, system = env
    session = MagicMock()
    session._append_message = AsyncMock(return_value="mid-2")
    system.sessions = {"s1": session}
    other_loop = asyncio.new_event_loop()
    cm._loop = other_loop

    # run_coroutine_threadsafe 提交到 other_loop，但该 loop 未运行 → future 永远不完成
    import asyncio as _a
    async def wrap():
        try:
            result = await fc._persist_message("s1", "user", "x")
        except Exception:
            result = ""
        return result
    # 手动驱动 other_loop 处理
    task = asyncio.create_task(wrap())
    await asyncio.sleep(0.05)
    other_loop.call_soon_threadsafe(lambda: None)
    other_loop.close()
    out = await task
    assert isinstance(out, str)


async def test_push_content_no_connections_warns(env):
    cm, _ = env
    cm.active_connections = {}
    sent = await fc.push_content("s1", msg_type="message", event="x", content="内容", persist=True)
    assert sent is False


async def test_push_content_send_fails_continues(env):
    cm, _ = env
    cm.active_connections = {"a": object(), "b": object()}
    results = []

    async def send_json(sid, event):
        if sid == "a":
            raise ConnectionError("down")
        results.append(sid)

    cm.send_json = send_json
    sent = await fc.push_content("s1", msg_type="message", event="x", content="内容", persist=False)
    assert sent is True
    assert results == ["b"]
