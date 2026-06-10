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
    """Small in-process event bus used by the plugin engine and trusted plugins."""

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
        return results

    def listener_count(self, event: str | None = None) -> int:
        with self._lock:
            if event is None:
                return sum(len(items) for items in self._listeners.values()) + len(self._wildcard_listeners)
            if event == "*":
                return len(self._wildcard_listeners)
            return len(self._listeners.get(event, []))


global_event_bus = EventBus()
