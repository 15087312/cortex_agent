from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


TOOL_SERVICE_CONTRACT_VERSION = "2026-05-rc1"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ToolListResponse:
    ok: bool
    provider: str
    actor_role: str
    tools: list[dict[str, Any]]
    name_mapping: dict[str, Any]
    warnings: list[dict[str, Any]]
    hidden_count: int
    exported_count: int
    generated_at: str = field(default_factory=utc_now)
    request_id: str | None = None
    contract_version: str = TOOL_SERVICE_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolInvocationResponse:
    ok: bool
    provider: str
    provider_call_id: str | None
    request_id: str
    conversation_id: str | None
    model_tool_name: str | None
    plugin_id: str | None
    plugin_version: str | None
    tool_name: str | None
    response: dict[str, Any]
    envelope: dict[str, Any]
    error: dict[str, Any] | None
    audit_summary: dict[str, Any]
    generated_at: str = field(default_factory=utc_now)
    contract_version: str = TOOL_SERVICE_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolServiceHealth:
    ok: bool
    production_mode: bool
    engine_available: bool
    catalog_available: bool
    provider_bridge_available: bool
    audit_available: bool
    governance_available: bool
    ready_for_model_calls: bool = False
    ready_for_production: bool = False
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    degraded_capabilities: list[str] = field(default_factory=list)
    governance_store: dict[str, Any] = field(default_factory=dict)
    confirmation_provider: dict[str, Any] = field(default_factory=dict)
    generated_at: str = field(default_factory=utc_now)
    contract_version: str = TOOL_SERVICE_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolServiceCapabilities:
    providers: list[str]
    schema_validation: bool
    returns_validation: bool
    per_tool_permissions: bool
    governance: bool
    sandbox: dict[str, Any]
    audit: bool
    legacy_compatibility: bool
    production_mode: bool
    governance_store: dict[str, Any] = field(default_factory=dict)
    confirmation_provider: dict[str, Any] = field(default_factory=dict)
    contract_version: str = TOOL_SERVICE_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RequestAuditSummary:
    request_id: str
    plugin_id: str | None = None
    tool_name: str | None = None
    decision: str | None = None
    error_code: str | None = None
    sanitized: bool = False
    truncated: bool = False
    permission_denied: bool = False
    schema_violation: bool = False
    governance_decision: str | None = None
    duration_ms: int | None = None
    audit_event_count: int = 0
    contract_version: str = TOOL_SERVICE_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolTraceContext:
    request_id: str
    conversation_id: str | None
    actor_role: str
    provider: str
    provider_call_id: str | None = None
    model_tool_name: str | None = None
    plugin_id: str | None = None
    tool_name: str | None = None
    started_at: str = field(default_factory=utc_now)
    ended_at: str | None = None
    duration_ms: int | None = None
    decision: str | None = None
    error_code: str | None = None
    contract_version: str = TOOL_SERVICE_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolServiceMetrics:
    tool_exports_total: int = 0
    tool_calls_total: int = 0
    tool_calls_allowed: int = 0
    tool_calls_denied: int = 0
    tool_calls_failed: int = 0
    confirmation_required_total: int = 0
    permission_denied_total: int = 0
    params_schema_error_total: int = 0
    return_schema_error_total: int = 0
    sanitized_total: int = 0
    truncated_total: int = 0
    budget_exceeded_total: int = 0
    rate_limited_total: int = 0
    duplicate_total: int = 0
    provider_counts: dict[str, int] = field(default_factory=dict)
    actor_role_counts: dict[str, int] = field(default_factory=dict)
    per_tool_calls: dict[str, int] = field(default_factory=dict)
    per_tool_failures: dict[str, int] = field(default_factory=dict)
    per_tool_denials: dict[str, int] = field(default_factory=dict)
    per_error_code: dict[str, int] = field(default_factory=dict)
    last_denied_reason_by_tool: dict[str, str] = field(default_factory=dict)
    confirmation_required_by_tool: dict[str, int] = field(default_factory=dict)
    budget_exceeded_by_tool: dict[str, int] = field(default_factory=dict)
    last_error_code: str | None = None
    contract_version: str = TOOL_SERVICE_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
