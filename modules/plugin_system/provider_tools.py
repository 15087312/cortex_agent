from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from .audit import AuditLogger, NullAuditLogger, new_request_id
from .llm_tools import (
    LLMToolCatalog,
    LLMToolRiskLevel,
    LLMToolRuntime,
    LLMToolSpec,
    SCHEMA_MAX_BYTES,
)
from .schema_validation import SchemaDefinitionError, SchemaValidationError, validate_json_value
from .tool_contracts import TOOL_SERVICE_CONTRACT_VERSION
from .tool_errors import safe_tool_error_message, tool_error_info
from .tool_governance import (
    ConfirmationProvider,
    ToolCallSessionStore,
    ToolExecutionMode,
    ToolGovernanceController,
    ToolGovernancePolicy,
    governance_failure_envelope,
    governance_preview_envelope,
    normalize_execution_mode,
)


PROVIDER_NAMES = ("generic", "openai", "anthropic")
PROVIDER_TOOL_NAME_MAX_CHARS = 64
MAX_TOOL_ARGUMENT_BYTES = 64 * 1024
DEFAULT_MAX_TOOLS = 128
DEFAULT_MAX_TOTAL_SCHEMA_BYTES = 256 * 1024
PROVIDER_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
THINKING_FIELD_NAMES = frozenset(
    {
        "thinking",
        "reasoning",
        "chain_of_thought",
        "internal_thoughts",
    }
)
_MISSING = object()


class ProviderName(str):
    GENERIC = "generic"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


@dataclass(frozen=True)
class ProviderToolExportOptions:
    provider: str = ProviderName.GENERIC
    actor_role: str = "model"
    production_mode: bool = True
    include_hidden: bool = False
    include_returns_summary: bool = False
    max_tools: int = DEFAULT_MAX_TOOLS
    max_description_chars: int = 512
    max_schema_bytes_per_tool: int = SCHEMA_MAX_BYTES
    max_total_schema_bytes: int = DEFAULT_MAX_TOTAL_SCHEMA_BYTES


@dataclass(frozen=True)
class ToolNameMapping:
    provider: str
    provider_tool_name: str
    model_tool_name: str
    plugin_id: str
    plugin_version: str
    tool_name: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderToolSpec:
    provider: str
    provider_tool_name: str
    model_tool_name: str
    definition: dict[str, Any]
    mapping: ToolNameMapping
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "provider_tool_name": self.provider_tool_name,
            "model_tool_name": self.model_tool_name,
            "definition": self.definition,
            "mapping": self.mapping.to_dict(),
            "warnings": self.warnings,
        }


@dataclass(frozen=True)
class ModelToolCall:
    provider: str
    provider_call_id: str | None
    provider_tool_name: str
    model_tool_name: str
    args: dict[str, Any]
    raw_args_size_bytes: int
    request_id: str
    parse_warnings: list[str] = field(default_factory=list)
    thinking_fields_ignored: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderToolResponse:
    provider: str
    provider_call_id: str | None
    ok: bool
    safe_content: dict[str, Any]
    envelope: dict[str, Any]
    error_code: str | None
    request_id: str
    audit_summary: dict[str, Any]
    message: dict[str, Any]
    contract_version: str = TOOL_SERVICE_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProviderToolCallError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        provider: str,
        request_id: str,
        provider_call_id: str | None = None,
        provider_tool_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.provider = provider
        self.request_id = request_id
        self.provider_call_id = provider_call_id
        self.provider_tool_name = provider_tool_name


class ModelToolBridge:
    """Provider-agnostic bridge that always delegates execution to LLMToolRuntime."""

    def __init__(
        self,
        engine: Any,
        *,
        audit_logger: AuditLogger | NullAuditLogger | None = None,
        governance_controller: ToolGovernanceController | None = None,
        governance_policy: ToolGovernancePolicy | None = None,
        governance_store: ToolCallSessionStore | None = None,
        confirmation_provider: ConfirmationProvider | None = None,
    ) -> None:
        self.engine = engine
        self.audit_logger: AuditLogger | NullAuditLogger = (
            audit_logger or getattr(engine, "audit_logger", None) or NullAuditLogger()
        )
        self.runtime = LLMToolRuntime(engine, audit_logger=self.audit_logger)
        self.governance = governance_controller or ToolGovernanceController(
            policy=governance_policy,
            store=governance_store,
            confirmation_provider=confirmation_provider,
            audit_logger=self.audit_logger,
        )

    def invoke_provider_tool_call(
        self,
        provider: str,
        payload: dict[str, Any],
        *,
        actor_role: str,
        conversation_id: str | None = None,
        production_mode: bool = True,
        options: ProviderToolExportOptions | None = None,
        execution_mode: str = ToolExecutionMode.EXECUTE,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        provider = normalize_provider(provider)
        execution_mode = normalize_execution_mode(execution_mode)
        request_id = request_id or new_request_id()
        options = options or ProviderToolExportOptions(
            provider=provider,
            actor_role=actor_role,
            production_mode=production_mode,
        )
        catalog = LLMToolCatalog.from_engine(
            self.engine,
            actor_role=actor_role,
            production_mode=production_mode,
            approved_only=True,
            include_hidden=True,
            request_id=request_id,
            audit_logger=self.audit_logger,
        )
        export = export_provider_tools(
            catalog,
            options=options,
            audit_logger=self.audit_logger,
            request_id=request_id,
        )
        mapping = tool_name_mapping_from_export(export)
        try:
            tool_call = parse_model_tool_call(
                provider,
                payload,
                name_mapping=mapping,
                request_id=request_id,
            )
        except ProviderToolCallError as exc:
            self._audit_rejected(
                exc,
                actor_role=actor_role,
                decision="deny",
                reason=exc.code,
            )
            envelope = provider_failure_envelope(
                request_id=exc.request_id,
                code=exc.code,
                message=safe_tool_error_message(exc.code),
                model_tool_name=exc.provider_tool_name,
            )
            response = create_provider_tool_response(
                provider,
                provider_call_id=exc.provider_call_id or _provider_call_id(provider, payload),
                provider_tool_name=exc.provider_tool_name,
                envelope=envelope,
                audit_logger=self.audit_logger,
            )
            return response.to_dict()
        except Exception as exc:
            self.audit_logger.record(
                "plugin.provider_tool_call_rejected",
                "error",
                request_id=request_id,
                action="provider_tool_call",
                details={
                    "request_id": request_id,
                    "provider": provider,
                    "provider_call_id": _provider_call_id(provider, payload) if isinstance(payload, dict) else None,
                    "actor_role": actor_role,
                    "decision": "deny",
                    "reason": "INTERNAL_ERROR",
                    "error_code": "INTERNAL_ERROR",
                    "exception_category": type(exc).__name__,
                },
                decision="deny",
                reason="INTERNAL_ERROR",
            )
            envelope = provider_failure_envelope(
                request_id=request_id,
                code="INTERNAL_ERROR",
                message=safe_tool_error_message("INTERNAL_ERROR"),
                model_tool_name=None,
            )
            response = create_provider_tool_response(
                provider,
                provider_call_id=_provider_call_id(provider, payload) if isinstance(payload, dict) else None,
                provider_tool_name=None,
                envelope=envelope,
                audit_logger=self.audit_logger,
            )
            return response.to_dict()
        self.audit_logger.record(
            "plugin.provider_tool_call_parsed",
            "success",
            request_id=tool_call.request_id,
            action="provider_tool_call",
            details={
                "request_id": tool_call.request_id,
                "provider": provider,
                "provider_call_id": tool_call.provider_call_id,
                "provider_tool_name": tool_call.provider_tool_name,
                "model_tool_name": tool_call.model_tool_name,
                "actor_role": actor_role,
                "raw_args_size_bytes": tool_call.raw_args_size_bytes,
                "parse_warnings": tool_call.parse_warnings,
                "thinking_fields_ignored": tool_call.thinking_fields_ignored,
                "decision": "allow",
            },
            decision="allow",
            reason="parsed",
        )
        spec = catalog.get(tool_call.model_tool_name)
        if spec is None or spec.hidden:
            envelope = provider_failure_envelope(
                request_id=tool_call.request_id,
                code="TOOL_NOT_VISIBLE",
                message=safe_tool_error_message("TOOL_NOT_VISIBLE"),
                model_tool_name=tool_call.model_tool_name,
            )
            response = create_provider_tool_response(
                provider,
                provider_call_id=tool_call.provider_call_id,
                provider_tool_name=tool_call.provider_tool_name,
                envelope=envelope,
                audit_logger=self.audit_logger,
            )
            return response.to_dict()
        params_failure = _provider_params_failure_envelope(
            spec,
            tool_call.args,
            request_id=tool_call.request_id,
            actor_role=actor_role,
            audit_logger=self.audit_logger,
        )
        if params_failure is not None:
            response = create_provider_tool_response(
                provider,
                provider_call_id=tool_call.provider_call_id,
                provider_tool_name=tool_call.provider_tool_name,
                envelope=params_failure,
                audit_logger=self.audit_logger,
            )
            return response.to_dict()
        governance_decision = self.governance.precheck(
            spec=spec,
            args=tool_call.args,
            provider=provider,
            provider_call_id=tool_call.provider_call_id,
            provider_tool_name=tool_call.provider_tool_name,
            actor_role=actor_role,
            conversation_id=conversation_id,
            request_id=tool_call.request_id,
            execution_mode=execution_mode,
            confirmation_token=confirmation_token or _provider_confirmation_token(payload),
            idempotency_key=_provider_idempotency_key(payload) or idempotency_key,
        )
        if governance_decision.safe_envelope is not None:
            envelope = {
                **governance_decision.safe_envelope,
                "request_id": tool_call.request_id,
                "metadata": {
                    **(governance_decision.safe_envelope.get("metadata") or {}),
                    "governance": _governance_metadata(governance_decision),
                },
            }
            response = create_provider_tool_response(
                provider,
                provider_call_id=tool_call.provider_call_id,
                provider_tool_name=tool_call.provider_tool_name,
                envelope=envelope,
                audit_logger=self.audit_logger,
            )
            return response.to_dict()
        if governance_decision.preview is not None:
            envelope = governance_preview_envelope(
                request_id=tool_call.request_id,
                preview=governance_decision.preview,
                metadata={"governance": _governance_metadata(governance_decision)},
            )
            response = create_provider_tool_response(
                provider,
                provider_call_id=tool_call.provider_call_id,
                provider_tool_name=tool_call.provider_tool_name,
                envelope=envelope,
                audit_logger=self.audit_logger,
            )
            return response.to_dict()
        if not governance_decision.allowed:
            code = governance_decision.error_code or "INTERNAL_ERROR"
            envelope = governance_failure_envelope(
                request_id=tool_call.request_id,
                code=code,
                message=safe_tool_error_message(code),
                model_tool_name=tool_call.model_tool_name,
                confirmation=governance_decision.confirmation,
                metadata={"governance": _governance_metadata(governance_decision)},
            )
            response = create_provider_tool_response(
                provider,
                provider_call_id=tool_call.provider_call_id,
                provider_tool_name=tool_call.provider_tool_name,
                envelope=envelope,
                audit_logger=self.audit_logger,
            )
            return response.to_dict()
        envelope = self.runtime.invoke(
            tool_call.model_tool_name,
            tool_call.args,
            actor_role=actor_role,
            request_id=tool_call.request_id,
            conversation_id=conversation_id,
            production_mode=production_mode,
            context={
                "provider": provider,
                "provider_call_id": tool_call.provider_call_id,
                "provider_tool_name": tool_call.provider_tool_name,
                "governance_decision": governance_decision.decision,
                "idempotency_key_hash": governance_decision.idempotency_key_hash,
            },
        )
        self.governance.record_result(
            governance_decision,
            spec=spec,
            actor_role=actor_role,
            conversation_id=conversation_id,
            request_id=tool_call.request_id,
            envelope=envelope,
        )
        raw_envelope_metadata = envelope.get("metadata")
        envelope_metadata: dict[str, Any] = (
            raw_envelope_metadata if isinstance(raw_envelope_metadata, dict) else {}
        )
        envelope = {
            **envelope,
            "metadata": {
                **envelope_metadata,
                "governance": _governance_metadata(governance_decision),
            },
        }
        response = create_provider_tool_response(
            provider,
            provider_call_id=tool_call.provider_call_id,
            provider_tool_name=tool_call.provider_tool_name,
            envelope=envelope,
            audit_logger=self.audit_logger,
        )
        return response.to_dict()

    def _audit_rejected(
        self,
        exc: ProviderToolCallError,
        *,
        actor_role: str,
        decision: str,
        reason: str,
    ) -> None:
        self.audit_logger.record(
            "plugin.provider_tool_call_rejected",
            "error",
            request_id=exc.request_id,
            action="provider_tool_call",
            details={
                "request_id": exc.request_id,
                "provider": exc.provider,
                "provider_call_id": exc.provider_call_id,
                "provider_tool_name": exc.provider_tool_name,
                "actor_role": actor_role,
                "decision": decision,
                "reason": reason,
                "error_code": exc.code,
            },
            decision=decision,
            reason=reason,
        )


def export_provider_tools(
    catalog: LLMToolCatalog,
    *,
    options: ProviderToolExportOptions | None = None,
    audit_logger: AuditLogger | NullAuditLogger | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    options = options or ProviderToolExportOptions()
    provider = normalize_provider(options.provider)
    name_result = build_tool_name_mapping(catalog, provider=provider, include_hidden=options.include_hidden)
    warnings: list[dict[str, Any]] = list(name_result["warnings"])
    specs_by_name = {spec.name: spec for spec in catalog.specs}
    exported: list[ProviderToolSpec] = []
    skipped_count = 0
    total_schema_bytes = 0
    sorted_mappings = sorted(
        name_result["mapping"].values(),
        key=lambda item: (_risk_sort_key(specs_by_name[item.model_tool_name]), item.model_tool_name),
    )
    for mapping in sorted_mappings:
        spec = specs_by_name[mapping.model_tool_name]
        if spec.hidden and not options.include_hidden:
            skipped_count += 1
            continue
        if len(exported) >= max(0, options.max_tools):
            skipped_count += 1
            warnings.append(
                _warning(
                    "max_tools_exceeded",
                    f"{mapping.model_tool_name}: export skipped because max_tools was reached",
                    provider=provider,
                    model_tool_name=mapping.model_tool_name,
                )
            )
            continue
        schema_size = _json_size(spec.parameters_schema)
        if schema_size > options.max_schema_bytes_per_tool:
            skipped_count += 1
            warnings.append(
                _warning(
                    "schema_too_large",
                    f"{mapping.model_tool_name}: parameters schema exceeds provider export budget",
                    provider=provider,
                    model_tool_name=mapping.model_tool_name,
                )
            )
            continue
        if total_schema_bytes + schema_size > options.max_total_schema_bytes:
            skipped_count += 1
            warnings.append(
                _warning(
                    "total_schema_budget_exceeded",
                    f"{mapping.model_tool_name}: export skipped because total schema budget was reached",
                    provider=provider,
                    model_tool_name=mapping.model_tool_name,
                )
            )
            continue
        definition, definition_warnings = provider_tool_definition(
            spec,
            mapping,
            provider=provider,
            options=options,
        )
        warnings.extend(definition_warnings)
        exported.append(
            ProviderToolSpec(
                provider=provider,
                provider_tool_name=mapping.provider_tool_name,
                model_tool_name=mapping.model_tool_name,
                definition=definition,
                mapping=mapping,
                warnings=[item["code"] for item in definition_warnings],
            )
        )
        total_schema_bytes += schema_size
    exported_mapping = {item.provider_tool_name: item.mapping.to_dict() for item in exported}
    suspicious_count = sum(
        1
        for spec in catalog.specs
        if "description_prompt_injection_risk" in spec.warnings
    )
    payload = {
        "status": "success",
        "provider": provider,
        "actor_role": options.actor_role,
        "production_mode": options.production_mode,
        "request_id": request_id,
        "contract_version": TOOL_SERVICE_CONTRACT_VERSION,
        "tools": [item.definition for item in exported],
        "name_mapping": exported_mapping,
        "warnings": warnings,
        "metadata": {
            "contract_version": TOOL_SERVICE_CONTRACT_VERSION,
            "provider": provider,
            "tool_count": len(exported),
        },
        "generated_at": datetime.now(UTC).isoformat(),
    }
    _audit_provider_exported(
        audit_logger,
        request_id=request_id,
        provider=provider,
        actor_role=options.actor_role,
        original_count=len(catalog.specs),
        exported_count=len(exported),
        hidden_count=max(0, len(catalog.specs) - len(exported)),
        suspicious_description_count=suspicious_count,
        warnings_count=len(warnings),
    )
    if skipped_count or len(exported) < len(catalog.specs):
        _audit_provider_export_limited(
            audit_logger,
            request_id=request_id,
            provider=provider,
            actor_role=options.actor_role,
            original_count=len(catalog.specs),
            exported_count=len(exported),
            hidden_count=max(0, len(catalog.specs) - len(exported)),
            reason="budget_or_visibility",
        )
    return payload


def build_tool_name_mapping(
    catalog: LLMToolCatalog,
    *,
    provider: str,
    include_hidden: bool = False,
) -> dict[str, Any]:
    provider = normalize_provider(provider)
    specs = sorted(
        [spec for spec in catalog.specs if include_hidden or not spec.hidden],
        key=lambda item: item.name,
    )
    candidates = {spec.name: provider_tool_name_candidate(spec.name) for spec in specs}
    counts: dict[str, int] = {}
    for candidate in candidates.values():
        counts[candidate] = counts.get(candidate, 0) + 1
    mapping: dict[str, ToolNameMapping] = {}
    warnings: list[dict[str, Any]] = []
    for spec in specs:
        candidate = candidates[spec.name]
        if counts[candidate] > 1:
            candidate = provider_tool_name_with_hash(candidate, spec.name)
            warnings.append(
                _warning(
                    "provider_name_collision_resolved",
                    f"{spec.name}: provider tool name collision resolved with stable hash suffix",
                    provider=provider,
                    model_tool_name=spec.name,
                )
            )
        if candidate in mapping:
            warnings.append(
                _warning(
                    "provider_name_unresolvable",
                    f"{spec.name}: provider tool name collision could not be resolved",
                    provider=provider,
                    model_tool_name=spec.name,
                )
            )
            continue
        if not provider_tool_name_valid(candidate):
            warnings.append(
                _warning(
                    "provider_name_invalid",
                    f"{spec.name}: provider tool name is invalid",
                    provider=provider,
                    model_tool_name=spec.name,
                )
            )
            continue
        mapping[candidate] = ToolNameMapping(
            provider=provider,
            provider_tool_name=candidate,
            model_tool_name=spec.name,
            plugin_id=spec.plugin_id,
            plugin_version=spec.plugin_version,
            tool_name=spec.tool_name,
        )
    return {"mapping": mapping, "warnings": warnings}


def provider_tool_definition(
    spec: LLMToolSpec,
    mapping: ToolNameMapping,
    *,
    provider: str,
    options: ProviderToolExportOptions,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    description, warnings = _provider_description(spec, provider=provider, options=options)
    if provider == ProviderName.OPENAI:
        return (
            {
                "type": "function",
                "function": {
                    "name": mapping.provider_tool_name,
                    "description": description,
                    "parameters": spec.parameters_schema,
                },
            },
            warnings,
        )
    if provider == ProviderName.ANTHROPIC:
        return (
            {
                "name": mapping.provider_tool_name,
                "description": description,
                "input_schema": spec.parameters_schema,
            },
            warnings,
        )
    metadata: dict[str, Any] = {
        "contract_version": TOOL_SERVICE_CONTRACT_VERSION,
        "plugin_id": spec.plugin_id,
        "tool_name": spec.tool_name,
        "risk_level": spec.risk_level,
        "exposure": spec.exposure,
        "required_permissions": spec.required_permissions,
    }
    if options.include_returns_summary and spec.returns_schema_summary is not None:
        metadata["returns_schema_summary"] = spec.returns_schema_summary
    return (
        {
            "name": mapping.provider_tool_name,
            "description": description,
            "input_schema": spec.parameters_schema,
            "metadata": metadata,
        },
        warnings,
    )


def parse_model_tool_call(
    provider: str,
    payload: dict[str, Any],
    *,
    name_mapping: dict[str, ToolNameMapping] | None = None,
    request_id: str | None = None,
    max_args_bytes: int = MAX_TOOL_ARGUMENT_BYTES,
) -> ModelToolCall:
    provider = normalize_provider(provider)
    request_id = request_id or new_request_id()
    call_payloads, thinking_fields = _provider_call_payloads(
        provider,
        payload,
        request_id=request_id,
    )
    if len(call_payloads) > 1:
        raise ProviderToolCallError(
            "MULTIPLE_TOOL_CALLS_UNSUPPORTED",
            "This entry point accepts one tool call; use parse_model_tool_calls for arrays.",
            provider=provider,
            request_id=request_id,
        )
    return _parse_normalized_model_tool_call(
        provider,
        call_payloads[0],
        name_mapping=name_mapping,
        request_id=request_id,
        max_args_bytes=max_args_bytes,
        thinking_fields_ignored=thinking_fields,
    )


def parse_model_tool_calls(
    provider: str,
    payload: dict[str, Any],
    *,
    name_mapping: dict[str, ToolNameMapping] | None = None,
    request_id: str | None = None,
    max_args_bytes: int = MAX_TOOL_ARGUMENT_BYTES,
) -> list[ModelToolCall]:
    provider = normalize_provider(provider)
    request_id = request_id or new_request_id()
    call_payloads, thinking_fields = _provider_call_payloads(
        provider,
        payload,
        request_id=request_id,
    )
    return [
        _parse_normalized_model_tool_call(
            provider,
            item,
            name_mapping=name_mapping,
            request_id=request_id,
            max_args_bytes=max_args_bytes,
            thinking_fields_ignored=thinking_fields,
        )
        for item in call_payloads
    ]


def _parse_normalized_model_tool_call(
    provider: str,
    payload: dict[str, Any],
    *,
    name_mapping: dict[str, ToolNameMapping] | None,
    request_id: str,
    max_args_bytes: int,
    thinking_fields_ignored: list[str],
) -> ModelToolCall:
    if not isinstance(payload, dict):
        raise ProviderToolCallError(
            "TOOL_CALL_PAYLOAD_UNSUPPORTED",
            "Provider tool call payload must be an object.",
            provider=provider,
            request_id=request_id,
        )
    provider_call_id = _provider_call_id(provider, payload)
    provider_tool_name, args, raw_size, warnings = _provider_args(
        provider,
        payload,
        request_id=request_id,
        max_args_bytes=max_args_bytes,
    )
    mapping = None
    if name_mapping is not None:
        mapping = name_mapping.get(provider_tool_name)
        if mapping is None:
            raise ProviderToolCallError(
                "TOOL_NOT_FOUND",
                "Tool is not exported for this caller.",
                provider=provider,
                request_id=request_id,
                provider_call_id=provider_call_id,
                provider_tool_name=provider_tool_name,
            )
    model_tool_name = mapping.model_tool_name if mapping else provider_tool_name
    return ModelToolCall(
        provider=provider,
        provider_call_id=provider_call_id,
        provider_tool_name=provider_tool_name,
        model_tool_name=model_tool_name,
        args=args,
        raw_args_size_bytes=raw_size,
        request_id=request_id,
        parse_warnings=warnings,
        thinking_fields_ignored=thinking_fields_ignored,
    )


def create_provider_tool_response(
    provider: str,
    *,
    provider_call_id: str | None,
    provider_tool_name: str | None,
    envelope: dict[str, Any],
    audit_logger: AuditLogger | NullAuditLogger | None = None,
) -> ProviderToolResponse:
    provider = normalize_provider(provider)
    request_id = str(envelope.get("request_id") or new_request_id())
    ok = bool(envelope.get("ok"))
    raw_error = envelope.get("error")
    error = raw_error if isinstance(raw_error, dict) else {}
    error_code = None if ok else str(error.get("code") or "INTERNAL_ERROR")
    safe_content = {
        "untrusted_tool_result": True,
        "tool_result_may_contain_user_or_plugin_controlled_text": True,
        "contract_version": TOOL_SERVICE_CONTRACT_VERSION,
        "provider": provider,
        "provider_call_id": provider_call_id,
        "provider_tool_name": provider_tool_name,
        "envelope": envelope,
    }
    message = provider_response_message(
        provider,
        provider_call_id=provider_call_id,
        provider_tool_name=provider_tool_name,
        safe_content=safe_content,
        is_error=not ok,
    )
    audit_summary = {
        "provider": provider,
        "provider_call_id": provider_call_id,
        "provider_tool_name": provider_tool_name,
        "request_id": request_id,
        "ok": ok,
        "error_code": error_code,
        "contract_version": TOOL_SERVICE_CONTRACT_VERSION,
    }
    if audit_logger is not None:
        audit_logger.record(
            "plugin.provider_tool_response_created",
            "success" if ok else "error",
            request_id=request_id,
            plugin=envelope.get("plugin_id"),
            action=envelope.get("tool_name") or provider_tool_name,
            details={**audit_summary, "decision": "allow" if ok else "deny"},
            plugin_id=envelope.get("plugin_id"),
            plugin_version=envelope.get("plugin_version"),
            decision="allow" if ok else "deny",
            reason="provider_response_created" if ok else error_code,
        )
    return ProviderToolResponse(
        provider=provider,
        provider_call_id=provider_call_id,
        ok=ok,
        safe_content=safe_content,
        envelope=envelope,
        error_code=error_code,
        request_id=request_id,
        audit_summary=audit_summary,
        message=message,
    )


def provider_response_message(
    provider: str,
    *,
    provider_call_id: str | None,
    provider_tool_name: str | None,
    safe_content: dict[str, Any],
    is_error: bool,
) -> dict[str, Any]:
    content = json.dumps(safe_content, ensure_ascii=False, sort_keys=True)
    if provider == ProviderName.OPENAI:
        return {
            "tool_call_id": provider_call_id,
            "role": "tool",
            "name": provider_tool_name,
            "content": content,
        }
    if provider == ProviderName.ANTHROPIC:
        return {
            "type": "tool_result",
            "tool_use_id": provider_call_id,
            "content": [{"type": "text", "text": content}],
            "is_error": bool(is_error),
        }
    return {
        "request_id": safe_content["envelope"].get("request_id"),
        "tool_name": provider_tool_name,
        "content": safe_content,
    }


def provider_failure_envelope(
    *,
    request_id: str,
    code: str,
    message: str | None = None,
    model_tool_name: str | None = None,
) -> dict[str, Any]:
    error_info = tool_error_info(code)
    return {
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
        "metadata": {},
    }


def _provider_call_payloads(
    provider: str,
    payload: dict[str, Any],
    *,
    request_id: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(payload, dict):
        raise ProviderToolCallError(
            "TOOL_CALL_PAYLOAD_UNSUPPORTED",
            "Provider tool call payload must be an object.",
            provider=provider,
            request_id=request_id,
        )
    thinking_fields = _thinking_fields(payload)
    calls: list[dict[str, Any]] = []
    if provider == ProviderName.OPENAI:
        raw_tool_calls = payload.get("tool_calls")
        if isinstance(raw_tool_calls, list):
            calls = _dict_items(raw_tool_calls)
        elif "function_call" in payload:
            function_call = payload.get("function_call")
            if isinstance(function_call, dict):
                calls = [{**payload, "function": function_call}]
            else:
                calls = []
        elif payload.get("type") == "function_call" and "name" in payload:
            calls = [payload]
        elif isinstance(payload.get("output"), list):
            calls = [
                item
                for item in _dict_items(payload["output"])
                if item.get("type") == "function_call" or "function" in item or "name" in item
            ]
        elif isinstance(payload.get("function"), dict) or "name" in payload:
            calls = [payload]
    elif provider == ProviderName.ANTHROPIC:
        if isinstance(payload.get("content"), list):
            calls = [
                item
                for item in _dict_items(payload["content"])
                if item.get("type") == "tool_use" or "input" in item or "name" in item
            ]
        elif payload.get("type") == "tool_use" or "input" in payload or "name" in payload:
            calls = [payload]
    else:
        if isinstance(payload.get("tool_calls"), list):
            calls = _dict_items(payload["tool_calls"])
        elif isinstance(payload.get("calls"), list):
            calls = _dict_items(payload["calls"])
        else:
            calls = [payload]
    if not calls:
        raise ProviderToolCallError(
            "TOOL_CALL_PAYLOAD_UNSUPPORTED",
            "Provider tool call payload format is not supported.",
            provider=provider,
            request_id=request_id,
        )
    return calls, thinking_fields


def _dict_items(items: list[Any]) -> list[dict[str, Any]]:
    return [item for item in items if isinstance(item, dict)]


def _thinking_fields(payload: dict[str, Any]) -> list[str]:
    found: set[str] = set()

    def walk(value: Any, *, in_arguments: bool = False) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                key_str = str(key)
                if key_str in THINKING_FIELD_NAMES and not in_arguments:
                    found.add(key_str)
                    continue
                walk(item, in_arguments=in_arguments or key_str in {"arguments", "input"})
        elif isinstance(value, list):
            for item in value:
                walk(item, in_arguments=in_arguments)

    walk(payload)
    return sorted(found)


def tool_name_mapping_from_export(export: dict[str, Any]) -> dict[str, ToolNameMapping]:
    result: dict[str, ToolNameMapping] = {}
    raw = export.get("name_mapping") if isinstance(export, dict) else {}
    if not isinstance(raw, dict):
        return result
    for provider_name, item in raw.items():
        if not isinstance(item, dict):
            continue
        result[str(provider_name)] = ToolNameMapping(
            provider=str(item.get("provider") or export.get("provider") or ProviderName.GENERIC),
            provider_tool_name=str(item.get("provider_tool_name") or provider_name),
            model_tool_name=str(item.get("model_tool_name") or ""),
            plugin_id=str(item.get("plugin_id") or ""),
            plugin_version=str(item.get("plugin_version") or ""),
            tool_name=str(item.get("tool_name") or ""),
        )
    return result


def normalize_provider(provider: str) -> str:
    normalized = str(provider or ProviderName.GENERIC).lower()
    if normalized not in PROVIDER_NAMES:
        raise ValueError(f"unsupported provider: {provider}")
    return normalized


def provider_tool_name_candidate(model_tool_name: str, *, max_chars: int = PROVIDER_TOOL_NAME_MAX_CHARS) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", model_tool_name)
    value = re.sub(r"_+", "_", value).strip("_-")
    if not value:
        value = "tool"
    if not re.match(r"^[A-Za-z_]", value):
        value = f"tool_{value}"
    return value[:max_chars]


def provider_tool_name_with_hash(
    candidate: str,
    model_tool_name: str,
    *,
    max_chars: int = PROVIDER_TOOL_NAME_MAX_CHARS,
) -> str:
    suffix = hashlib.sha256(model_tool_name.encode("utf-8")).hexdigest()[:6]
    room = max_chars - len(suffix) - 1
    base = candidate[:max(1, room)].rstrip("_-") or "tool"
    if not re.match(r"^[A-Za-z_]", base):
        base = "tool"
    return f"{base}_{suffix}"[:max_chars]


def provider_tool_name_valid(value: str) -> bool:
    return bool(PROVIDER_TOOL_NAME_PATTERN.match(value))


def _provider_description(
    spec: LLMToolSpec,
    *,
    provider: str,
    options: ProviderToolExportOptions,
) -> tuple[str, list[dict[str, Any]]]:
    description = spec.description or "Plugin tool."
    warnings: list[dict[str, Any]] = []
    if len(description) > options.max_description_chars:
        description = description[: max(0, options.max_description_chars - 15)].rstrip() + "...[truncated]"
        warnings.append(
            _warning(
                "description_truncated",
                f"{spec.name}: description truncated for provider export",
                provider=provider,
                model_tool_name=spec.name,
            )
        )
    return description, warnings


def _provider_args(
    provider: str,
    payload: dict[str, Any],
    *,
    request_id: str,
    max_args_bytes: int,
) -> tuple[str, dict[str, Any], int, list[str]]:
    warnings: list[str] = []
    provider_call_id = _provider_call_id(provider, payload)
    if provider == ProviderName.OPENAI:
        function = payload.get("function")
        if isinstance(function, dict):
            provider_tool_name = str(function.get("name") or "")
            raw_args = function.get("arguments", _MISSING)
        else:
            provider_tool_name = str(payload.get("name") or "")
            raw_args = payload.get("arguments", _MISSING)
        args, raw_size = _coerce_args_object(
            provider,
            provider_tool_name,
            raw_args,
            request_id=request_id,
            provider_call_id=provider_call_id,
            max_args_bytes=max_args_bytes,
            warnings=warnings,
            allow_empty_default=True,
        )
        return _validate_provider_args_object(
            provider,
            provider_tool_name,
            args,
            raw_size,
            request_id=request_id,
            provider_call_id=provider_call_id,
            warnings=warnings,
        )
    if provider == ProviderName.ANTHROPIC:
        provider_tool_name = str(payload.get("name") or "")
        raw_args = payload.get("input", _MISSING)
        args, raw_size = _coerce_args_object(
            provider,
            provider_tool_name,
            raw_args,
            request_id=request_id,
            provider_call_id=provider_call_id,
            max_args_bytes=max_args_bytes,
            warnings=warnings,
            allow_empty_default=False,
        )
        return _validate_provider_args_object(
            provider,
            provider_tool_name,
            args,
            raw_size,
            request_id=request_id,
            provider_call_id=provider_call_id,
            warnings=warnings,
        )
    provider_tool_name = str(payload.get("name") or "")
    raw_args = payload.get("arguments", payload.get("input", _MISSING))
    args, raw_size = _coerce_args_object(
        provider,
        provider_tool_name,
        raw_args,
        request_id=request_id,
        provider_call_id=provider_call_id,
        max_args_bytes=max_args_bytes,
        warnings=warnings,
        allow_empty_default=False,
    )
    return _validate_provider_args_object(
        provider,
        provider_tool_name,
        args,
        raw_size,
        request_id=request_id,
        provider_call_id=provider_call_id,
        warnings=warnings,
    )


def _coerce_args_object(
    provider: str,
    provider_tool_name: str,
    raw_args: Any,
    *,
    request_id: str,
    provider_call_id: str | None,
    max_args_bytes: int,
    warnings: list[str],
    allow_empty_default: bool,
) -> tuple[Any, int]:
    if raw_args is _MISSING:
        raise ProviderToolCallError(
            "TOOL_CALL_MISSING_ARGUMENTS",
            "Tool call arguments are missing.",
            provider=provider,
            request_id=request_id,
            provider_call_id=provider_call_id,
            provider_tool_name=provider_tool_name,
        )
    if raw_args in (None, "") and allow_empty_default:
        raw_args = "{}"
        warnings.append("empty_arguments_defaulted")
    if isinstance(raw_args, str):
        raw_size = _byte_size(raw_args)
        if raw_size > max_args_bytes:
            raise ProviderToolCallError(
                "ARGUMENTS_TOO_LARGE",
                "Tool arguments exceed the provider bridge limit.",
                provider=provider,
                request_id=request_id,
                provider_call_id=provider_call_id,
                provider_tool_name=provider_tool_name,
            )
        try:
            return json.loads(raw_args), raw_size
        except json.JSONDecodeError as exc:
            raise ProviderToolCallError(
                "INVALID_ARGUMENT_JSON",
                "Tool arguments are not valid JSON.",
                provider=provider,
                request_id=request_id,
                provider_call_id=provider_call_id,
                provider_tool_name=provider_tool_name,
            ) from exc
    raw_size = _json_size(raw_args)
    if raw_size > max_args_bytes:
        raise ProviderToolCallError(
            "ARGUMENTS_TOO_LARGE",
            "Tool arguments exceed the provider bridge limit.",
            provider=provider,
            request_id=request_id,
            provider_call_id=provider_call_id,
            provider_tool_name=provider_tool_name,
        )
    return raw_args, raw_size


def _validate_provider_args_object(
    provider: str,
    provider_tool_name: str,
    args: Any,
    raw_size: int,
    *,
    request_id: str,
    provider_call_id: str | None,
    warnings: list[str],
) -> tuple[str, dict[str, Any], int, list[str]]:
    if not provider_tool_name:
        raise ProviderToolCallError(
            "TOOL_CALL_MISSING_NAME",
            "Tool name is missing.",
            provider=provider,
            request_id=request_id,
            provider_call_id=provider_call_id,
        )
    if not isinstance(args, dict):
        raise ProviderToolCallError(
            "PARAM_SCHEMA_ERROR",
            "Tool arguments must be a JSON object.",
            provider=provider,
            request_id=request_id,
            provider_call_id=provider_call_id,
            provider_tool_name=provider_tool_name,
        )
    return provider_tool_name, args, raw_size, warnings


def _provider_call_id(provider: str, payload: dict[str, Any]) -> str | None:
    value = payload.get("id") or payload.get("call_id") or payload.get("tool_call_id") or payload.get("request_id")
    if value is None:
        return None
    return str(value)


def _provider_idempotency_key(payload: dict[str, Any]) -> str | None:
    value = payload.get("idempotency_key")
    if value is None:
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            value = metadata.get("idempotency_key")
    if value in (None, ""):
        return None
    return str(value)


def _provider_confirmation_token(payload: dict[str, Any]) -> str | None:
    value = payload.get("confirmation_token")
    if value is None:
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            value = metadata.get("confirmation_token")
    if value in (None, ""):
        return None
    return str(value)


def _governance_metadata(decision: Any) -> dict[str, Any]:
    return {
        "decision": decision.decision,
        "reason": decision.reason,
        "risk_level": decision.risk_level,
        "requires_confirmation": decision.requires_confirmation,
        "remaining_budget": decision.remaining_budget,
        "idempotency_status": decision.idempotency_status,
        "idempotency_key_hash": decision.idempotency_key_hash,
        "args_hash": decision.args_hash,
        "execution_mode": decision.execution_mode,
    }


def _provider_params_failure_envelope(
    spec: LLMToolSpec,
    args: dict[str, Any],
    *,
    request_id: str,
    actor_role: str,
    audit_logger: AuditLogger | NullAuditLogger,
) -> dict[str, Any] | None:
    try:
        validate_json_value(args, spec.parameters_schema)
    except SchemaValidationError as exc:
        violation = exc.violation
        audit_logger.record(
            "plugin.tool_params_schema_violation",
            "error",
            request_id=request_id,
            plugin=spec.plugin_id,
            action=spec.tool_name,
            details={
                "request_id": request_id,
                "actor_role": actor_role,
                "plugin_id": spec.plugin_id,
                "plugin_version": spec.plugin_version,
                "tool_name": spec.tool_name,
                "model_tool_name": spec.name,
                "schema_path": violation.schema_path,
                "violation": violation.violation,
                "expected": violation.expected,
                "actual_type": violation.actual_type,
                "decision": "deny",
                "arg_summary": _summarize_json(args),
                "version": spec.plugin_version,
            },
            plugin_id=spec.plugin_id,
            plugin_version=spec.plugin_version,
            decision="deny",
            reason=violation.violation,
        )
        return provider_failure_envelope(
            request_id=request_id,
            code="PARAM_SCHEMA_ERROR",
            message=f"Tool arguments do not match schema at {violation.instance_path}.",
            model_tool_name=spec.name,
        )
    except SchemaDefinitionError:
        audit_logger.record(
            "plugin.tool_params_schema_violation",
            "error",
            request_id=request_id,
            plugin=spec.plugin_id,
            action=spec.tool_name,
            details={
                "request_id": request_id,
                "actor_role": actor_role,
                "plugin_id": spec.plugin_id,
                "plugin_version": spec.plugin_version,
                "tool_name": spec.tool_name,
                "model_tool_name": spec.name,
                "schema_path": "$",
                "violation": "invalid_params_schema",
                "expected": "supported JSON Schema subset",
                "actual_type": "schema",
                "decision": "deny",
                "version": spec.plugin_version,
            },
            plugin_id=spec.plugin_id,
            plugin_version=spec.plugin_version,
            decision="deny",
            reason="invalid_params_schema",
        )
        return provider_failure_envelope(
            request_id=request_id,
            code="PARAM_SCHEMA_ERROR",
            message=safe_tool_error_message("PARAM_SCHEMA_ERROR"),
            model_tool_name=spec.name,
        )
    return None


def _summarize_json(value: Any) -> dict[str, Any]:
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


def _risk_sort_key(spec: LLMToolSpec) -> int:
    order = {
        LLMToolRiskLevel.LOW: 0,
        LLMToolRiskLevel.MEDIUM: 1,
        LLMToolRiskLevel.HIGH: 2,
        LLMToolRiskLevel.CRITICAL: 3,
    }
    return order.get(spec.risk_level, 4)


def _json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return MAX_TOOL_ARGUMENT_BYTES + 1


def _byte_size(value: str) -> int:
    return len(value.encode("utf-8", errors="replace"))


def _warning(code: str, message: str, *, provider: str, model_tool_name: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "provider": provider,
    }
    if model_tool_name is not None:
        payload["model_tool_name"] = model_tool_name
    return payload


def _audit_provider_exported(
    audit_logger: AuditLogger | NullAuditLogger | None,
    *,
    request_id: str | None,
    provider: str,
    actor_role: str,
    original_count: int,
    exported_count: int,
    hidden_count: int,
    suspicious_description_count: int,
    warnings_count: int,
) -> None:
    if audit_logger is None:
        return
    audit_logger.record(
        "plugin.provider_tool_exported",
        "success",
        request_id=request_id or new_request_id(),
        action="provider_tool_export",
        details={
            "request_id": request_id,
            "provider": provider,
            "actor_role": actor_role,
            "original_count": original_count,
            "exported_count": exported_count,
            "hidden_count": hidden_count,
            "suspicious_description_count": suspicious_description_count,
            "warnings_count": warnings_count,
        },
    )


def _audit_provider_export_limited(
    audit_logger: AuditLogger | NullAuditLogger | None,
    *,
    request_id: str | None,
    provider: str,
    actor_role: str,
    original_count: int,
    exported_count: int,
    hidden_count: int,
    reason: str,
) -> None:
    if audit_logger is None:
        return
    audit_logger.record(
        "plugin.provider_tool_export_limited",
        "success",
        request_id=request_id or new_request_id(),
        action="provider_tool_export",
        details={
            "request_id": request_id,
            "provider": provider,
            "actor_role": actor_role,
            "original_count": original_count,
            "exported_count": exported_count,
            "hidden_count": hidden_count,
            "reason": reason,
        },
        decision="limit",
        reason=reason,
    )
