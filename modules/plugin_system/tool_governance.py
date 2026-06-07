from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass, field, fields as dataclass_fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from .audit import AuditLogger, NullAuditLogger, new_request_id
from .models import PermissionName
from .tool_errors import tool_error_info


class ToolExecutionMode(str):
    EXECUTE = "execute"
    DRY_RUN = "dry_run"
    PREVIEW_ONLY = "preview_only"
    CONFIRMATION_ONLY = "confirmation_only"


GOVERNANCE_ERROR_MESSAGES = {
    "CONFIRMATION_REQUIRED": "This tool requires confirmation before execution.",
    "CONFIRMATION_INVALID": "Tool confirmation is invalid for this request.",
    "CONFIRMATION_EXPIRED": "Tool confirmation has expired.",
    "BUDGET_EXCEEDED": "Tool call budget has been exceeded.",
    "RATE_LIMITED": "Tool call rate limit has been exceeded.",
    "DUPLICATE_TOOL_CALL": "This tool call was already completed.",
    "DUPLICATE_IN_PROGRESS": "An equivalent tool call is already in progress.",
    "IDEMPOTENCY_CONFLICT": "Idempotency key does not match this tool call.",
    "DRY_RUN_ONLY": "Tool call was evaluated without executing the plugin.",
    "TOOL_LOOP_DETECTED": "Repeated equivalent tool calls were detected.",
    "TOOL_STORM_RATE_LIMITED": "Tool call storm protection rate-limited this session.",
    "CONFIRMATION_NOT_REQUIRED": "This tool does not require confirmation.",
}

HIGH_RISK_CONFIRMATION_PERMISSIONS = frozenset(
    {
        PermissionName.NETWORK_OUTBOUND.value,
        PermissionName.FS_WRITE.value,
        PermissionName.MEMORY_WRITE.value,
        PermissionName.OUTPUT_SEND.value,
    }
)
SIDE_EFFECT_PERMISSIONS = HIGH_RISK_CONFIRMATION_PERMISSIONS


@dataclass(frozen=True)
class GovernanceStoreMetadata:
    store_kind: str
    persistent: bool
    multi_process_safe: bool
    multi_instance_safe: bool
    production_recommended: bool
    description: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolCallBudget:
    max_tool_calls_per_session: int
    max_high_risk_tool_calls_per_session: int
    max_tool_calls_per_minute: int
    max_total_result_bytes_per_session: int
    max_denied_calls_per_session: int = 6


@dataclass(frozen=True)
class ToolGovernancePolicy:
    model_budget: ToolCallBudget = field(
        default_factory=lambda: ToolCallBudget(
            max_tool_calls_per_session=8,
            max_high_risk_tool_calls_per_session=1,
            max_tool_calls_per_minute=10,
            max_total_result_bytes_per_session=256 * 1024,
        )
    )
    expert_budget: ToolCallBudget = field(
        default_factory=lambda: ToolCallBudget(
            max_tool_calls_per_session=24,
            max_high_risk_tool_calls_per_session=8,
            max_tool_calls_per_minute=30,
            max_total_result_bytes_per_session=1024 * 1024,
        )
    )
    admin_budget: ToolCallBudget = field(
        default_factory=lambda: ToolCallBudget(
            max_tool_calls_per_session=64,
            max_high_risk_tool_calls_per_session=32,
            max_tool_calls_per_minute=60,
            max_total_result_bytes_per_session=4 * 1024 * 1024,
        )
    )
    confirmation_ttl_seconds: int = 300
    require_confirmation_for_model_high_risk: bool = True
    require_confirmation_for_expert_high_risk: bool = True
    require_confirmation_for_admin_high_risk: bool = False
    repeated_args_deny_threshold: int = 4
    storm_denied_threshold: int = 4

    def budget_for(self, actor_role: str) -> ToolCallBudget:
        role = (actor_role or "model").lower()
        if role == "admin":
            return self.admin_budget
        if role == "expert":
            return self.expert_budget
        return self.model_budget

    def requires_confirmation(self, actor_role: str, risk: "ToolRiskDecision") -> bool:
        if not risk.requires_confirmation:
            return False
        role = (actor_role or "model").lower()
        if role == "admin":
            return self.require_confirmation_for_admin_high_risk
        if role == "expert":
            return self.require_confirmation_for_expert_high_risk
        return self.require_confirmation_for_model_high_risk


@dataclass(frozen=True)
class ToolRiskDecision:
    risk_level: str
    required_permissions: list[str]
    side_effecting: bool
    requires_confirmation: bool
    expected_side_effects: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConfirmationRequirement:
    confirmation_token: str
    token_hash: str
    expires_at: str
    actor_role: str
    conversation_id: str | None
    model_tool_name: str
    args_hash: str
    required_permissions: list[str]
    risk_level: str
    created_at: str
    accepted_at: str | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "confirmation_token": self.confirmation_token,
            "expires_at": self.expires_at,
            "risk_level": self.risk_level,
            "required_permissions": self.required_permissions,
            "model_tool_name": self.model_tool_name,
        }


@dataclass(frozen=True)
class ConfirmationRequest:
    actor_role: str
    conversation_id: str | None
    model_tool_name: str
    args_hash: str
    required_permissions: list[str]
    risk_level: str
    ttl_seconds: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConfirmationDecision:
    status: str
    requirement: ConfirmationRequirement | None = None
    token_hash: str | None = None
    provider_kind: str = "local"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.requirement is not None:
            payload["requirement"] = self.requirement.to_safe_dict()
        return payload


@dataclass(frozen=True)
class ConfirmationStatus:
    status: str
    token_hash: str | None
    expires_at: str | None
    actor_role: str | None
    conversation_id: str | None
    model_tool_name: str | None
    provider_kind: str = "local"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ConfirmationProvider(Protocol):
    provider_kind: str
    production_recommended: bool

    def metadata(self) -> dict[str, Any]:
        ...

    def create_confirmation(self, request: ConfirmationRequest) -> ConfirmationRequirement:
        ...

    def verify_confirmation(
        self,
        token: str,
        *,
        actor_role: str,
        conversation_id: str | None,
        model_tool_name: str,
        args_hash: str,
        required_permissions: list[str],
    ) -> ConfirmationDecision:
        ...

    def deny_confirmation(self, token: str, *, reason: str = "denied") -> ConfirmationDecision:
        ...

    def expire_confirmation(self, token: str) -> ConfirmationDecision:
        ...

    def get_status(self, token: str) -> ConfirmationStatus:
        ...

    def health(self) -> dict[str, Any]:
        ...


@dataclass
class IdempotencyRecord:
    idempotency_key_hash: str
    model_tool_name: str
    args_hash: str
    status: str
    created_at: str
    updated_at: str
    safe_envelope: dict[str, Any] | None = None
    error_code: str | None = None


@dataclass
class ToolCallSession:
    session_id: str
    actor_role: str
    conversation_id: str | None
    started_at: str
    last_seen_at: str
    call_count: int = 0
    high_risk_count: int = 0
    denied_count: int = 0
    total_result_bytes: int = 0
    tool_counts: dict[str, int] = field(default_factory=dict)
    recent_args_hashes: dict[str, int] = field(default_factory=dict)
    call_timestamps: list[float] = field(default_factory=list)

    def to_safe_dict(self, budget: ToolCallBudget) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "actor_role": self.actor_role,
            "conversation_id": self.conversation_id,
            "call_count": self.call_count,
            "high_risk_count": self.high_risk_count,
            "denied_count": self.denied_count,
            "total_result_bytes": self.total_result_bytes,
            "remaining_budget": {
                "tool_calls": max(0, budget.max_tool_calls_per_session - self.call_count),
                "high_risk_tool_calls": max(
                    0,
                    budget.max_high_risk_tool_calls_per_session - self.high_risk_count,
                ),
                "result_bytes": max(
                    0,
                    budget.max_total_result_bytes_per_session - self.total_result_bytes,
                ),
            },
        }


@dataclass(frozen=True)
class ToolExecutionDecision:
    allowed: bool
    decision: str
    reason: str
    risk_level: str
    requires_confirmation: bool
    confirmation_token: str | None
    confirmation: dict[str, Any] | None
    remaining_budget: dict[str, int]
    idempotency_status: str | None
    idempotency_key: str
    idempotency_key_hash: str
    args_hash: str
    execution_mode: str
    audit_fields: dict[str, Any]
    safe_envelope: dict[str, Any] | None = None
    error_code: str | None = None
    preview: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ToolCallSessionStore:
    """In-memory governance store for single-process local execution.

    This store is intentionally local and process-bound. Production multi-instance
    deployments should replace it with an external durable store before relying on
    cross-process budgets, confirmations, or idempotency.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, ToolCallSession] = {}
        self._confirmations: dict[str, ConfirmationRequirement] = {}
        self._idempotency: dict[str, IdempotencyRecord] = {}

    def metadata(self) -> GovernanceStoreMetadata:
        return GovernanceStoreMetadata(
            store_kind="memory",
            persistent=False,
            multi_process_safe=False,
            multi_instance_safe=False,
            production_recommended=False,
            description="Single-process in-memory governance store.",
        )

    def health(self) -> dict[str, Any]:
        metadata = self.metadata().to_dict()
        return {
            "status": "pass",
            "store_kind": metadata["store_kind"],
            "production_recommended": metadata["production_recommended"],
            "warnings": ["memory_store_not_production_multi_instance_safe"],
            "generated_at": _utc_now(),
        }

    def session_for(
        self,
        *,
        actor_role: str,
        conversation_id: str | None,
        request_id: str,
    ) -> ToolCallSession:
        session_id = conversation_id or f"request:{request_id}"
        now = _utc_now()
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = ToolCallSession(
                    session_id=session_id,
                    actor_role=actor_role,
                    conversation_id=conversation_id,
                    started_at=now,
                    last_seen_at=now,
                )
                self._sessions[session_id] = session
            session.last_seen_at = now
            return session

    def save_session(self, session: ToolCallSession) -> None:
        with self._lock:
            self._sessions[session.session_id] = session

    def get_session(self, conversation_id: str) -> ToolCallSession | None:
        with self._lock:
            return self._sessions.get(conversation_id)

    def reset_session(self, conversation_id: str) -> bool:
        with self._lock:
            removed = self._sessions.pop(conversation_id, None)
            return removed is not None

    def session_summary(self, conversation_id: str, *, policy: ToolGovernancePolicy | None = None) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(conversation_id)
            if session is None:
                return {
                    "conversation_id": conversation_id,
                    "session_found": False,
                    "call_count": 0,
                    "high_risk_count": 0,
                    "denied_count": 0,
                    "total_result_bytes": 0,
                    "tool_counts": {},
                    "recent_call_count": 0,
                    "remaining_budget": {},
                }
            budget = (policy or ToolGovernancePolicy()).budget_for(session.actor_role)
            safe = session.to_safe_dict(budget)
            return {
                **safe,
                "session_found": True,
                "tool_counts": dict(session.tool_counts),
                "recent_call_count": _recent_call_count(session),
            }

    def create_confirmation(
        self,
        *,
        actor_role: str,
        conversation_id: str | None,
        model_tool_name: str,
        args_hash: str,
        required_permissions: list[str],
        risk_level: str,
        ttl_seconds: int,
    ) -> ConfirmationRequirement:
        token = uuid.uuid4().hex
        now = datetime.now(UTC)
        requirement = ConfirmationRequirement(
            confirmation_token=token,
            token_hash=_hash_text(token),
            expires_at=(now + timedelta(seconds=max(1, ttl_seconds))).isoformat(),
            actor_role=actor_role,
            conversation_id=conversation_id,
            model_tool_name=model_tool_name,
            args_hash=args_hash,
            required_permissions=sorted(required_permissions),
            risk_level=risk_level,
            created_at=now.isoformat(),
        )
        with self._lock:
            self._confirmations[requirement.token_hash] = requirement
        return requirement

    def validate_confirmation(
        self,
        token: str,
        *,
        actor_role: str,
        conversation_id: str | None,
        model_tool_name: str,
        args_hash: str,
        required_permissions: list[str],
    ) -> tuple[str, ConfirmationRequirement | None]:
        token_hash = _hash_text(token)
        with self._lock:
            requirement = self._confirmations.get(token_hash)
            if requirement is None:
                return "invalid", None
            if _parse_utc(requirement.expires_at) < datetime.now(UTC):
                return "expired", requirement
            if (
                requirement.actor_role != actor_role
                or requirement.conversation_id != conversation_id
                or requirement.model_tool_name != model_tool_name
                or requirement.args_hash != args_hash
                or sorted(requirement.required_permissions) != sorted(required_permissions)
            ):
                return "invalid", requirement
            accepted = ConfirmationRequirement(
                confirmation_token=requirement.confirmation_token,
                token_hash=requirement.token_hash,
                expires_at=requirement.expires_at,
                actor_role=requirement.actor_role,
                conversation_id=requirement.conversation_id,
                model_tool_name=requirement.model_tool_name,
                args_hash=requirement.args_hash,
                required_permissions=requirement.required_permissions,
                risk_level=requirement.risk_level,
                created_at=requirement.created_at,
                accepted_at=_utc_now(),
            )
            self._confirmations[token_hash] = accepted
            return "accepted", accepted

    def get_confirmation(self, token: str) -> ConfirmationRequirement | None:
        token_hash = _hash_text(token)
        with self._lock:
            return self._confirmations.get(token_hash)

    def remove_confirmation(self, token: str) -> ConfirmationRequirement | None:
        token_hash = _hash_text(token)
        with self._lock:
            return self._confirmations.pop(token_hash, None)

    def idempotency_record(self, key: str) -> IdempotencyRecord | None:
        key_hash = _hash_text(key)
        with self._lock:
            return self._idempotency.get(key_hash)

    def begin_idempotency(
        self,
        key: str,
        *,
        model_tool_name: str,
        args_hash: str,
    ) -> IdempotencyRecord:
        now = _utc_now()
        record = IdempotencyRecord(
            idempotency_key_hash=_hash_text(key),
            model_tool_name=model_tool_name,
            args_hash=args_hash,
            status="in_progress",
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._idempotency[record.idempotency_key_hash] = record
        return record

    def complete_idempotency(
        self,
        key: str,
        *,
        ok: bool,
        safe_envelope: dict[str, Any],
        error_code: str | None,
    ) -> None:
        now = _utc_now()
        key_hash = _hash_text(key)
        with self._lock:
            record = self._idempotency.get(key_hash)
            if record is None:
                return
            record.status = "succeeded" if ok else "failed"
            record.safe_envelope = safe_envelope if ok else None
            record.error_code = error_code
            record.updated_at = now

    def record_allowed_call(
        self,
        session: ToolCallSession,
        *,
        model_tool_name: str,
        args_hash: str,
        high_risk: bool,
        result_size_bytes: int,
    ) -> None:
        with self._lock:
            session.call_count += 1
            session.high_risk_count += 1 if high_risk else 0
            session.total_result_bytes += max(0, result_size_bytes)
            session.tool_counts[model_tool_name] = session.tool_counts.get(model_tool_name, 0) + 1
            key = f"{model_tool_name}:{args_hash}"
            session.recent_args_hashes[key] = session.recent_args_hashes.get(key, 0) + 1
            session.call_timestamps.append(datetime.now(UTC).timestamp())
            session.call_timestamps = [
                timestamp for timestamp in session.call_timestamps if datetime.now(UTC).timestamp() - timestamp <= 60
            ]
            session.last_seen_at = _utc_now()

    def record_denied_call(self, session: ToolCallSession) -> None:
        with self._lock:
            session.denied_count += 1
            session.last_seen_at = _utc_now()


class FileToolCallSessionStore(ToolCallSessionStore):
    """Durable local JSON governance store.

    This keeps confirmation/idempotency indexes hashed and only persists safe
    envelopes. It is suitable for one local service process or low-contention
    development deployments. Multi-host production deployments should use an
    external transactional store with the same method contract.
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        self._load()

    def metadata(self) -> GovernanceStoreMetadata:
        return GovernanceStoreMetadata(
            store_kind="local_file",
            persistent=True,
            multi_process_safe=False,
            multi_instance_safe=False,
            production_recommended=False,
            description="Local JSON governance store for one service process.",
        )

    def health(self) -> dict[str, Any]:
        metadata = self.metadata().to_dict()
        return {
            "status": "warn",
            "store_kind": metadata["store_kind"],
            "path": str(self.path),
            "persistent": True,
            "multi_instance_safe": False,
            "production_recommended": False,
            "warnings": ["local_file_store_not_multi_instance_safe"],
            "generated_at": _utc_now(),
        }

    def session_for(
        self,
        *,
        actor_role: str,
        conversation_id: str | None,
        request_id: str,
    ) -> ToolCallSession:
        session = super().session_for(
            actor_role=actor_role,
            conversation_id=conversation_id,
            request_id=request_id,
        )
        self._persist()
        return session

    def save_session(self, session: ToolCallSession) -> None:
        super().save_session(session)
        self._persist()

    def reset_session(self, conversation_id: str) -> bool:
        removed = super().reset_session(conversation_id)
        self._persist()
        return removed

    def create_confirmation(self, **kwargs: Any) -> ConfirmationRequirement:
        requirement = super().create_confirmation(**kwargs)
        self._persist()
        return requirement

    def validate_confirmation(self, *args: Any, **kwargs: Any) -> tuple[str, ConfirmationRequirement | None]:
        result = super().validate_confirmation(*args, **kwargs)
        self._persist()
        return result

    def begin_idempotency(self, *args: Any, **kwargs: Any) -> IdempotencyRecord:
        record = super().begin_idempotency(*args, **kwargs)
        self._persist()
        return record

    def complete_idempotency(self, *args: Any, **kwargs: Any) -> None:
        super().complete_idempotency(*args, **kwargs)
        self._persist()

    def record_allowed_call(self, *args: Any, **kwargs: Any) -> None:
        super().record_allowed_call(*args, **kwargs)
        self._persist()

    def record_denied_call(self, session: ToolCallSession) -> None:
        super().record_denied_call(session)
        self._persist()

    def _load(self) -> None:
        with self._lock:
            if not self.path.exists():
                return
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                raise ValueError(f"failed to load tool governance store: {self.path}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"tool governance store must contain a JSON object: {self.path}")
            self._sessions = _dataclass_mapping(
                payload.get("sessions"),
                ToolCallSession,
                key_field="session_id",
            )
            self._confirmations = _dataclass_mapping(
                payload.get("confirmations"),
                ConfirmationRequirement,
                key_field="token_hash",
            )
            self._idempotency = _dataclass_mapping(
                payload.get("idempotency"),
                IdempotencyRecord,
                key_field="idempotency_key_hash",
            )

    def _persist(self) -> None:
        with self._lock:
            payload = {
                "schema_version": 1,
                "updated_at": _utc_now(),
                "sessions": [asdict(item) for item in self._sessions.values()],
                "confirmations": [_safe_confirmation_record(item) for item in self._confirmations.values()],
                "idempotency": [asdict(item) for item in self._idempotency.values()],
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=str(self.path.parent),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    handle.write("\n")
                os.replace(tmp_name, self.path)
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)


class ExternalGovernanceStore(ToolCallSessionStore):
    """Placeholder base for injected external transactional governance stores."""

    def metadata(self) -> GovernanceStoreMetadata:
        return GovernanceStoreMetadata(
            store_kind="external",
            persistent=True,
            multi_process_safe=True,
            multi_instance_safe=True,
            production_recommended=True,
            description="External governance store supplied by the host system.",
        )

    def reserve_idempotency_key(self, key: str, request_summary: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "unconfigured",
            "idempotency_key_hash": _hash_text(key),
            "request_summary": summarize_json(request_summary),
        }

    def complete_idempotency_key(self, key: str, safe_envelope: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "unconfigured",
            "idempotency_key_hash": _hash_text(key),
            "safe_envelope_summary": summarize_json(safe_envelope),
        }

    def get_idempotency_record(self, key: str) -> dict[str, Any] | None:
        return {"status": "unconfigured", "idempotency_key_hash": _hash_text(key)}

    def increment_rate_counter(self, scope: str, window: str) -> dict[str, Any]:
        return {"status": "unconfigured", "scope": scope, "window": window, "count": 0}

    def get_rate_counter(self, scope: str, window: str) -> dict[str, Any]:
        return {"status": "unconfigured", "scope": scope, "window": window, "count": 0}

    def health(self) -> dict[str, Any]:
        metadata = self.metadata().to_dict()
        return {
            "status": "pass",
            "store_kind": metadata["store_kind"],
            "production_recommended": True,
            "warnings": [],
            "generated_at": _utc_now(),
        }


class UnconfiguredExternalGovernanceStore(ExternalGovernanceStore):
    """Non-production stub documenting the external governance store contract."""

    def metadata(self) -> GovernanceStoreMetadata:
        return GovernanceStoreMetadata(
            store_kind="unconfigured",
            persistent=False,
            multi_process_safe=False,
            multi_instance_safe=False,
            production_recommended=False,
            description="External governance store is not configured.",
        )

    def health(self) -> dict[str, Any]:
        return {
            "status": "warn",
            "store_kind": "unconfigured",
            "production_recommended": False,
            "warnings": ["external_governance_store_unconfigured"],
            "generated_at": _utc_now(),
        }


class LocalConfirmationProvider:
    provider_kind = "local"
    production_recommended = False

    def __init__(
        self,
        store: ToolCallSessionStore,
        *,
        audit_logger: AuditLogger | NullAuditLogger | None = None,
    ) -> None:
        self.store = store
        self.audit_logger = audit_logger or NullAuditLogger()

    def metadata(self) -> dict[str, Any]:
        store_metadata = governance_store_metadata(self.store)
        return {
            "provider_kind": self.provider_kind,
            "production_recommended": self.production_recommended,
            "token_scope": "actor_role/conversation_id/model_tool_name/args_hash/permissions",
            "stores_raw_args": False,
            "external_ui": False,
            "store": store_metadata,
        }

    def health(self) -> dict[str, Any]:
        return {
            "status": "warn",
            "provider_kind": self.provider_kind,
            "production_recommended": self.production_recommended,
            "warnings": ["local_confirmation_provider_not_external_approval"],
            "generated_at": _utc_now(),
        }

    def create_confirmation(self, request: ConfirmationRequest) -> ConfirmationRequirement:
        requirement = self.store.create_confirmation(
            actor_role=request.actor_role,
            conversation_id=request.conversation_id,
            model_tool_name=request.model_tool_name,
            args_hash=request.args_hash,
            required_permissions=request.required_permissions,
            risk_level=request.risk_level,
            ttl_seconds=request.ttl_seconds,
        )
        self._audit(
            "plugin.confirmation.created",
            "success",
            details={
                "provider_kind": self.provider_kind,
                "actor_role": request.actor_role,
                "conversation_id": request.conversation_id,
                "model_tool_name": request.model_tool_name,
                "args_hash": request.args_hash,
                "required_permissions": sorted(request.required_permissions),
                "risk_level": request.risk_level,
                "confirmation_token_hash": requirement.token_hash,
                "expires_at": requirement.expires_at,
            },
            decision="require_confirmation",
            reason="CONFIRMATION_REQUIRED",
        )
        return requirement

    def verify_confirmation(
        self,
        token: str,
        *,
        actor_role: str,
        conversation_id: str | None,
        model_tool_name: str,
        args_hash: str,
        required_permissions: list[str],
    ) -> ConfirmationDecision:
        status, requirement = self.store.validate_confirmation(
            token,
            actor_role=actor_role,
            conversation_id=conversation_id,
            model_tool_name=model_tool_name,
            args_hash=args_hash,
            required_permissions=required_permissions,
        )
        event = "plugin.confirmation.verified"
        result = "success" if status == "accepted" else "error"
        reason = "confirmation_accepted" if status == "accepted" else (
            "CONFIRMATION_EXPIRED" if status == "expired" else "CONFIRMATION_INVALID"
        )
        self._audit(
            event,
            result,
            details={
                "provider_kind": self.provider_kind,
                "status": status,
                "actor_role": actor_role,
                "conversation_id": conversation_id,
                "model_tool_name": model_tool_name,
                "args_hash": args_hash,
                "required_permissions": sorted(required_permissions),
                "confirmation_token_hash": _hash_text(token),
            },
            decision="allow" if status == "accepted" else "deny",
            reason=reason,
        )

        return ConfirmationDecision(
            status=status,
            requirement=requirement,
            token_hash=_hash_text(token),
            provider_kind=self.provider_kind,
        )

    def deny_confirmation(self, token: str, *, reason: str = "denied") -> ConfirmationDecision:
        requirement = self.store.remove_confirmation(token)
        token_hash = _hash_text(token)
        self._audit(
            "plugin.confirmation.denied",
            "error",
            details={
                "provider_kind": self.provider_kind,
                "status": "denied",
                "reason": reason,
                "confirmation_token_hash": token_hash,
                "model_tool_name": requirement.model_tool_name if requirement else None,
            },
            decision="deny",
            reason=reason,
        )
        return ConfirmationDecision(
            status="denied",
            requirement=requirement,
            token_hash=token_hash,
            provider_kind=self.provider_kind,
        )

    def expire_confirmation(self, token: str) -> ConfirmationDecision:
        requirement = self.store.remove_confirmation(token)
        token_hash = _hash_text(token)
        self._audit(
            "plugin.confirmation.expired",
            "error",
            details={
                "provider_kind": self.provider_kind,
                "status": "expired",
                "confirmation_token_hash": token_hash,
                "model_tool_name": requirement.model_tool_name if requirement else None,
            },
            decision="deny",
            reason="CONFIRMATION_EXPIRED",
        )
        return ConfirmationDecision(
            status="expired",
            requirement=requirement,
            token_hash=token_hash,
            provider_kind=self.provider_kind,
        )

    def get_status(self, token: str) -> ConfirmationStatus:
        requirement = self.store.get_confirmation(token)
        token_hash = _hash_text(token)
        if requirement is None:
            return ConfirmationStatus(
                status="missing",
                token_hash=token_hash,
                expires_at=None,
                actor_role=None,
                conversation_id=None,
                model_tool_name=None,
                provider_kind=self.provider_kind,
            )
        status = "expired" if _parse_utc(requirement.expires_at) < datetime.now(UTC) else "pending"
        if requirement.accepted_at is not None:
            status = "approved"
        return ConfirmationStatus(
            status=status,
            token_hash=token_hash,
            expires_at=requirement.expires_at,
            actor_role=requirement.actor_role,
            conversation_id=requirement.conversation_id,
            model_tool_name=requirement.model_tool_name,
            provider_kind=self.provider_kind,
        )

    def _audit(
        self,
        event: str,
        result: str,
        *,
        details: dict[str, Any],
        decision: str,
        reason: str,
    ) -> None:
        self.audit_logger.record(
            event,
            result,
            request_id=new_request_id(),
            action="confirmation",
            details={key: value for key, value in details.items() if value is not None},
            decision=decision,
            reason=reason,
        )


class UnconfiguredApprovalProvider:
    """Non-production stub for a future external approval provider."""

    provider_kind = "unconfigured"
    production_recommended = False

    def __init__(self, *, audit_logger: AuditLogger | NullAuditLogger | None = None) -> None:
        self.audit_logger = audit_logger or NullAuditLogger()

    def metadata(self) -> dict[str, Any]:
        return {
            "provider_kind": self.provider_kind,
            "production_recommended": self.production_recommended,
            "external_ui": False,
            "stores_raw_args": False,
            "token_scope": "unconfigured",
            "warnings": ["external_approval_provider_unconfigured"],
        }

    def create_confirmation(self, request: ConfirmationRequest) -> ConfirmationRequirement:
        raise RuntimeError("external approval provider is not configured")

    def create_confirmation_request(self, **request: Any) -> dict[str, Any]:
        self._audit_health("plugin.external_approval_provider_unconfigured")
        return {
            "status": "unconfigured",
            "request_summary": summarize_json(request),
            "provider_kind": self.provider_kind,
        }

    def get_confirmation_status(self, token: str) -> ConfirmationStatus:
        return self.get_status(token)

    def approve(self, token: str, approver_id: str, reason: str) -> ConfirmationDecision:
        return ConfirmationDecision(status="unconfigured", token_hash=_hash_text(token), provider_kind=self.provider_kind)

    def deny(self, token: str, approver_id: str, reason: str) -> ConfirmationDecision:
        return ConfirmationDecision(status="unconfigured", token_hash=_hash_text(token), provider_kind=self.provider_kind)

    def expire(self, token: str) -> ConfirmationDecision:
        return self.expire_confirmation(token)

    def verify_confirmation(
        self,
        token: str,
        *,
        actor_role: str,
        conversation_id: str | None,
        model_tool_name: str,
        args_hash: str,
        required_permissions: list[str],
    ) -> ConfirmationDecision:
        return ConfirmationDecision(status="unconfigured", token_hash=_hash_text(token), provider_kind=self.provider_kind)

    def deny_confirmation(self, token: str, *, reason: str = "denied") -> ConfirmationDecision:
        return ConfirmationDecision(status="unconfigured", token_hash=_hash_text(token), provider_kind=self.provider_kind)

    def expire_confirmation(self, token: str) -> ConfirmationDecision:
        return ConfirmationDecision(status="unconfigured", token_hash=_hash_text(token), provider_kind=self.provider_kind)

    def get_status(self, token: str) -> ConfirmationStatus:
        return ConfirmationStatus(
            status="unconfigured",
            token_hash=_hash_text(token),
            expires_at=None,
            actor_role=None,
            conversation_id=None,
            model_tool_name=None,
            provider_kind=self.provider_kind,
        )

    def health(self) -> dict[str, Any]:
        self._audit_health("plugin.external_approval_provider_unconfigured")
        return {
            "status": "warn",
            "provider_kind": self.provider_kind,
            "production_recommended": False,
            "warnings": ["external_approval_provider_unconfigured"],
            "generated_at": _utc_now(),
        }

    def _audit_health(self, event: str) -> None:
        self.audit_logger.record(
            event,
            "warning",
            request_id=new_request_id(),
            action="approval_provider",
            details={"provider_kind": self.provider_kind, "production_recommended": False},
            decision="warn",
            reason="unconfigured",
        )


class ToolGovernanceController:
    def __init__(
        self,
        *,
        policy: ToolGovernancePolicy | None = None,
        store: ToolCallSessionStore | None = None,
        confirmation_provider: ConfirmationProvider | None = None,
        audit_logger: AuditLogger | NullAuditLogger | None = None,
    ) -> None:
        self.policy = policy or ToolGovernancePolicy()
        self.store = store or ToolCallSessionStore()
        self.audit_logger = audit_logger or NullAuditLogger()
        self.confirmation_provider = confirmation_provider or LocalConfirmationProvider(
            self.store,
            audit_logger=self.audit_logger,
        )

    def precheck(
        self,
        *,
        spec: Any,
        args: dict[str, Any],
        provider: str,
        provider_call_id: str | None,
        provider_tool_name: str,
        actor_role: str,
        conversation_id: str | None,
        request_id: str,
        execution_mode: str,
        confirmation_token: str | None,
        idempotency_key: str | None,
    ) -> ToolExecutionDecision:
        mode = normalize_execution_mode(execution_mode)
        risk = tool_risk_decision(spec)
        budget = self.policy.budget_for(actor_role)
        session = self.store.session_for(
            actor_role=actor_role,
            conversation_id=conversation_id,
            request_id=request_id,
        )
        args_hash = stable_json_hash(args)
        resolved_idempotency_key = idempotency_key or derive_idempotency_key(
            conversation_id=conversation_id,
            provider_call_id=provider_call_id,
            model_tool_name=spec.name,
            args_hash=args_hash,
        )
        idempotency_key_hash = _hash_text(resolved_idempotency_key)
        audit_fields = {
            "request_id": request_id,
            "provider": provider,
            "provider_call_id": provider_call_id,
            "provider_tool_name": provider_tool_name,
            "actor_role": actor_role,
            "conversation_id": conversation_id,
            "session_id": session.session_id,
            "plugin_id": spec.plugin_id,
            "plugin_version": spec.plugin_version,
            "tool_name": spec.tool_name,
            "model_tool_name": spec.name,
            "risk_level": risk.risk_level,
            "required_permissions": risk.required_permissions,
            "args_hash": args_hash,
            "args_summary": summarize_json(args),
            "idempotency_key_hash": idempotency_key_hash,
            "execution_mode": mode,
        }

        if mode in {ToolExecutionMode.DRY_RUN, ToolExecutionMode.PREVIEW_ONLY}:
            preview = tool_preview(spec, args=args, risk=risk, execution_mode=mode)
            self._audit(
                "plugin.tool_dry_run",
                "success",
                request_id=request_id,
                spec=spec,
                details={**audit_fields, "decision": "dry_run_only", "reason": mode},
                decision="dry_run_only",
                reason=mode,
            )
            return _decision(
                allowed=False,
                decision="dry_run_only",
                reason=mode,
                risk=risk,
                budget=budget,
                session=session,
                idempotency_key=resolved_idempotency_key,
                idempotency_key_hash=idempotency_key_hash,
                args_hash=args_hash,
                execution_mode=mode,
                audit_fields=audit_fields,
                error_code="DRY_RUN_ONLY",
                preview=preview,
            )

        duplicate = self._idempotency_precheck(
            resolved_idempotency_key,
            spec=spec,
            args_hash=args_hash,
            risk=risk,
            audit_fields=audit_fields,
            request_id=request_id,
        )
        if duplicate is not None:
            return duplicate

        budget_decision = self._budget_precheck(
            session,
            spec=spec,
            risk=risk,
            budget=budget,
            audit_fields=audit_fields,
            request_id=request_id,
            idempotency_key=resolved_idempotency_key,
            idempotency_key_hash=idempotency_key_hash,
            args_hash=args_hash,
            execution_mode=mode,
        )
        if budget_decision is not None:
            return budget_decision

        confirmation_decision = self._confirmation_precheck(
            spec=spec,
            risk=risk,
            actor_role=actor_role,
            conversation_id=conversation_id,
            confirmation_token=confirmation_token,
            budget=budget,
            session=session,
            audit_fields=audit_fields,
            request_id=request_id,
            idempotency_key=resolved_idempotency_key,
            idempotency_key_hash=idempotency_key_hash,
            args_hash=args_hash,
            execution_mode=mode,
        )
        if confirmation_decision is not None:
            return confirmation_decision

        if mode == ToolExecutionMode.CONFIRMATION_ONLY:
            self.store.record_denied_call(session)
            self._audit(
                "plugin.tool_confirmation_not_required",
                "success",
                request_id=request_id,
                spec=spec,
                details={
                    **audit_fields,
                    "decision": "no_confirmation_required",
                    "reason": "CONFIRMATION_NOT_REQUIRED",
                },
                decision="no_confirmation_required",
                reason="CONFIRMATION_NOT_REQUIRED",
            )
            return _decision(
                allowed=False,
                decision="no_confirmation_required",
                reason="confirmation_not_required",
                risk=risk,
                budget=budget,
                session=session,
                idempotency_key=resolved_idempotency_key,
                idempotency_key_hash=idempotency_key_hash,
                args_hash=args_hash,
                execution_mode=mode,
                audit_fields=audit_fields,
                error_code="CONFIRMATION_NOT_REQUIRED",
            )

        if risk.side_effecting:
            self.store.begin_idempotency(
                resolved_idempotency_key,
                model_tool_name=spec.name,
                args_hash=args_hash,
            )
        return _decision(
            allowed=True,
            decision="allow",
            reason="governance_allow",
            risk=risk,
            budget=budget,
            session=session,
            idempotency_key=resolved_idempotency_key,
            idempotency_key_hash=idempotency_key_hash,
            args_hash=args_hash,
            execution_mode=mode,
            audit_fields={**audit_fields, "decision": "allow", "reason": "governance_allow"},
        )

    def record_result(
        self,
        decision: ToolExecutionDecision,
        *,
        spec: Any,
        actor_role: str,
        conversation_id: str | None,
        request_id: str,
        envelope: dict[str, Any],
    ) -> None:
        budget = self.policy.budget_for(actor_role)
        session = self.store.session_for(
            actor_role=actor_role,
            conversation_id=conversation_id,
            request_id=request_id,
        )
        ok = bool(envelope.get("ok"))
        result_size = _envelope_result_size(envelope)
        risk = tool_risk_decision(spec)
        if ok:
            self.store.record_allowed_call(
                session,
                model_tool_name=spec.name,
                args_hash=decision.args_hash,
                high_risk=risk.requires_confirmation,
                result_size_bytes=result_size,
            )
        else:
            self.store.record_denied_call(session)
        if risk.side_effecting:
            raw_error = envelope.get("error")
            error = raw_error if isinstance(raw_error, dict) else {}
            self.store.complete_idempotency(
                decision.idempotency_key,
                ok=ok,
                safe_envelope=envelope,
                error_code=None if ok else str(error.get("code") or "INTERNAL_ERROR"),
            )
            self._audit(
                "plugin.tool_idempotency_recorded",
                "success" if ok else "error",
                request_id=request_id,
                spec=spec,
                details={
                    **decision.audit_fields,
                    "decision": "record",
                    "ok": ok,
                    "error_code": None if ok else str(error.get("code") or "INTERNAL_ERROR"),
                    "result_size_bytes": result_size,
                    "remaining_budget": session.to_safe_dict(budget)["remaining_budget"],
                },
                decision="record",
                reason="idempotency_recorded",
            )

    def _idempotency_precheck(
        self,
        key: str,
        *,
        spec: Any,
        args_hash: str,
        risk: ToolRiskDecision,
        audit_fields: dict[str, Any],
        request_id: str,
    ) -> ToolExecutionDecision | None:
        if not risk.side_effecting:
            return None
        record = self.store.idempotency_record(key)
        if record is None:
            return None
        budget = self.policy.budget_for(str(audit_fields["actor_role"]))
        session = self.store.session_for(
            actor_role=str(audit_fields["actor_role"]),
            conversation_id=audit_fields.get("conversation_id"),
            request_id=request_id,
        )
        if record.model_tool_name != spec.name or record.args_hash != args_hash:
            self._audit(
                "plugin.tool_duplicate_rejected",
                "error",
                request_id=request_id,
                spec=spec,
                details={**audit_fields, "decision": "deny", "reason": "IDEMPOTENCY_CONFLICT"},
                decision="deny",
                reason="IDEMPOTENCY_CONFLICT",
            )
            self.store.record_denied_call(session)
            return _decision(
                allowed=False,
                decision="deny",
                reason="idempotency_conflict",
                risk=risk,
                budget=budget,
                session=session,
                idempotency_key=key,
                idempotency_key_hash=_hash_text(key),
                args_hash=args_hash,
                execution_mode=str(audit_fields["execution_mode"]),
                audit_fields=audit_fields,
                error_code="IDEMPOTENCY_CONFLICT",
                idempotency_status=record.status,
            )
        if record.status == "in_progress":
            self._audit(
                "plugin.tool_duplicate_rejected",
                "error",
                request_id=request_id,
                spec=spec,
                details={**audit_fields, "decision": "deny", "reason": "DUPLICATE_IN_PROGRESS"},
                decision="deny",
                reason="DUPLICATE_IN_PROGRESS",
            )
            self.store.record_denied_call(session)
            return _decision(
                allowed=False,
                decision="duplicate",
                reason="duplicate_in_progress",
                risk=risk,
                budget=budget,
                session=session,
                idempotency_key=key,
                idempotency_key_hash=_hash_text(key),
                args_hash=args_hash,
                execution_mode=str(audit_fields["execution_mode"]),
                audit_fields=audit_fields,
                error_code="DUPLICATE_IN_PROGRESS",
                idempotency_status=record.status,
            )
        if record.status == "succeeded" and record.safe_envelope is not None:
            self._audit(
                "plugin.tool_idempotency_hit",
                "success",
                request_id=request_id,
                spec=spec,
                details={**audit_fields, "decision": "duplicate", "reason": "DUPLICATE_TOOL_CALL"},
                decision="duplicate",
                reason="DUPLICATE_TOOL_CALL",
            )
            return _decision(
                allowed=False,
                decision="duplicate",
                reason="idempotency_hit",
                risk=risk,
                budget=budget,
                session=session,
                idempotency_key=key,
                idempotency_key_hash=_hash_text(key),
                args_hash=args_hash,
                execution_mode=str(audit_fields["execution_mode"]),
                audit_fields=audit_fields,
                error_code="DUPLICATE_TOOL_CALL",
                idempotency_status=record.status,
                safe_envelope=record.safe_envelope,
            )
        return None

    def _budget_precheck(
        self,
        session: ToolCallSession,
        *,
        spec: Any,
        risk: ToolRiskDecision,
        budget: ToolCallBudget,
        audit_fields: dict[str, Any],
        request_id: str,
        idempotency_key: str,
        idempotency_key_hash: str,
        args_hash: str,
        execution_mode: str,
    ) -> ToolExecutionDecision | None:
        code: str | None = None
        event = "plugin.tool_budget_exceeded"
        if session.call_count >= budget.max_tool_calls_per_session:
            code = "BUDGET_EXCEEDED"
        elif risk.requires_confirmation and session.high_risk_count >= budget.max_high_risk_tool_calls_per_session:
            code = "BUDGET_EXCEEDED"
        elif session.total_result_bytes >= budget.max_total_result_bytes_per_session:
            code = "BUDGET_EXCEEDED"
        elif _recent_call_count(session) >= budget.max_tool_calls_per_minute:
            code = "RATE_LIMITED"
            event = "plugin.tool_call_storm_detected"
        elif session.denied_count >= budget.max_denied_calls_per_session:
            code = "TOOL_STORM_RATE_LIMITED"
            event = "plugin.tool_call_storm_detected"
        repeated_key = f"{spec.name}:{args_hash}"
        if session.recent_args_hashes.get(repeated_key, 0) >= self.policy.repeated_args_deny_threshold:
            code = "TOOL_LOOP_DETECTED"
            event = "plugin.tool_loop_detected"
        if code is None:
            return None
        self.store.record_denied_call(session)
        self._audit(
            event,
            "error",
            request_id=request_id,
            spec=spec,
            details={**audit_fields, "decision": "deny", "reason": code},
            decision="deny",
            reason=code,
        )
        return _decision(
            allowed=False,
            decision="rate_limited" if code == "RATE_LIMITED" else "deny",
            reason=code.lower(),
            risk=risk,
            budget=budget,
            session=session,
            idempotency_key=idempotency_key,
            idempotency_key_hash=idempotency_key_hash,
            args_hash=args_hash,
            execution_mode=execution_mode,
            audit_fields=audit_fields,
            error_code=code,
        )

    def _confirmation_precheck(
        self,
        *,
        spec: Any,
        risk: ToolRiskDecision,
        actor_role: str,
        conversation_id: str | None,
        confirmation_token: str | None,
        budget: ToolCallBudget,
        session: ToolCallSession,
        audit_fields: dict[str, Any],
        request_id: str,
        idempotency_key: str,
        idempotency_key_hash: str,
        args_hash: str,
        execution_mode: str,
    ) -> ToolExecutionDecision | None:
        if not self.policy.requires_confirmation(actor_role, risk):
            return None
        if not confirmation_token:
            new_requirement = self.confirmation_provider.create_confirmation(
                ConfirmationRequest(
                    actor_role=actor_role,
                    conversation_id=conversation_id,
                    model_tool_name=spec.name,
                    args_hash=args_hash,
                    required_permissions=risk.required_permissions,
                    risk_level=risk.risk_level,
                    ttl_seconds=self.policy.confirmation_ttl_seconds,
                )
            )
            self.store.record_denied_call(session)
            self._audit(
                "plugin.tool_confirmation_required",
                "error",
                request_id=request_id,
                spec=spec,
                details={
                    **audit_fields,
                    "decision": "require_confirmation",
                    "reason": "CONFIRMATION_REQUIRED",
                    "confirmation_token_hash": new_requirement.token_hash,
                    "expires_at": new_requirement.expires_at,
                },
                decision="require_confirmation",
                reason="CONFIRMATION_REQUIRED",
            )
            return _decision(
                allowed=False,
                decision="require_confirmation",
                reason="confirmation_required",
                risk=risk,
                budget=budget,
                session=session,
                idempotency_key=idempotency_key,
                idempotency_key_hash=idempotency_key_hash,
                args_hash=args_hash,
                execution_mode=execution_mode,
                audit_fields=audit_fields,
                error_code="CONFIRMATION_REQUIRED",
                confirmation_token=new_requirement.confirmation_token,
                confirmation=new_requirement.to_safe_dict(),
            )
        confirmation_decision = self.confirmation_provider.verify_confirmation(
            confirmation_token,
            actor_role=actor_role,
            conversation_id=conversation_id,
            model_tool_name=spec.name,
            args_hash=args_hash,
            required_permissions=risk.required_permissions,
        )
        status = confirmation_decision.status
        requirement = confirmation_decision.requirement
        if status == "accepted":
            accepted_requirement = requirement
            self._audit(
                "plugin.tool_confirmation_accepted",
                "success",
                request_id=request_id,
                spec=spec,
                details={
                    **audit_fields,
                    "decision": "allow",
                    "reason": "confirmation_accepted",
                    "confirmation_token_hash": accepted_requirement.token_hash if accepted_requirement else None,
                },
                decision="allow",
                reason="confirmation_accepted",
            )
            return None
        code = "CONFIRMATION_EXPIRED" if status == "expired" else "CONFIRMATION_INVALID"
        event = "plugin.tool_confirmation_expired" if status == "expired" else "plugin.tool_confirmation_denied"
        self.store.record_denied_call(session)
        self._audit(
            event,
            "error",
            request_id=request_id,
            spec=spec,
            details={
                **audit_fields,
                "decision": "deny",
                "reason": code,
                "confirmation_token_hash": _hash_text(confirmation_token),
            },
            decision="deny",
            reason=code,
        )
        return _decision(
            allowed=False,
            decision="deny",
            reason=code.lower(),
            risk=risk,
            budget=budget,
            session=session,
            idempotency_key=idempotency_key,
            idempotency_key_hash=idempotency_key_hash,
            args_hash=args_hash,
            execution_mode=execution_mode,
            audit_fields=audit_fields,
            error_code=code,
        )

    def _audit(
        self,
        event: str,
        result: str,
        *,
        request_id: str,
        spec: Any,
        details: dict[str, Any],
        decision: str,
        reason: str,
    ) -> None:
        self.audit_logger.record(
            event,
            result,
            request_id=request_id,
            plugin=spec.plugin_id,
            action=spec.tool_name,
            details={key: value for key, value in details.items() if value is not None},
            plugin_id=spec.plugin_id,
            plugin_version=spec.plugin_version,
            decision=decision,
            reason=reason,
        )


def normalize_execution_mode(value: str | None) -> str:
    mode = str(value or ToolExecutionMode.EXECUTE)
    if mode not in {
        ToolExecutionMode.EXECUTE,
        ToolExecutionMode.DRY_RUN,
        ToolExecutionMode.PREVIEW_ONLY,
        ToolExecutionMode.CONFIRMATION_ONLY,
    }:
        raise ValueError(f"unsupported tool execution mode: {value}")
    return mode


def governance_store_metadata(store: ToolCallSessionStore | None) -> dict[str, Any]:
    if store is None:
        return GovernanceStoreMetadata(
            store_kind="missing",
            persistent=False,
            multi_process_safe=False,
            multi_instance_safe=False,
            production_recommended=False,
            description="No governance store configured.",
        ).to_dict()
    metadata = getattr(store, "metadata", None)
    if callable(metadata):
        value = metadata()
        if isinstance(value, GovernanceStoreMetadata):
            return value.to_dict()
        if isinstance(value, dict):
            return dict(value)
    return GovernanceStoreMetadata(
        store_kind=store.__class__.__name__,
        persistent=False,
        multi_process_safe=False,
        multi_instance_safe=False,
        production_recommended=False,
        description="Governance store does not expose safety metadata.",
    ).to_dict()


def tool_risk_decision(spec: Any) -> ToolRiskDecision:
    permissions = sorted(str(item) for item in getattr(spec, "required_permissions", []) or [])
    high_risk_permissions = sorted(set(permissions) & HIGH_RISK_CONFIRMATION_PERMISSIONS)
    side_effects = sorted(set(permissions) & SIDE_EFFECT_PERMISSIONS)
    return ToolRiskDecision(
        risk_level=str(getattr(spec, "risk_level", "low") or "low"),
        required_permissions=permissions,
        side_effecting=bool(side_effects),
        requires_confirmation=bool(high_risk_permissions),
        expected_side_effects=side_effects,
    )


def tool_preview(
    spec: Any,
    *,
    args: dict[str, Any],
    risk: ToolRiskDecision,
    execution_mode: str,
) -> dict[str, Any]:
    return {
        "execution_mode": execution_mode,
        "plugin_id": spec.plugin_id,
        "plugin_version": spec.plugin_version,
        "tool_name": spec.tool_name,
        "model_tool_name": spec.name,
        "required_permissions": risk.required_permissions,
        "expected_side_effects": risk.expected_side_effects,
        "risk_level": risk.risk_level,
        "side_effecting": risk.side_effecting,
        "args_summary": summarize_json(args),
    }


def derive_idempotency_key(
    *,
    conversation_id: str | None,
    provider_call_id: str | None,
    model_tool_name: str,
    args_hash: str,
) -> str:
    material = {
        "conversation_id": conversation_id or "",
        "provider_call_id": provider_call_id or "",
        "model_tool_name": model_tool_name,
        "args_hash": args_hash,
    }
    return stable_json_hash(material)


def stable_json_hash(value: Any) -> str:
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        payload = repr(type(value).__name__)
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def summarize_json(value: Any) -> dict[str, Any]:
    try:
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        size = None
    if isinstance(value, dict):
        return {
            "keys": sorted(str(key) for key in value),
            "types": {str(key): type(item).__name__ for key, item in value.items()},
            "size_bytes": size,
        }
    return {"type": type(value).__name__, "size_bytes": size}


def safe_governance_error_message(code: str) -> str:
    info = tool_error_info(code)
    return info.safe_message if info.code != "INTERNAL_ERROR" else GOVERNANCE_ERROR_MESSAGES.get(
        code,
        "Tool call was blocked by governance policy.",
    )


def governance_failure_envelope(
    *,
    request_id: str,
    code: str,
    model_tool_name: str,
    message: str | None = None,
    confirmation: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error_info = tool_error_info(code)
    envelope: dict[str, Any] = {
        "ok": False,
        "request_id": request_id,
        "plugin_id": None,
        "plugin_version": None,
        "tool_name": None,
        "model_tool_name": model_tool_name,
        "error": {
            "code": error_info.code,
            "message": message or error_info.safe_message,
            "retryable": error_info.retryable,
            "category": error_info.category,
        },
        "metadata": metadata or {},
    }
    if confirmation is not None:
        envelope["confirmation"] = confirmation
    return envelope


def governance_preview_envelope(
    *,
    request_id: str,
    preview: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": True,
        "request_id": request_id,
        "plugin_id": preview.get("plugin_id"),
        "plugin_version": preview.get("plugin_version"),
        "tool_name": preview.get("tool_name"),
        "model_tool_name": preview.get("model_tool_name"),
        "result": {
            "governance_preview": True,
            **preview,
        },
        "metadata": metadata,
    }


def _dataclass_mapping(raw: Any, cls: Any, *, key_field: str) -> dict[str, Any]:
    if not isinstance(raw, list):
        return {}
    field_names = {item.name for item in dataclass_fields(cls)}
    result: dict[str, Any] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        payload = {key: value for key, value in item.items() if key in field_names}
        try:
            record = cls(**payload)
        except TypeError:
            continue
        key = getattr(record, key_field, None)
        if isinstance(key, str) and key:
            result[key] = record
    return result


def _safe_confirmation_record(requirement: ConfirmationRequirement) -> dict[str, Any]:
    payload = asdict(requirement)
    payload["confirmation_token"] = None
    return payload


def _decision(
    *,
    allowed: bool,
    decision: str,
    reason: str,
    risk: ToolRiskDecision,
    budget: ToolCallBudget,
    session: ToolCallSession,
    idempotency_key: str,
    idempotency_key_hash: str,
    args_hash: str,
    execution_mode: str,
    audit_fields: dict[str, Any],
    requires_confirmation: bool | None = None,
    confirmation_token: str | None = None,
    confirmation: dict[str, Any] | None = None,
    error_code: str | None = None,
    idempotency_status: str | None = None,
    safe_envelope: dict[str, Any] | None = None,
    preview: dict[str, Any] | None = None,
) -> ToolExecutionDecision:
    remaining = session.to_safe_dict(budget)["remaining_budget"]
    return ToolExecutionDecision(
        allowed=allowed,
        decision=decision,
        reason=reason,
        risk_level=risk.risk_level,
        requires_confirmation=risk.requires_confirmation if requires_confirmation is None else requires_confirmation,
        confirmation_token=confirmation_token,
        confirmation=confirmation,
        remaining_budget=remaining,
        idempotency_status=idempotency_status,
        idempotency_key=idempotency_key,
        idempotency_key_hash=idempotency_key_hash,
        args_hash=args_hash,
        execution_mode=execution_mode,
        audit_fields=audit_fields,
        safe_envelope=safe_envelope,
        error_code=error_code,
        preview=preview,
    )


def _recent_call_count(session: ToolCallSession) -> int:
    now = datetime.now(UTC).timestamp()
    session.call_timestamps = [timestamp for timestamp in session.call_timestamps if now - timestamp <= 60]
    return len(session.call_timestamps)


def _envelope_result_size(envelope: dict[str, Any]) -> int:
    metadata = envelope.get("metadata")
    if isinstance(metadata, dict) and metadata.get("result_size_bytes") is not None:
        try:
            return max(0, int(metadata["result_size_bytes"]))
        except (TypeError, ValueError):
            pass
    result = envelope.get("result")
    try:
        return len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def _hash_text(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def run_governance_selftest() -> dict[str, Any]:
    class MinimalSpec:
        name = "selftest.network"
        plugin_id = "selftest"
        plugin_version = "1.0.0"
        tool_name = "network"
        risk_level = "high"
        required_permissions = [PermissionName.COMPUTE.value, PermissionName.NETWORK_OUTBOUND.value]

    audit_logger = NullAuditLogger()
    policy = ToolGovernancePolicy(
        expert_budget=ToolCallBudget(
            max_tool_calls_per_session=2,
            max_high_risk_tool_calls_per_session=1,
            max_tool_calls_per_minute=2,
            max_total_result_bytes_per_session=4096,
        )
    )
    store = ToolCallSessionStore()
    controller = ToolGovernanceController(policy=policy, store=store, audit_logger=audit_logger)
    memory_metadata = governance_store_metadata(store)
    confirmation_metadata = controller.confirmation_provider.metadata()
    spec = MinimalSpec()
    first = controller.precheck(
        spec=spec,
        args={"url": "https://example.com"},
        provider="generic",
        provider_call_id="call-1",
        provider_tool_name="selftest_network",
        actor_role="expert",
        conversation_id="gov-selftest",
        request_id=new_request_id(),
        execution_mode=ToolExecutionMode.EXECUTE,
        confirmation_token=None,
        idempotency_key="gov-key",
    )
    accepted = controller.precheck(
        spec=spec,
        args={"url": "https://example.com"},
        provider="generic",
        provider_call_id="call-1",
        provider_tool_name="selftest_network",
        actor_role="expert",
        conversation_id="gov-selftest",
        request_id=new_request_id(),
        execution_mode=ToolExecutionMode.EXECUTE,
        confirmation_token=first.confirmation_token,
        idempotency_key="gov-key",
    )
    preview = controller.precheck(
        spec=spec,
        args={"url": "https://example.com"},
        provider="generic",
        provider_call_id="call-2",
        provider_tool_name="selftest_network",
        actor_role="expert",
        conversation_id="gov-selftest-preview",
        request_id=new_request_id(),
        execution_mode=ToolExecutionMode.DRY_RUN,
        confirmation_token=None,
        idempotency_key=None,
    )
    confirmation_only = controller.precheck(
        spec=spec,
        args={"url": "https://example.com"},
        provider="generic",
        provider_call_id="call-confirm-only",
        provider_tool_name="selftest_network",
        actor_role="expert",
        conversation_id="gov-selftest-confirm-only",
        request_id=new_request_id(),
        execution_mode=ToolExecutionMode.CONFIRMATION_ONLY,
        confirmation_token=None,
        idempotency_key="gov-confirm-only-key",
    )
    with tempfile.TemporaryDirectory(prefix="plugin-governance-store-") as temp_dir:
        store_path = Path(temp_dir) / "governance.json"
        file_store = FileToolCallSessionStore(store_path)
        file_metadata = governance_store_metadata(file_store)
        file_controller = ToolGovernanceController(policy=policy, store=file_store, audit_logger=audit_logger)
        persisted_first = file_controller.precheck(
            spec=spec,
            args={"url": "https://example.com"},
            provider="generic",
            provider_call_id="persist-1",
            provider_tool_name="selftest_network",
            actor_role="expert",
            conversation_id="gov-selftest-persist",
            request_id=new_request_id(),
            execution_mode=ToolExecutionMode.CONFIRMATION_ONLY,
            confirmation_token=None,
            idempotency_key="persist-key",
        )
        persisted_text = store_path.read_text(encoding="utf-8")
        reopened_controller = ToolGovernanceController(
            policy=policy,
            store=FileToolCallSessionStore(store_path),
            audit_logger=audit_logger,
        )
        persisted_accepted = reopened_controller.precheck(
            spec=spec,
            args={"url": "https://example.com"},
            provider="generic",
            provider_call_id="persist-1",
            provider_tool_name="selftest_network",
            actor_role="expert",
            conversation_id="gov-selftest-persist",
            request_id=new_request_id(),
            execution_mode=ToolExecutionMode.EXECUTE,
            confirmation_token=persisted_first.confirmation_token,
            idempotency_key="persist-key",
        )
    checks = {
        "memory_store_metadata": memory_metadata.get("store_kind") == "memory"
        and memory_metadata.get("persistent") is False
        and memory_metadata.get("production_recommended") is False,
        "confirmation_provider_metadata": confirmation_metadata.get("provider_kind") == "local"
        and confirmation_metadata.get("production_recommended") is False
        and confirmation_metadata.get("stores_raw_args") is False,
        "confirmation_required": first.error_code == "CONFIRMATION_REQUIRED",
        "confirmation_token_present": bool(first.confirmation_token),
        "confirmation_accepted": accepted.allowed is True,
        "dry_run_not_allowed_to_execute": preview.allowed is False and preview.preview is not None,
        "confirmation_only_returns_token": confirmation_only.error_code == "CONFIRMATION_REQUIRED"
        and bool(confirmation_only.confirmation_token),
        "file_store_persists_confirmation": persisted_accepted.allowed is True,
        "file_store_metadata": file_metadata.get("store_kind") == "local_file"
        and file_metadata.get("persistent") is True
        and file_metadata.get("production_recommended") is False,
        "file_store_omits_plain_token": bool(persisted_first.confirmation_token)
        and (persisted_first.confirmation_token or "") not in persisted_text,
    }
    failed = sorted(name for name, ok in checks.items() if not ok)
    return {
        "status": "success" if not failed else "error",
        "checks": checks,
        "failed_checks": failed,
        "store": "in_memory_or_local_file",
        "memory_store_metadata": memory_metadata,
        "file_store_metadata": file_metadata,
        "confirmation_provider": confirmation_metadata,
        "generated_at": _utc_now(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run plugin tool governance selftest")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    if not args.selftest:
        parser.print_help()
        return 2
    report = run_governance_selftest()
    if args.json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"tool governance selftest status={report['status']}")
    return 0 if report.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
