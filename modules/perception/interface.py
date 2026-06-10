"""Perception module interface facade."""
from __future__ import annotations

from typing import Any, List, Protocol, runtime_checkable


@runtime_checkable
class PerceptionPort(Protocol):
    """Protocol for attention-relevant perception state."""

    @property
    def is_running(self) -> bool:
        """Whether perception collection is active."""

    def get_attention_items(self, max_age_seconds: float = 10.0) -> List[Any]:
        """Return recent attention-worthy perception items."""


class PerceptionManagerAdapter:
    """Adapter around the concrete perception manager singleton."""

    def __init__(self, manager: Any):
        self._manager = manager

    @property
    def is_running(self) -> bool:
        return bool(getattr(self._manager, "_running", False))

    def get_attention_items(self, max_age_seconds: float = 10.0) -> List[Any]:
        return self._manager.get_attention_items(max_age_seconds=max_age_seconds)


def create_perception_port() -> PerceptionPort:
    """Create the default perception port with delayed concrete import."""
    from modules.perception import perception_manager

    return PerceptionManagerAdapter(perception_manager)


def get_perception_port() -> PerceptionPort:
    """Compatibility alias for callers that expect get_* naming."""
    return create_perception_port()
