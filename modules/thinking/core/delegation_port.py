"""Delegation abstraction for single-model thinking loops.

ContinuousThinker owns the decision that a model wants to delegate, but the
concrete system action (probe_start / runner activation) belongs behind this
port so the thinker does not depend on probe tools directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol


@dataclass
class DelegationRequest:
    """A model-internal delegation request emitted during one thinking call."""

    role: str
    task: str
    session_id: str = ""
    caller_model_id: str = ""
    caller_tier: str = "large"
    return_to_model_id: str = ""
    return_to_session_id: str = ""
    task_id: str = ""
    wait_seconds: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DelegationResult:
    """Result of dispatching a delegation request."""

    success: bool
    probe_id: str = ""
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class DelegationPort(Protocol):
    """Abstract port used by ContinuousThinker to dispatch delegations."""

    def delegate(self, request: DelegationRequest) -> DelegationResult:
        ...


class ProbeDelegationAdapter:
    """Delegation adapter backed by probe_start."""

    def delegate(self, request: DelegationRequest) -> DelegationResult:
        try:
            from modules.thinking.intent import resolve_role
            from modules.thinking.probes.probe_tools import probe_start

            identity = resolve_role(request.role)
            if identity is None:
                return DelegationResult(
                    success=False,
                    error=f"未知委托角色: {request.role}",
                )

            target_tier, identity_key = identity
            raw = probe_start(
                target_tier=target_tier,
                identity_key=identity_key,
                task_description=request.task,
                probe_priority=str(request.metadata.get("probe_priority", "MEDIUM")),
                ttl_seconds=int(request.metadata.get("ttl_seconds", 1800)),
                _caller_role=request.caller_tier,
                _caller_model_id=request.caller_model_id,
                _session_id=request.session_id,
                return_to_model_id=request.return_to_model_id or request.caller_model_id,
                return_to_session_id=request.return_to_session_id or request.session_id,
                task_id=request.task_id,
            )
            return DelegationResult(
                success=bool(raw.get("success")),
                probe_id=str(raw.get("probe_id", "") or ""),
                error=str(raw.get("error", "") or ""),
                metadata=dict(raw),
            )
        except Exception as e:
            return DelegationResult(success=False, error=str(e))


def create_delegation_port() -> DelegationPort:
    """Factory for the default delegation port."""
    return ProbeDelegationAdapter()
