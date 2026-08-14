"""ModelMessageBus 扩展测试：事件发射 / 广播 / RPC / 订阅 / 清理"""
import asyncio
import time

import pytest

from modules.thinking.communication.message_bus import (
    ModelMessageBus,
    Message,
    MessageType,
    get_message_bus,
)


@pytest.fixture
def bus():
    b = ModelMessageBus()
    b._queues.clear()
    b._subscriptions.clear()
    b._pending_responses.clear()
    b._stats = {"sent": 0, "received": 0, "expired": 0, "broadcasts": 0}
    b._event_emitters.clear()
    return b


def _msg(**kw):
    base = dict(msg_type=MessageType.QUERY, sender="a", recipient="b", content={"x": 1})
    base.update(kw)
    return Message(**base)


# ── Message 基础 ───────────────────────────────────────────────────────

def test_message_auto_id():
    m = Message(sender="a", recipient="b")
    assert m.msg_id.startswith("msg_")
    m2 = Message(msg_id="custom", sender="a", recipient="b")
    assert m2.msg_id == "custom"


def test_message_expired_and_to_dict():
    m = Message(sender="a", recipient="b", content="hi", ttl=0.01)
    time.sleep(0.02)
    assert m.expired is True
    d = m.to_dict()
    assert d["msg_type"] == "system"
    assert d["content"] == "hi"
    # content 为空时 to_dict 不抛异常
    m2 = Message(sender="a", recipient="b", content=None)
    assert m2.to_dict()["content"] == ""


# ── lock 属性（无事件循环场景） ────────────────────────────────────────

def test_lock_no_running_loop(monkeypatch):
    b = ModelMessageBus()
    # 无运行中的事件循环 → 返回已有锁
    lock = b.lock
    assert lock is b._ModelMessageBus__lock


# ── 事件发射器 ─────────────────────────────────────────────────────────

async def test_set_and_emit_event_session_specific(bus):
    received = []
    bus.set_event_emitter(received.append, session_id="s1")
    m = _msg(metadata={"session_id": "s1"})
    bus._emit_event("broadcast", m, {"extra": 1})
    assert len(received) == 1
    ev = received[0]
    assert ev["action"] == "broadcast"
    assert ev["payload"]["extra"] == 1


async def test_set_and_emit_global_fallback(bus):
    received = []
    bus.set_event_emitter(received.append)  # 全局 ""
    m = _msg(metadata={"session_id": "s2"})
    bus._emit_event("broadcast", m)
    assert received  # 会话无专属 emitter → 用全局


async def test_emit_ignores_non_broadcast(bus):
    received = []
    bus.set_event_emitter(received.append)
    bus._emit_event("message_sent", _msg())
    assert received == []


async def test_emit_no_emitter(bus):
    bus._emit_event("broadcast", _msg())  # 无 emitter 不抛异常


async def test_emit_exception_in_emitter(bus):
    def boom(event):
        raise RuntimeError("boom")
    bus.set_event_emitter(boom)
    bus._emit_event("broadcast", _msg())  # 不抛异常


async def test_set_event_emitter_remove(bus):
    bus.set_event_emitter(None, session_id="s1")  # 删除不存在的 → 不抛
    bus.set_event_emitter(lambda e: None)
    bus.set_event_emitter(None)  # 清空
    assert bus._event_emitters == {}


# ── broadcast ──────────────────────────────────────────────────────────

async def test_broadcast_excludes_sender(bus):
    await bus.send(_msg(recipient="b"))
    await bus.send(_msg(recipient="c"))
    msg = Message(sender="a", recipient="broadcast", content="hello")
    msg.msg_type = MessageType.BROADCAST
    await bus.broadcast(msg)
    msgs_b = await bus.receive("b", limit=10)
    msgs_c = await bus.receive("c", limit=10)
    assert any(m.content == "hello" for m in msgs_b)
    assert any(m.content == "hello" for m in msgs_c)
    assert bus._stats["broadcasts"] == 1
    assert len(bus._broadcast_history) == 1


async def test_broadcast_no_recipients(bus):
    msg = Message(sender="a", recipient="broadcast", content="x")
    await bus.broadcast(msg)
    assert bus._stats["broadcasts"] == 1


# ── request / send_response ────────────────────────────────────────────

async def test_request_response_roundtrip(bus):
    async def responder():
        await asyncio.sleep(0.01)
        req = await bus.receive_one("b")
        await bus.send_response(req, {"result": 42})

    task = asyncio.create_task(responder())
    resp = await bus.request(_msg(recipient="b"), timeout=5)
    await task
    assert resp is not None
    assert resp.content == {"result": 42}
    assert resp.correlation_id.startswith("corr_")


async def test_request_timeout(bus):
    resp = await bus.request(_msg(recipient="nobody"), timeout=0.05)
    assert resp is None
    assert bus._pending_responses == {}


async def test_request_with_existing_correlation_id(bus):
    msg = _msg(correlation_id="my_corr", recipient="b")
    async def responder():
        req = await bus.receive_one("b")
        await bus.send_response(req, "ok")
    task = asyncio.create_task(responder())
    resp = await bus.request(msg, timeout=5)
    await task
    assert resp.content == "ok"
    assert resp.correlation_id == "my_corr"


async def test_send_response_no_waiting_future(bus):
    msg = _msg(recipient="b", correlation_id="corr1")
    m = Message(
        msg_type=MessageType.QUERY, sender="b", recipient="a",
        correlation_id="corr1", content="回",
    )
    resp_id = await bus.send_response(m, "result")
    assert resp_id  # 作为普通消息发送


# ── receive / peek / expire ────────────────────────────────────────────

async def test_receive_skips_expired(bus):
    m = _msg(recipient="b", ttl=0.01)
    await bus.send(m)
    time.sleep(0.02)
    msgs = await bus.receive("b", limit=10)
    assert msgs == []
    assert bus._stats["expired"] == 1


async def test_receive_one_and_peek(bus):
    m = _msg(recipient="b")
    await bus.send(m)
    assert await bus.receive_one("b") is not None
    await bus.send(_msg(recipient="c"))
    peeked = await bus.peek("c", limit=50)
    assert len(peeked) == 1
    # peek 不消费
    assert len(await bus.receive("c")) == 1


async def test_peek_all(bus):
    await bus.send(_msg(recipient="b"))
    all_ = await bus.peek_all()
    assert "b" in all_


async def test_list_recipients_and_queue_size(bus):
    await bus.send(_msg(recipient="b"))
    assert await bus.list_recipients() == ["b"]
    assert await bus.get_queue_size("b") == 1
    assert await bus.get_queue_size() == 1
    assert await bus.get_queue_size("不存在") == 0


# ── 订阅 ───────────────────────────────────────────────────────────────

async def test_subscribe_notify_and_unsubscribe(bus):
    calls = []
    async def cb(_):
        calls.append(1)
    await bus.subscribe("b", cb)
    await bus.send(_msg(recipient="b"))
    assert calls == [1]
    await bus.unsubscribe("b", cb)
    await bus.send(_msg(recipient="b"))
    assert calls == [1]


async def test_subscribe_sync_callback_and_error(bus):
    calls = []
    def sync_cb(_):
        calls.append(1)
    def boom_cb(_):
        raise RuntimeError("cb boom")
    await bus.subscribe("b", sync_cb)
    await bus.subscribe("b", boom_cb)
    await bus.send(_msg(recipient="b"))
    assert calls == [1]


async def test_unsubscribe_all(bus):
    await bus.subscribe("b", lambda _: None)
    await bus.unsubscribe("b")
    assert bus._subscriptions == {}


# ── cleanup / stats / status ───────────────────────────────────────────

async def test_cleanup_expired_and_futures(bus):
    m = _msg(recipient="b", ttl=0.01)
    await bus.send(m)
    time.sleep(0.02)
    removed = await bus.cleanup()
    assert removed >= 1


async def test_get_stats_and_status(bus):
    await bus.send(_msg(recipient="b"))
    stats = await bus.get_stats()
    assert stats["sent"] == 1
    assert stats["total_queued"] == 1
    status = await bus.get_status()
    assert status["sent"] == 1


async def test_get_message_bus_singleton(monkeypatch):
    import modules.thinking.communication.message_bus as mod
    monkeypatch.setattr(mod, "_message_bus", None)
    a = get_message_bus()
    b = get_message_bus()
    assert a is b
    assert mod._message_bus is a
