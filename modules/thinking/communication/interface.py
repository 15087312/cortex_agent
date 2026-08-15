"""Communication module interface facade."""
from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable

from modules.thinking.communication.message_bus import Message, MessageType


@runtime_checkable
class MessageBusPort(Protocol):
    async def send(self, message: Message) -> str: ...
    async def receive(self, recipient: str) -> Any: ...
    async def subscribe(self, recipient: str, callback: Callable) -> None: ...
    async def unsubscribe(self, recipient: str, callback: Callable) -> None: ...


def get_message_bus_port() -> MessageBusPort:
    """Return the default message bus through the module facade."""
    from modules.thinking.communication.message_bus import get_message_bus

    return get_message_bus()


__all__ = ["Message", "MessageType", "MessageBusPort", "get_message_bus_port"]
