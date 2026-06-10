from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable


EventCallback = Callable[["Event"], Any]


@dataclass(frozen=True)
class Event:
    name: str
    data: Any = None
    source: str = "system"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class EventBus:
    """In-process event bus for plugin engine.

    publish() 同时分发给本地监听者和思考模块的 MessageBus（如果可用），
    实现插件事件与模型通信的统一，无需额外桥接。
    """

    def __init__(self):
        self._listeners: dict[str, list[EventCallback]] = defaultdict(list)
        self._wildcard_listeners: list[EventCallback] = []
        self._lock = threading.Lock()

    def subscribe(self, event: str, callback: EventCallback) -> None:
        with self._lock:
            if event == "*":
                if callback not in self._wildcard_listeners:
                    self._wildcard_listeners.append(callback)
                return
            if callback not in self._listeners[event]:
                self._listeners[event].append(callback)

    def unsubscribe(self, event: str, callback: EventCallback) -> None:
        with self._lock:
            listeners = self._wildcard_listeners if event == "*" else self._listeners.get(event, [])
            if callback in listeners:
                listeners.remove(callback)

    def publish(self, event: str | Event, data: Any = None, source: str = "system") -> list[Any]:
        envelope = event if isinstance(event, Event) else Event(name=event, data=data, source=source)
        with self._lock:
            callbacks = [*self._listeners.get(envelope.name, []), *self._wildcard_listeners]
        results: list[Any] = []
        for callback in callbacks:
            try:
                results.append(callback(envelope))
            except Exception as exc:
                results.append({"error": str(exc), "event": envelope.name})

        # 同步到思考模块 MessageBus（如果可用）
        self._forward_to_message_bus(envelope)

        return results

    def _forward_to_message_bus(self, envelope: Event) -> None:
        """将事件转发到 ModelMessageBus，让思考模块能订阅插件事件"""
        try:
            from modules.thinking.communication.message_bus import (
                get_message_bus, Message, MessageType,
            )
            bus = get_message_bus()
            msg = Message(
                msg_type=MessageType.BROADCAST,
                sender=f"plugin:{envelope.source}",
                recipient="broadcast",
                content={
                    "action": "plugin_event",
                    "event": envelope.name,
                    "data": envelope.data,
                },
                metadata={"source": "plugin_event_bus"},
            )
            try:
                import asyncio
                loop = asyncio.get_running_loop()
                loop.create_task(bus.broadcast(msg))
            except RuntimeError:
                pass
        except ImportError:
            pass  # 思考模块不可用时静默跳过

    def listener_count(self, event: str | None = None) -> int:
        with self._lock:
            if event is None:
                return sum(len(items) for items in self._listeners.values()) + len(self._wildcard_listeners)
            if event == "*":
                return len(self._wildcard_listeners)
            return len(self._listeners.get(event, []))


global_event_bus = EventBus()
