"""
Tests for ModelMessageBus — async message passing backbone.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from modules.thinking.communication.message_bus import (
    ModelMessageBus,
    Message,
    MessageType,
    get_message_bus,
)


@pytest.fixture
def bus():
    """Fresh MessageBus instance per test."""
    b = ModelMessageBus()
    b._queues.clear()
    b._subscriptions.clear()
    b._pending_responses.clear()
    b._stats = {"sent": 0, "received": 0, "expired": 0, "broadcasts": 0}
    return b


# --- send / receive ---

@pytest.mark.asyncio
async def test_send_and_receive(bus):
    msg = Message(
        msg_type=MessageType.QUERY,
        sender="a",
        recipient="b",
        content={"action": "test"},
    )
    await bus.send(msg)
    msgs = await bus.receive("b", limit=10)
    assert len(msgs) == 1
    assert msgs[0].content == {"action": "test"}


@pytest.mark.asyncio
async def test_receive_empty(bus):
    msgs = await bus.receive("nonexistent")
    assert msgs == []


@pytest.mark.asyncio
async def test_receive_respects_limit(bus):
    for i in range(5):
        await bus.send(Message(
            msg_type=MessageType.SYSTEM,
            sender="s", recipient="r",
            content={"i": i},
        ))
    msgs = await bus.receive("r", limit=3)
    assert len(msgs) == 3


# --- stats ---

@pytest.mark.asyncio
async def test_get_stats(bus):
    await bus.send(Message(msg_type=MessageType.SYSTEM, sender="s", recipient="r", content="x"))
    stats = await bus.get_stats()
    assert "sent" in stats
    assert stats["sent"] >= 1


@pytest.mark.asyncio
async def test_list_recipients(bus):
    await bus.send(Message(msg_type=MessageType.SYSTEM, sender="s", recipient="alpha", content="x"))
    await bus.send(Message(msg_type=MessageType.SYSTEM, sender="s", recipient="beta", content="x"))
    recipients = await bus.list_recipients()
    assert "alpha" in recipients
    assert "beta" in recipients


# --- cleanup ---

@pytest.mark.asyncio
async def test_cleanup_removes_done_futures(bus):
    future = asyncio.get_event_loop().create_future()
    future.set_result("done")
    bus._pending_responses["done-cid"] = future
    removed = await bus.cleanup()
    assert removed >= 1
