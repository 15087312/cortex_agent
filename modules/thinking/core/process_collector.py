"""Thinking process collection abstractions.

Other modules should depend on these interfaces/factories rather than reading
ContinuousThinker internals directly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from modules.thinking.core.control_tools import ThinkingControlDecision, ThinkingTaskContext


@dataclass
class ThinkingStepRecord:
    """One collected step from a continuous thinking loop."""

    round_num: int
    content: str
    duration_ms: float = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ThinkingProcessSnapshot:
    """A sanitized snapshot of one thinking loop."""

    session_id: str
    model_id: str
    tier: str
    task_context: Optional[ThinkingTaskContext]
    steps: List[ThinkingStepRecord] = field(default_factory=list)
    final_result: str = ""
    stopped_by_continue_false: bool = False
    control_decision: Optional[ThinkingControlDecision] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class ThinkingProcessCollector(ABC):
    """Abstract collector for continuous thinking loop process data."""

    @abstractmethod
    def reset(
        self,
        *,
        session_id: str,
        model_id: str,
        tier: str,
        task_context: Optional[ThinkingTaskContext] = None,
    ) -> None:
        """Start a new collection window."""

    @abstractmethod
    def record_step(
        self,
        *,
        round_num: int,
        content: str,
        duration_ms: float = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a sanitized thinking step."""

    @abstractmethod
    def complete(
        self,
        *,
        final_result: str,
        control_decision: Optional[ThinkingControlDecision] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ThinkingProcessSnapshot:
        """Finish collection and return a snapshot."""

    @abstractmethod
    def snapshot(self) -> ThinkingProcessSnapshot:
        """Return the current snapshot."""


class InMemoryThinkingProcessCollector(ThinkingProcessCollector):
    """Default in-memory collector implementation."""

    def __init__(self) -> None:
        self._snapshot = ThinkingProcessSnapshot(
            session_id="",
            model_id="",
            tier="",
            task_context=None,
        )

    def reset(
        self,
        *,
        session_id: str,
        model_id: str,
        tier: str,
        task_context: Optional[ThinkingTaskContext] = None,
    ) -> None:
        self._snapshot = ThinkingProcessSnapshot(
            session_id=session_id,
            model_id=model_id,
            tier=tier,
            task_context=task_context,
        )

    def record_step(
        self,
        *,
        round_num: int,
        content: str,
        duration_ms: float = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._snapshot.steps.append(
            ThinkingStepRecord(
                round_num=round_num,
                content=content,
                duration_ms=duration_ms,
                metadata=metadata or {},
            )
        )

    def complete(
        self,
        *,
        final_result: str,
        control_decision: Optional[ThinkingControlDecision] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ThinkingProcessSnapshot:
        self._snapshot.final_result = final_result
        self._snapshot.control_decision = control_decision
        self._snapshot.stopped_by_continue_false = bool(
            control_decision and not control_decision.should_continue
        )
        if metadata:
            self._snapshot.metadata.update(metadata)
        if control_decision:
            self._snapshot.metadata["control_reason"] = control_decision.reason
            self._snapshot.metadata["control_result_summary"] = control_decision.result_summary
        return self.snapshot()

    def snapshot(self) -> ThinkingProcessSnapshot:
        return ThinkingProcessSnapshot(
            session_id=self._snapshot.session_id,
            model_id=self._snapshot.model_id,
            tier=self._snapshot.tier,
            task_context=self._snapshot.task_context,
            steps=list(self._snapshot.steps),
            final_result=self._snapshot.final_result,
            stopped_by_continue_false=self._snapshot.stopped_by_continue_false,
            control_decision=self._snapshot.control_decision,
            metadata=dict(self._snapshot.metadata),
        )


class ThinkingProcessCollectorFactory(ABC):
    """Factory abstraction for thinking process collectors."""

    @abstractmethod
    def create_collector(self) -> ThinkingProcessCollector:
        """Create a collector instance."""


class DefaultThinkingProcessCollectorFactory(ThinkingProcessCollectorFactory):
    """Default collector factory."""

    def create_collector(self) -> ThinkingProcessCollector:
        return InMemoryThinkingProcessCollector()


_DEFAULT_FACTORY: ThinkingProcessCollectorFactory = DefaultThinkingProcessCollectorFactory()


def set_thinking_process_collector_factory(factory: ThinkingProcessCollectorFactory) -> None:
    """Override the global collector factory."""
    global _DEFAULT_FACTORY
    _DEFAULT_FACTORY = factory


def get_thinking_process_collector_factory() -> ThinkingProcessCollectorFactory:
    """Return the configured collector factory."""
    return _DEFAULT_FACTORY


def create_thinking_process_collector() -> ThinkingProcessCollector:
    """Create a thinking process collector through the configured factory."""
    return _DEFAULT_FACTORY.create_collector()
