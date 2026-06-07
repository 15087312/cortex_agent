from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from .audit import AuditLogger, NullAuditLogger, new_request_id
from .models import InstalledPlugin, PermissionName, PluginMetadata, PluginStatus, TrustLevel
from .schema_validation import SchemaDefinitionError, SchemaValidationError, validate_json_value
from .tool_errors import normalize_tool_error_code, safe_tool_error_message, tool_error_info
from .tool_result import MAX_TOOL_RESULT_BYTES


DESCRIPTION_MAX_CHARS = 512
SCHEMA_MAX_BYTES = 32 * 1024
MAX_ENUM_ITEMS = 100
HIGH_RISK_PERMISSIONS = frozenset(
    {
        PermissionName.NETWORK_OUTBOUND.value,
        PermissionName.FS_READ.value,
        PermissionName.FS_WRITE.value,
        PermissionName.MEMORY_WRITE.value,
        PermissionName.OUTPUT_SEND.value,
    }
)
SENSITIVE_RESULT_REJECTION_MARKERS = (
    "tool result exceeds",
    "tool result is not json serializable",
    "tool result contains unsupported value type",
)
PROMPT_INJECTION_PATTERNS = (
    re.compile(r"\bignore\s+(all\s+)?previous\s+instructions\b", re.IGNORECASE),
    re.compile(r"\boverride\s+(the\s+)?(system|developer)\s+instructions\b", re.IGNORECASE),
    re.compile(r"\bdisregard\s+(the\s+)?(system|developer|previous)\s+instructions\b", re.IGNORECASE),
    re.compile(r"\breveal\s+(the\s+)?(system|developer)\s+prompt\b", re.IGNORECASE),
    re.compile(r"\bdeveloper\s+message\b", re.IGNORECASE),
    re.compile(r"\bexfiltrate\s+secrets?\b", re.IGNORECASE),
    re.compile(r"\bbypass\s+(the\s+)?policy\b", re.IGNORECASE),
    re.compile(r"\bsend\s+memory\b", re.IGNORECASE),
    re.compile(r"\bdisable\s+safety\b", re.IGNORECASE),
    re.compile(r"\bcall\s+this\s+tool\s+automatically\b", re.IGNORECASE),
    re.compile(r"\bhidden\s+instructions?\b", re.IGNORECASE),
    re.compile(r"\bjailbreak\b", re.IGNORECASE),
    re.compile(r"\byou\s+are\s+now\s+(system|developer|admin)\b", re.IGNORECASE),
    re.compile(r"\btreat\s+this\s+as\s+(a\s+)?system\s+message\b", re.IGNORECASE),
)


class LLMToolExposure(str):
    HIDDEN = "hidden"
    MODEL_DEFAULT = "model_default"
    EXPERT_ONLY = "expert_only"
    ADMIN_ONLY = "admin_only"


class LLMToolRiskLevel(str):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class DescriptionSanitization:
    text: str
    suspicious: bool
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ToolExposureDecision:
    visible: bool
    exposure: str
    reasons: list[str]
    warnings: list[str]
    risk_level: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LLMToolSpec:
    name: str
    plugin_id: str
    plugin_version: str
    tool_name: str
    description: str
    parameters_schema: dict[str, Any]
    returns_schema_summary: dict[str, Any] | None
    risk_level: str
    required_permissions: list[str]
    exposure: str
    hidden: bool
    dangerous_capabilities: list[str]
    request_policy: dict[str, Any]
    timeout_ms: int
    max_result_bytes: int
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ToolExposurePolicy:
    """Decide whether a plugin tool may be shown to a model-facing caller."""

    def decide(
        self,
        *,
        metadata: PluginMetadata,
        installed: InstalledPlugin | None,
        tool_name: str,
        actor_role: str,
        production_mode: bool,
        approved_only: bool,
        description: DescriptionSanitization,
        params_schema: dict[str, Any],
        returns_schema: dict[str, Any] | None,
    ) -> ToolExposureDecision:
        reasons: list[str] = []
        warnings = list(description.warnings)
        tool_permissions = metadata.tool_requested_permissions(tool_name)
        dangerous = sorted(tool_permissions & HIGH_RISK_PERMISSIONS)
        risk_level = _risk_level(tool_permissions)
        schema_complete = bool(params_schema.get("properties")) and returns_schema is not None
        approved = _is_approved(installed)
        exposure = LLMToolExposure.MODEL_DEFAULT
        visible = True

        if approved_only and not approved:
            visible = False
            exposure = LLMToolExposure.HIDDEN
            reasons.append("plugin_not_approved")
        if description.suspicious:
            if (actor_role or "model").lower() == "model":
                visible = False
                exposure = LLMToolExposure.HIDDEN
            else:
                exposure = LLMToolExposure.EXPERT_ONLY
            reasons.append("description_prompt_injection_risk")
        if not params_schema.get("properties"):
            warnings.append("missing_params_schema")
            if production_mode:
                visible = False
                exposure = LLMToolExposure.HIDDEN
                reasons.append("missing_params_schema")
            else:
                exposure = LLMToolExposure.EXPERT_ONLY
        if returns_schema is None:
            warnings.append("missing_returns_schema")
            if production_mode:
                visible = False
                exposure = LLMToolExposure.HIDDEN
                reasons.append("missing_returns_schema")
        if metadata.runtime.trust == TrustLevel.THIRD_PARTY and not approved:
            exposure = LLMToolExposure.EXPERT_ONLY if visible else exposure
            reasons.append("third_party_requires_explicit_approval")
        if dangerous:
            exposure = _high_risk_exposure(dangerous)
            reasons.append("dangerous_capabilities")
        if production_mode and metadata.runtime.trust == TrustLevel.THIRD_PARTY and metadata.effective_run_mode.value == "in_process":
            visible = False
            exposure = LLMToolExposure.HIDDEN
            reasons.append("legacy_or_in_process_hidden_in_production")
        if not schema_complete:
            reasons.append("schema_incomplete")

        visible = visible and _actor_can_see(actor_role, exposure)
        if not visible and not reasons:
            reasons.append("actor_role_not_allowed")
        return ToolExposureDecision(
            visible=visible,
            exposure=exposure,
            reasons=reasons or ["allowed"],
            warnings=warnings,
            risk_level=risk_level,
        )


class LLMToolCatalog:
    def __init__(
        self,
        specs: list[LLMToolSpec],
        decisions: dict[str, ToolExposureDecision],
        *,
        request_id: str | None = None,
    ) -> None:
        self.specs = specs
        self.decisions = decisions
        self.request_id = request_id
        self._by_name = {spec.name: spec for spec in specs}

    @classmethod
    def from_engine(
        cls,
        engine: Any,
        *,
        actor_role: str = "model",
        trust_level: str | None = None,
        production_mode: bool | None = None,
        approved_only: bool = True,
        include_hidden: bool = False,
        request_id: str | None = None,
        audit_logger: AuditLogger | NullAuditLogger | None = None,
    ) -> "LLMToolCatalog":
        engine.discover()
        installed = list(getattr(engine.loader, "installed_plugins", {}).values())
        return cls.from_installed_plugins(
            installed,
            actor_role=actor_role,
            trust_level=trust_level,
            production_mode=bool(getattr(engine, "production_mode", False) if production_mode is None else production_mode),
            approved_only=approved_only,
            include_hidden=include_hidden,
            request_id=request_id,
            audit_logger=audit_logger or getattr(engine, "audit_logger", None),
        )

    @classmethod
    def from_installed_plugins(
        cls,
        installed_plugins: list[InstalledPlugin] | tuple[InstalledPlugin, ...],
        *,
        actor_role: str = "model",
        trust_level: str | None = None,
        production_mode: bool = False,
        approved_only: bool = True,
        include_hidden: bool = False,
        request_id: str | None = None,
        audit_logger: AuditLogger | NullAuditLogger | None = None,
    ) -> "LLMToolCatalog":
        policy = ToolExposurePolicy()
        specs: list[LLMToolSpec] = []
        decisions: dict[str, ToolExposureDecision] = {}
        all_specs = 0
        high_risk_count = 0
        for installed in installed_plugins:
            metadata = installed.metadata
            if trust_level and metadata.runtime.trust.value != trust_level:
                continue
            for tool_name in sorted(metadata.tool_entries()):
                spec, decision = _build_tool_spec(
                    metadata,
                    installed,
                    tool_name,
                    actor_role=actor_role,
                    production_mode=production_mode,
                    approved_only=approved_only,
                    policy=policy,
                )
                all_specs += 1
                if spec.risk_level in {LLMToolRiskLevel.HIGH, LLMToolRiskLevel.CRITICAL}:
                    high_risk_count += 1
                decisions[spec.name] = decision
                if decision.visible or include_hidden:
                    specs.append(spec)
        catalog = cls(specs, decisions, request_id=request_id)
        _audit_catalog_generated(
            audit_logger,
            request_id=request_id,
            actor_role=actor_role,
            visible_tool_count=sum(1 for spec in specs if not spec.hidden),
            hidden_tool_count=max(0, all_specs - sum(1 for item in decisions.values() if item.visible)),
            high_risk_tool_count=high_risk_count,
        )
        return catalog

    @classmethod
    def from_plugin_dir(
        cls,
        plugin_dir: str | Path,
        *,
        actor_role: str = "expert",
        production_mode: bool = False,
        approved: bool = False,
        include_hidden: bool = False,
    ) -> "LLMToolCatalog":
        metadata = read_plugin_metadata(plugin_dir)
        installed = InstalledPlugin(
            metadata=metadata,
            path=str(Path(plugin_dir).resolve()),
            status=PluginStatus.ENABLED,
            granted_permissions=metadata.permissions if approved else [{"compute": True}],
            permission_review={"required": not approved, "reviewed": approved},
        )
        return cls.from_installed_plugins(
            [installed],
            actor_role=actor_role,
            production_mode=production_mode,
            approved_only=False,
            include_hidden=include_hidden,
        )

    def visible_specs(self) -> list[LLMToolSpec]:
        return [spec for spec in self.specs if not spec.hidden]

    def get(self, model_tool_name: str) -> LLMToolSpec | None:
        return self._by_name.get(model_tool_name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "success",
            "request_id": self.request_id,
            "tools": [spec.to_dict() for spec in self.specs],
            "decisions": {name: decision.to_dict() for name, decision in self.decisions.items()},
            "generated_at": datetime.now(UTC).isoformat(),
        }


class LLMToolRuntime:
    """Uniform model-facing tool invocation wrapper around PluginEngine.call_tool."""

    def __init__(self, engine: Any, *, audit_logger: AuditLogger | NullAuditLogger | None = None) -> None:
        self.engine = engine
        self.audit_logger = audit_logger or getattr(engine, "audit_logger", None) or NullAuditLogger()

    def invoke(
        self,
        model_tool_name: str,
        args: dict[str, Any],
        *,
        actor_role: str = "model",
        request_id: str | None = None,
        conversation_id: str | None = None,
        production_mode: bool | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_id = request_id or new_request_id()
        started = time.perf_counter()
        production = bool(getattr(self.engine, "production_mode", False) if production_mode is None else production_mode)
        self._audit(
            "plugin.llm_tool_call_requested",
            "success",
            request_id=request_id,
            actor_role=actor_role,
            model_tool_name=model_tool_name,
            decision="requested",
            conversation_id=conversation_id,
            arg_summary=_summarize_json(args),
            context_keys=sorted((context or {}).keys()),
        )
        catalog = LLMToolCatalog.from_engine(
            self.engine,
            actor_role=actor_role,
            production_mode=production,
            approved_only=True,
            include_hidden=True,
            request_id=request_id,
            audit_logger=self.audit_logger,
        )
        spec = catalog.get(model_tool_name)
        if spec is None or spec.hidden:
            return self._deny(
                request_id=request_id,
                started=started,
                actor_role=actor_role,
                model_tool_name=model_tool_name,
                spec=spec,
                code="TOOL_NOT_VISIBLE",
                reason="tool_not_visible",
                message="Tool is not available to this caller.",
            )
        try:
            validate_json_value(args, spec.parameters_schema)
        except SchemaValidationError as exc:
            violation = exc.violation
            self.audit_logger.record(
                "plugin.tool_params_schema_violation",
                "error",
                request_id=request_id,
                plugin=spec.plugin_id,
                action=spec.tool_name,
                details={
                    "request_id": request_id,
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
            return self._deny(
                request_id=request_id,
                started=started,
                actor_role=actor_role,
                model_tool_name=model_tool_name,
                spec=spec,
                code="PARAM_SCHEMA_ERROR",
                reason=violation.violation,
                message=f"Tool arguments do not match schema at {violation.instance_path}.",
            )
        except SchemaDefinitionError as exc:
            self.audit_logger.record(
                "plugin.tool_params_schema_violation",
                "error",
                request_id=request_id,
                plugin=spec.plugin_id,
                action=spec.tool_name,
                details={
                    "request_id": request_id,
                    "plugin_id": spec.plugin_id,
                    "plugin_version": spec.plugin_version,
                    "tool_name": spec.tool_name,
                    "model_tool_name": spec.name,
                    "schema_path": "$",
                    "violation": "invalid_params_schema",
                    "decision": "deny",
                    "exception_category": type(exc).__name__,
                    "version": spec.plugin_version,
                },
                plugin_id=spec.plugin_id,
                plugin_version=spec.plugin_version,
                decision="deny",
                reason="invalid_params_schema",
            )
            return self._deny(
                request_id=request_id,
                started=started,
                actor_role=actor_role,
                model_tool_name=model_tool_name,
                spec=spec,
                code="PARAM_SCHEMA_ERROR",
                reason="invalid_params_schema",
                message=_safe_error_message("PARAM_SCHEMA_ERROR"),
            )
        self._audit(
            "plugin.llm_tool_call_allowed",
            "success",
            request_id=request_id,
            actor_role=actor_role,
            spec=spec,
            model_tool_name=model_tool_name,
            decision="allow",
            reason="visible_and_params_valid",
        )
        try:
            result = self.engine.call_tool(spec.plugin_id, spec.tool_name, args, request_id=request_id)
        except Exception as exc:
            duration_ms = _duration_ms(started)
            code = _error_code(str(exc))
            self._audit(
                "plugin.llm_tool_call_failed",
                "error",
                request_id=request_id,
                actor_role=actor_role,
                spec=spec,
                model_tool_name=model_tool_name,
                decision="deny",
                reason="internal_exception",
                duration_ms=duration_ms,
                error_code=code,
                exception_category=type(exc).__name__,
            )
            return _failure_envelope(
                request_id=request_id,
                spec=spec,
                code=code,
                message=_safe_error_message(code),
                duration_ms=duration_ms,
            )
        duration_ms = _duration_ms(started)
        if result.get("status") != "success":
            code = _error_code(str(result.get("error", "")))
            self._audit(
                "plugin.llm_tool_call_failed",
                "error",
                request_id=request_id,
                actor_role=actor_role,
                spec=spec,
                model_tool_name=model_tool_name,
                decision="deny",
                reason=str(result.get("error", "tool_failed"))[:160],
                duration_ms=duration_ms,
                error_code=code,
            )
            return _failure_envelope(
                request_id=request_id,
                spec=spec,
                code=code,
                message=_safe_error_message(code),
                duration_ms=duration_ms,
            )
        metadata = result.get("_tool_result_metadata") if isinstance(result, dict) else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        sanitized = bool(metadata.get("sanitized_fields"))
        truncated = bool(metadata.get("truncated_fields"))
        result_size = metadata.get("final_size_bytes")
        self._audit(
            "plugin.llm_tool_call_completed",
            "success",
            request_id=request_id,
            actor_role=actor_role,
            spec=spec,
            model_tool_name=model_tool_name,
            decision="allow",
            reason="completed",
            duration_ms=duration_ms,
            sanitized=sanitized,
            truncated=truncated,
        )
        return {
            "ok": True,
            "request_id": request_id,
            "plugin_id": spec.plugin_id,
            "plugin_version": spec.plugin_version,
            "tool_name": spec.tool_name,
            "model_tool_name": spec.name,
            "result": result.get("data"),
            "sanitized": sanitized,
            "truncated": truncated,
            "metadata": {
                "duration_ms": duration_ms,
                "result_size_bytes": result_size,
            },
        }

    def _deny(
        self,
        *,
        request_id: str,
        started: float,
        actor_role: str,
        model_tool_name: str,
        spec: LLMToolSpec | None,
        code: str,
        reason: str,
        message: str,
    ) -> dict[str, Any]:
        duration_ms = _duration_ms(started)
        self._audit(
            "plugin.llm_tool_call_denied",
            "error",
            request_id=request_id,
            actor_role=actor_role,
            spec=spec,
            model_tool_name=model_tool_name,
            decision="deny",
            reason=reason,
            duration_ms=duration_ms,
            error_code=code,
        )
        return _failure_envelope(
            request_id=request_id,
            spec=spec,
            model_tool_name=model_tool_name,
            code=code,
            message=message,
            duration_ms=duration_ms,
        )

    def _audit(
        self,
        event: str,
        result: str,
        *,
        request_id: str,
        actor_role: str,
        model_tool_name: str,
        spec: LLMToolSpec | None = None,
        decision: str,
        reason: str | None = None,
        duration_ms: int | None = None,
        error_code: str | None = None,
        sanitized: bool | None = None,
        truncated: bool | None = None,
        conversation_id: str | None = None,
        arg_summary: dict[str, Any] | None = None,
        context_keys: list[str] | None = None,
        exception_category: str | None = None,
    ) -> None:
        details = {
            "request_id": request_id,
            "actor_role": actor_role,
            "plugin_id": spec.plugin_id if spec else None,
            "plugin_version": spec.plugin_version if spec else None,
            "tool_name": spec.tool_name if spec else None,
            "model_tool_name": model_tool_name,
            "decision": decision,
            "reason": reason,
            "duration_ms": duration_ms,
            "error_code": error_code,
            "sanitized": sanitized,
            "truncated": truncated,
            "conversation_id": conversation_id,
            "arg_summary": arg_summary,
            "context_keys": context_keys,
            "exception_category": exception_category,
        }
        self.audit_logger.record(
            event,
            result,
            request_id=request_id,
            plugin=spec.plugin_id if spec else None,
            action=spec.tool_name if spec else model_tool_name,
            details={key: value for key, value in details.items() if value is not None},
            plugin_id=spec.plugin_id if spec else None,
            plugin_version=spec.plugin_version if spec else None,
            decision=decision,
            reason=reason,
        )


def llm_model_tool_name(plugin_id: str, tool_name: str) -> str:
    return f"{_safe_slug(plugin_id)}.{_safe_slug(tool_name)}"


def sanitize_tool_description(value: str | None) -> DescriptionSanitization:
    raw = " ".join(str(value or "").replace("\x00", " ").split())
    warnings: list[str] = []
    suspicious = False
    for pattern in PROMPT_INJECTION_PATTERNS:
        if pattern.search(raw):
            suspicious = True
            if "description_prompt_injection_risk" not in warnings:
                warnings.append("description_prompt_injection_risk")
            raw = pattern.sub("[removed]", raw)
    if len(raw) > DESCRIPTION_MAX_CHARS:
        warnings.append("description_truncated")
        raw = raw[: DESCRIPTION_MAX_CHARS - 15].rstrip() + "...[truncated]"
    return DescriptionSanitization(text=raw, suspicious=suspicious, warnings=warnings)


def tool_parameters_schema(metadata: PluginMetadata, tool_name: str) -> dict[str, Any]:
    spec = metadata.tool_extension_specs().get(tool_name)
    params = spec.params if spec else {}
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            name: param.to_json_schema()
            for name, param in sorted(params.items())
        },
        "additionalProperties": False,
    }
    required = sorted(name for name, param in params.items() if param.required)
    if required:
        schema["required"] = required
    return schema


def read_plugin_metadata(plugin_dir: str | Path) -> PluginMetadata:
    path = Path(plugin_dir).resolve() / "plugin.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("plugin.yaml must contain a mapping")
    return PluginMetadata(**raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preview sanitized LLM-facing plugin tools")
    parser.add_argument("plugin_dir")
    parser.add_argument("--actor-role", default="expert", choices=["model", "expert", "admin"])
    parser.add_argument("--provider", choices=["generic", "openai", "anthropic"])
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--include-hidden", action="store_true")
    parser.add_argument("--include-returns-summary", action="store_true")
    parser.add_argument("--max-tools", type=int, default=128)
    parser.add_argument("--max-description-chars", type=int, default=DESCRIPTION_MAX_CHARS)
    parser.add_argument("--max-schema-bytes-per-tool", type=int, default=SCHEMA_MAX_BYTES)
    parser.add_argument("--max-total-schema-bytes", type=int, default=256 * 1024)
    parser.add_argument("--governance-preview", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    catalog = LLMToolCatalog.from_plugin_dir(
        args.plugin_dir,
        actor_role=args.actor_role,
        production_mode=args.production,
        approved=args.approved,
        include_hidden=args.include_hidden,
    )
    if args.provider:
        from .provider_tools import ProviderToolExportOptions, export_provider_tools

        payload = export_provider_tools(
            catalog,
            options=ProviderToolExportOptions(
                provider=args.provider,
                actor_role=args.actor_role,
                production_mode=args.production,
                include_hidden=args.include_hidden,
                include_returns_summary=args.include_returns_summary,
                max_tools=args.max_tools,
                max_description_chars=args.max_description_chars,
                max_schema_bytes_per_tool=args.max_schema_bytes_per_tool,
                max_total_schema_bytes=args.max_total_schema_bytes,
            ),
        )
        if args.governance_preview:
            from .tool_governance import tool_risk_decision

            specs_by_name = {spec.name: spec for spec in catalog.specs}
            governance: dict[str, Any] = {}
            for provider_name, mapping in payload.get("name_mapping", {}).items():
                if not isinstance(mapping, dict):
                    continue
                spec = specs_by_name.get(str(mapping.get("model_tool_name") or ""))
                if spec is None:
                    continue
                risk = tool_risk_decision(spec)
                governance[str(provider_name)] = {
                    "model_tool_name": spec.name,
                    "risk_level": risk.risk_level,
                    "required_permissions": risk.required_permissions,
                    "side_effecting": risk.side_effecting,
                    "requires_confirmation": risk.requires_confirmation,
                    "expected_side_effects": risk.expected_side_effects,
                }
            payload["governance_preview"] = governance
    else:
        payload = catalog.to_dict()
    if args.json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        if args.provider:
            print(
                f"provider={payload['provider']} actor_role={payload['actor_role']} "
                f"tools={len(payload['tools'])} warnings={len(payload['warnings'])}"
            )
            for item in payload["tools"]:
                name = item.get("function", {}).get("name") if args.provider == "openai" else item.get("name")
                print(f"{name}")
        else:
            for spec in payload["tools"]:
                print(f"{spec['name']} exposure={spec['exposure']} risk={spec['risk_level']}")
    return 0


def _build_tool_spec(
    metadata: PluginMetadata,
    installed: InstalledPlugin | None,
    tool_name: str,
    *,
    actor_role: str,
    production_mode: bool,
    approved_only: bool,
    policy: ToolExposurePolicy,
) -> tuple[LLMToolSpec, ToolExposureDecision]:
    tool_spec = metadata.tool_extension_specs()[tool_name]
    description = sanitize_tool_description(tool_spec.description or metadata.description)
    params_schema = tool_parameters_schema(metadata, tool_name)
    returns_schema = metadata.tool_result_schema(tool_name)
    required_permissions = sorted(metadata.tool_requested_permissions(tool_name))
    dangerous = sorted(set(required_permissions) & HIGH_RISK_PERMISSIONS)
    decision = policy.decide(
        metadata=metadata,
        installed=installed,
        tool_name=tool_name,
        actor_role=actor_role,
        production_mode=production_mode,
        approved_only=approved_only,
        description=description,
        params_schema=params_schema,
        returns_schema=returns_schema,
    )
    spec = LLMToolSpec(
        name=llm_model_tool_name(metadata.name, tool_name),
        plugin_id=metadata.name,
        plugin_version=metadata.version,
        tool_name=tool_name,
        description=description.text,
        parameters_schema=params_schema,
        returns_schema_summary=_returns_schema_summary(returns_schema),
        risk_level=decision.risk_level,
        required_permissions=required_permissions,
        exposure=decision.exposure,
        hidden=not decision.visible,
        dangerous_capabilities=dangerous,
        request_policy={
            "request_scope_required": True,
            "approved_required": approved_only,
            "actor_role": actor_role,
            "production_mode": production_mode,
        },
        timeout_ms=int(metadata.runtime.timeout_seconds * 1000),
        max_result_bytes=MAX_TOOL_RESULT_BYTES,
        warnings=decision.warnings,
    )
    return spec, decision


def _returns_schema_summary(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if schema is None:
        return None
    raw_properties = schema.get("properties")
    properties = raw_properties if isinstance(raw_properties, dict) else {}
    raw_items = schema.get("items")
    items = raw_items if isinstance(raw_items, dict) else {}
    return {
        "type": schema.get("type"),
        "required": list(schema.get("required") or []),
        "properties": sorted(str(name) for name in properties),
        "items_type": items.get("type"),
        "additionalProperties": schema.get("additionalProperties"),
    }


def _risk_level(permissions: set[str]) -> str:
    if PermissionName.MEMORY_WRITE.value in permissions or PermissionName.OUTPUT_SEND.value in permissions:
        return LLMToolRiskLevel.CRITICAL
    if PermissionName.FS_WRITE.value in permissions:
        return LLMToolRiskLevel.HIGH
    if PermissionName.NETWORK_OUTBOUND.value in permissions:
        return LLMToolRiskLevel.HIGH
    if permissions & {PermissionName.MEMORY_READ.value, PermissionName.CONFIG_READ.value, PermissionName.FS_READ.value}:
        return LLMToolRiskLevel.MEDIUM
    return LLMToolRiskLevel.LOW


def _high_risk_exposure(dangerous: list[str]) -> str:
    if any(item in dangerous for item in [PermissionName.FS_WRITE.value, PermissionName.MEMORY_WRITE.value, PermissionName.OUTPUT_SEND.value]):
        return LLMToolExposure.ADMIN_ONLY
    return LLMToolExposure.EXPERT_ONLY


def _actor_can_see(actor_role: str, exposure: str) -> bool:
    role = (actor_role or "model").lower()
    if exposure == LLMToolExposure.HIDDEN:
        return False
    if exposure == LLMToolExposure.MODEL_DEFAULT:
        return role in {"model", "expert", "admin"}
    if exposure == LLMToolExposure.EXPERT_ONLY:
        return role in {"expert", "admin"}
    if exposure == LLMToolExposure.ADMIN_ONLY:
        return role == "admin"
    return False


def _is_approved(installed: InstalledPlugin | None) -> bool:
    if installed is None or installed.status != PluginStatus.ENABLED:
        return False
    review = installed.permission_review or {}
    if review.get("required"):
        return False
    return True


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()
    return re.sub(r"_+", "_", slug) or "tool"


def _audit_catalog_generated(
    audit_logger: AuditLogger | NullAuditLogger | None,
    *,
    request_id: str | None,
    actor_role: str,
    visible_tool_count: int,
    hidden_tool_count: int,
    high_risk_tool_count: int,
) -> None:
    if audit_logger is None:
        return
    audit_logger.record(
        "plugin.llm_tool_catalog_generated",
        "success",
        request_id=request_id or new_request_id(),
        action="llm_tool_catalog",
        details={
            "actor_role": actor_role,
            "visible_tool_count": visible_tool_count,
            "hidden_tool_count": hidden_tool_count,
            "high_risk_tool_count": high_risk_tool_count,
            "request_id": request_id,
        },
    )


def _failure_envelope(
    *,
    request_id: str,
    spec: LLMToolSpec | None,
    code: str,
    message: str,
    duration_ms: int,
    model_tool_name: str | None = None,
) -> dict[str, Any]:
    info = tool_error_info(code)
    return {
        "ok": False,
        "request_id": request_id,
        "plugin_id": spec.plugin_id if spec else None,
        "plugin_version": spec.plugin_version if spec else None,
        "tool_name": spec.tool_name if spec else None,
        "model_tool_name": spec.name if spec else model_tool_name,
        "error": {
            "code": info.code,
            "message": safe_tool_error_message(info.code) if not info.expose_to_model else message,
            "retryable": info.retryable,
            "category": info.category,
        },
        "metadata": {"duration_ms": duration_ms},
    }


def _error_code(message: str) -> str:
    normalized = message.lower()
    if "schema" in normalized and "return" in normalized:
        return "RETURN_SCHEMA_ERROR"
    if "argument" in normalized or "params" in normalized:
        return "PARAM_SCHEMA_ERROR"
    if "permission" in normalized or "does not have" in normalized or "scope" in normalized:
        return "PERMISSION_DENIED"
    if "timed out" in normalized:
        return "TOOL_TIMEOUT"
    if "sandbox" in normalized and ("unavailable" in normalized or "not available" in normalized):
        return "SANDBOX_UNAVAILABLE"
    if any(marker in normalized for marker in SENSITIVE_RESULT_REJECTION_MARKERS):
        return "SANITIZED_REJECTED"
    return normalize_tool_error_code("INTERNAL_ERROR")


def _safe_error_message(code: str) -> str:
    return safe_tool_error_message(code)


def _duration_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


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


if __name__ == "__main__":
    raise SystemExit(main())
