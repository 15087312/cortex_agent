"""
插件工具服务 — AI 自创工具的注册、发现、调用

设计目标：
  管理 AI 通过 learn 模式创建的插件工具。每个插件工具是一个标准包
  （plugin.yaml + tool_impl.py + recipe.json），存放在 data/plugins/ 下。

与 MCP 的关系：
  本地自创工具走此服务，外部工具走 MCP。两者在 CombinedToolProvider 中合并，
  模型看到的是统一的工具列表。
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from .audit import AuditLogger, NullAuditLogger, new_request_id
from .engine import PluginEngine
from .llm_tools import LLMToolCatalog, LLMToolRuntime
from .models import PluginStatus
from .provider_tools import (
    PROVIDER_NAMES,
    ModelToolBridge,
    ProviderToolExportOptions,
    export_provider_tools,
)
from .tool_contracts import (
    TOOL_SERVICE_CONTRACT_VERSION,
    RequestAuditSummary,
    ToolInvocationResponse,
    ToolListResponse,
    ToolServiceCapabilities,
    ToolServiceHealth,
    ToolServiceMetrics,
    ToolTraceContext,
    utc_now,
)
from .tool_errors import tool_error_catalog, tool_error_info, tool_error_payload
from .tool_governance import (
    ConfirmationProvider,
    LocalConfirmationProvider,
    ToolCallSessionStore,
    ToolExecutionMode,
    governance_store_metadata,
)


class PluginToolService:
    """Stable model-facing facade for plugin tools.

    The service delegates all execution to ModelToolBridge, LLMToolRuntime, and
    PluginEngine. It only owns contract shaping, lightweight metrics, and safe
    audit/session summaries.
    """

    def __init__(
        self,
        engine: PluginEngine | None = None,
        catalog: LLMToolCatalog | None = None,
        runtime: LLMToolRuntime | None = None,
        bridge: ModelToolBridge | None = None,
        production_mode: bool = True,
        actor_role_default: str = "model",
        audit_sink: Any | None = None,
        governance_store: ToolCallSessionStore | None = None,
        governance_store_factory: Any | None = None,
        confirmation_provider: ConfirmationProvider | None = None,
        strict_production: bool = False,
    ) -> None:
        self.engine = engine
        self.catalog = catalog
        self.production_mode = bool(
            getattr(engine, "production_mode", production_mode) if engine is not None else production_mode
        )
        self.actor_role_default = actor_role_default or "model"
        self.strict_production = bool(strict_production)
        self.audit_logger: AuditLogger | NullAuditLogger = (
            audit_sink
            or getattr(engine, "audit_logger", None)
            or getattr(bridge, "audit_logger", None)
            or NullAuditLogger()
        )
        self.governance_store = governance_store or (
            governance_store_factory() if callable(governance_store_factory) else ToolCallSessionStore()
        )
        self.confirmation_provider = confirmation_provider or LocalConfirmationProvider(
            self.governance_store,
            audit_logger=self.audit_logger,
        )
        self.bridge = bridge or (
            ModelToolBridge(
                engine,
                audit_logger=self.audit_logger,
                governance_store=self.governance_store,
                confirmation_provider=self.confirmation_provider,
            )
            if engine is not None
            else None
        )
        if bridge is not None:
            controller = getattr(bridge, "governance", None)
            store = getattr(controller, "store", None)
            if store is not None:
                self.governance_store = store
            provider = getattr(controller, "confirmation_provider", None)
            if provider is not None:
                self.confirmation_provider = provider
        self.runtime = runtime or (
            LLMToolRuntime(engine, audit_logger=self.audit_logger) if engine is not None else None
        )
        self.metrics = ToolServiceMetrics()
        self._audit_governance_store_warning_if_needed()

    def list_tools(
        self,
        *,
        provider: str = "generic",
        actor_role: str | None = None,
        conversation_id: str | None = None,
        include_hidden: bool = False,
        max_tools: int = 128,
        max_total_schema_bytes: int = 256 * 1024,
        request_id: str | None = None,
    ) -> ToolListResponse:
        provider = _normalize_provider(provider)
        role = actor_role or self.actor_role_default
        request_id = request_id or new_request_id()
        if self.engine is None and self.catalog is None:
            return ToolListResponse(
                ok=False,
                provider=provider,
                actor_role=role,
                tools=[],
                name_mapping={},
                warnings=[tool_error_payload("SERVICE_UNAVAILABLE")],
                hidden_count=0,
                exported_count=0,
                request_id=request_id,
            )
        catalog = self.catalog or LLMToolCatalog.from_engine(
            self.engine,
            actor_role=role,
            production_mode=self.production_mode,
            approved_only=True,
            include_hidden=True,
            request_id=request_id,
            audit_logger=self.audit_logger,
        )
        export = export_provider_tools(
            catalog,
            options=ProviderToolExportOptions(
                provider=provider,
                actor_role=role,
                production_mode=self.production_mode,
                include_hidden=include_hidden,
                max_tools=max_tools,
                max_total_schema_bytes=max_total_schema_bytes,
            ),
            audit_logger=self.audit_logger,
            request_id=request_id,
        )
        self._record_export(provider=provider, actor_role=role)
        hidden_count = max(0, len(catalog.specs) - len(export.get("tools", [])))
        return ToolListResponse(
            ok=True,
            provider=provider,
            actor_role=role,
            tools=list(export.get("tools", [])),
            name_mapping=dict(export.get("name_mapping", {})),
            warnings=list(export.get("warnings", [])),
            hidden_count=hidden_count,
            exported_count=len(export.get("tools", [])),
            request_id=request_id,
        )

    def invoke_tool_call(
        self,
        *,
        provider: str,
        payload: dict[str, Any],
        actor_role: str | None = None,
        conversation_id: str | None = None,
        request_id: str | None = None,
        execution_mode: str = ToolExecutionMode.EXECUTE,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> ToolInvocationResponse:
        provider = _normalize_provider(provider)
        role = actor_role or self.actor_role_default
        request_id = request_id or new_request_id()
        started = time.perf_counter()
        if self.bridge is None:
            envelope = _service_failure_envelope(request_id, "SERVICE_UNAVAILABLE")
            response = _service_provider_response(provider, envelope=envelope, provider_call_id=None, provider_tool_name=None)
            wrapped = self._invocation_contract(
                provider=provider,
                response=response,
                conversation_id=conversation_id,
                request_id=request_id,
                started=started,
            )
            self._record_call(provider=provider, actor_role=role, response=wrapped)
            return wrapped
        response_dict = self.bridge.invoke_provider_tool_call(
            provider,
            payload,
            actor_role=role,
            conversation_id=conversation_id,
            production_mode=self.production_mode,
            execution_mode=execution_mode,
            confirmation_token=confirmation_token,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
        wrapped = self._invocation_contract(
            provider=provider,
            response=response_dict,
            conversation_id=conversation_id,
            request_id=request_id,
            started=started,
        )
        self._record_call(provider=provider, actor_role=role, response=wrapped)
        return wrapped

    def get_request_audit_summary(self, request_id: str) -> RequestAuditSummary:
        records = _read_audit_records(self.audit_logger)
        matched = [record for record in records if getattr(record, "request_id", None) == request_id]
        plugin_id = None
        tool_name = None
        decision = None
        error_code = None
        sanitized = False
        truncated = False
        permission_denied = False
        schema_violation = False
        governance_decision = None
        duration_ms = None
        for record in matched:
            details = getattr(record, "details", {}) or {}
            plugin_id = plugin_id or getattr(record, "plugin_id", None) or details.get("plugin_id")
            tool_name = tool_name or getattr(record, "action", None) or details.get("tool_name")
            decision = details.get("decision") or getattr(record, "decision", None) or decision
            error_code = details.get("error_code") or error_code
            sanitized = sanitized or bool(details.get("sanitized"))
            truncated = truncated or bool(details.get("truncated"))
            permission_denied = permission_denied or "permission" in str(error_code or "").lower()
            schema_violation = schema_violation or "schema" in str(error_code or "").lower() or "schema" in str(getattr(record, "event", ""))
            if isinstance(details.get("governance"), dict):
                governance_decision = details["governance"].get("decision") or governance_decision
            governance_decision = details.get("governance_decision") or governance_decision
            raw_duration = details.get("duration_ms")
            if isinstance(raw_duration, int):
                duration_ms = raw_duration
        return RequestAuditSummary(
            request_id=request_id,
            plugin_id=str(plugin_id) if plugin_id is not None else None,
            tool_name=str(tool_name) if tool_name is not None else None,
            decision=str(decision) if decision is not None else None,
            error_code=str(error_code) if error_code is not None else None,
            sanitized=sanitized,
            truncated=truncated,
            permission_denied=permission_denied,
            schema_violation=schema_violation,
            governance_decision=str(governance_decision) if governance_decision is not None else None,
            duration_ms=duration_ms,
            audit_event_count=len(matched),
        )

    def get_conversation_summary(self, conversation_id: str) -> dict[str, Any]:
        policy = getattr(getattr(self.bridge, "governance", None), "policy", None)
        summary = self.governance_store.session_summary(conversation_id, policy=policy)
        summary["last_error_code"] = self.metrics.last_error_code
        return summary

    def health(self) -> ToolServiceHealth:
        warnings: list[str] = []
        blockers: list[str] = []
        degraded: list[str] = []
        if self.engine is None and self.catalog is None:
            blockers.append("engine_or_catalog_unavailable")
        if self.bridge is None:
            blockers.append("provider_bridge_unavailable")
        if not hasattr(self.audit_logger, "record"):
            degraded.append("audit_unavailable")
        store_metadata = governance_store_metadata(self.governance_store)
        if not bool(store_metadata.get("persistent")):
            warnings.append("governance_store_not_persistent")
            degraded.append("process_local_governance")
        if self.production_mode and not bool(store_metadata.get("production_recommended")):
            warnings.append("governance_store_not_production_recommended")
        if self.production_mode and not bool(store_metadata.get("multi_instance_safe")):
            warnings.append("governance_store_not_multi_instance_safe")
        if self.strict_production and warnings:
            blockers.extend(sorted(set(warnings)))
        confirmation_metadata = _confirmation_provider_metadata(self.confirmation_provider)
        if self.production_mode and not bool(confirmation_metadata.get("production_recommended")):
            warnings.append("confirmation_provider_not_production_recommended")
            degraded.append("local_confirmation_provider")
        ready_for_model_calls = self.bridge is not None and (self.engine is not None or self.catalog is not None)
        ready_for_production = ready_for_model_calls and not blockers and not warnings
        return ToolServiceHealth(
            ok=not blockers,
            production_mode=self.production_mode,
            engine_available=self.engine is not None,
            catalog_available=self.catalog is not None or self.engine is not None,
            provider_bridge_available=self.bridge is not None,
            audit_available=hasattr(self.audit_logger, "record"),
            governance_available=bool(getattr(self.bridge, "governance", None)) if self.bridge else False,
            ready_for_model_calls=ready_for_model_calls and not (self.strict_production and blockers),
            ready_for_production=ready_for_production,
            blockers=sorted(set(blockers)),
            warnings=sorted(set(warnings)),
            degraded_capabilities=sorted(set(degraded)),
            governance_store=store_metadata,
            confirmation_provider=confirmation_metadata,
        )

    def capabilities(self) -> ToolServiceCapabilities:
        sandbox = {
            "backend": str(getattr(self.engine, "sandbox_backend", "unknown")) if self.engine else "unknown",
            "require_enforced_sandbox": bool(getattr(self.engine, "require_enforced_sandbox", False)) if self.engine else False,
        }
        return ToolServiceCapabilities(
            providers=list(PROVIDER_NAMES),
            schema_validation=True,
            returns_validation=True,
            per_tool_permissions=True,
            governance=self.bridge is not None and getattr(self.bridge, "governance", None) is not None,
            sandbox=sandbox,
            audit=hasattr(self.audit_logger, "record"),
            legacy_compatibility=True,
            production_mode=self.production_mode,
            governance_store=governance_store_metadata(self.governance_store),
            confirmation_provider=_confirmation_provider_metadata(self.confirmation_provider),
        )

    def metrics_snapshot(self) -> dict[str, Any]:
        return self.metrics.to_dict()

    def reset_metrics(self) -> dict[str, Any]:
        if self.production_mode:
            self.audit_logger.record(
                "plugin.tool_service_metrics_reset",
                "success",
                request_id=new_request_id(),
                action="metrics_reset",
                details={"decision": "allow", "reason": "operator_reset"},
                decision="allow",
                reason="operator_reset",
            )
        self.metrics = ToolServiceMetrics()
        return self.metrics.to_dict()

    def reset_session(self, conversation_id: str) -> dict[str, Any]:
        removed = self.governance_store.reset_session(conversation_id)
        if self.production_mode:
            self.audit_logger.record(
                "plugin.tool_service_session_reset",
                "success",
                request_id=new_request_id(),
                action="session_reset",
                details={
                    "conversation_id": conversation_id,
                    "removed": removed,
                    "decision": "allow",
                    "reason": "operator_reset",
                },
                decision="allow",
                reason="operator_reset",
            )
        return {"conversation_id": conversation_id, "removed": removed}

    def _audit_governance_store_warning_if_needed(self) -> None:
        store_metadata = governance_store_metadata(self.governance_store)
        warning: str | None = None
        if not bool(store_metadata.get("persistent")):
            warning = "governance_store_not_persistent"
        if self.production_mode and not bool(store_metadata.get("multi_instance_safe")):
            warning = "governance_store_not_multi_instance_safe"
        if warning is None:
            return
        self.audit_logger.record(
            "plugin.governance_store_warning",
            "warning",
            request_id=new_request_id(),
            action="governance_store",
            details={
                "store_kind": store_metadata.get("store_kind"),
                "production_mode": self.production_mode,
                "persistent": store_metadata.get("persistent"),
                "multi_instance_safe": store_metadata.get("multi_instance_safe"),
                "warning": warning,
            },
            decision="warn",
            reason=warning,
        )

    def _invocation_contract(
        self,
        *,
        provider: str,
        response: dict[str, Any],
        conversation_id: str | None,
        request_id: str,
        started: float,
    ) -> ToolInvocationResponse:
        raw_envelope = response.get("envelope")
        envelope: dict[str, Any] = raw_envelope if isinstance(raw_envelope, dict) else {}
        error = envelope.get("error") if isinstance(envelope.get("error"), dict) else None
        if error is not None:
            error = tool_error_payload(error.get("code"))
        raw_metadata = envelope.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_governance = metadata.get("governance")
        governance: dict[str, Any] = raw_governance if isinstance(raw_governance, dict) else {}
        trace = ToolTraceContext(
            request_id=str(envelope.get("request_id") or response.get("request_id") or request_id),
            conversation_id=conversation_id,
            actor_role="",
            provider=provider,
            provider_call_id=response.get("provider_call_id"),
            model_tool_name=envelope.get("model_tool_name"),
            plugin_id=envelope.get("plugin_id"),
            tool_name=envelope.get("tool_name"),
            ended_at=utc_now(),
            duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
            decision=governance.get("decision") or ("allow" if response.get("ok") else "deny"),
            error_code=error.get("code") if error else None,
        )
        audit_summary = dict(response.get("audit_summary") or {})
        audit_summary["trace"] = trace.to_dict()
        return ToolInvocationResponse(
            ok=bool(response.get("ok")),
            provider=provider,
            provider_call_id=response.get("provider_call_id"),
            request_id=trace.request_id,
            conversation_id=conversation_id,
            model_tool_name=envelope.get("model_tool_name"),
            plugin_id=envelope.get("plugin_id"),
            plugin_version=envelope.get("plugin_version"),
            tool_name=envelope.get("tool_name"),
            response=dict(response.get("message") or {}),
            envelope=envelope,
            error=error,
            audit_summary=audit_summary,
        )

    def _record_export(self, *, provider: str, actor_role: str) -> None:
        self.metrics.tool_exports_total += 1
        _increment(self.metrics.provider_counts, provider)
        _increment(self.metrics.actor_role_counts, actor_role)

    def _record_call(self, *, provider: str, actor_role: str, response: ToolInvocationResponse) -> None:
        self.metrics.tool_calls_total += 1
        _increment(self.metrics.provider_counts, provider)
        _increment(self.metrics.actor_role_counts, actor_role)
        metric_tool_name = _metrics_tool_name(response.model_tool_name)
        _increment(self.metrics.per_tool_calls, metric_tool_name)
        if response.ok:
            self.metrics.tool_calls_allowed += 1
        else:
            self.metrics.tool_calls_denied += 1
            self.metrics.tool_calls_failed += 1
            _increment(self.metrics.per_tool_denials, metric_tool_name)
            _increment(self.metrics.per_tool_failures, metric_tool_name)
        error_code = response.error.get("code") if response.error else None
        if error_code:
            self.metrics.last_error_code = error_code
            _increment(self.metrics.per_error_code, error_code)
            self.metrics.last_denied_reason_by_tool[metric_tool_name] = error_code
            if error_code == "CONFIRMATION_REQUIRED":
                self.metrics.confirmation_required_total += 1
                _increment(self.metrics.confirmation_required_by_tool, metric_tool_name)
            elif error_code == "PERMISSION_DENIED":
                self.metrics.permission_denied_total += 1
            elif error_code == "PARAM_SCHEMA_ERROR":
                self.metrics.params_schema_error_total += 1
            elif error_code == "RETURN_SCHEMA_ERROR":
                self.metrics.return_schema_error_total += 1
            elif error_code == "BUDGET_EXCEEDED":
                self.metrics.budget_exceeded_total += 1
                _increment(self.metrics.budget_exceeded_by_tool, metric_tool_name)
            elif error_code == "RATE_LIMITED":
                self.metrics.rate_limited_total += 1
            elif error_code in {"DUPLICATE_TOOL_CALL", "DUPLICATE_IN_PROGRESS"}:
                self.metrics.duplicate_total += 1
        metadata = response.envelope.get("metadata") if isinstance(response.envelope, dict) else {}
        if response.envelope.get("sanitized") or (isinstance(metadata, dict) and metadata.get("sanitized")):
            self.metrics.sanitized_total += 1
        if response.envelope.get("truncated") or (isinstance(metadata, dict) and metadata.get("truncated")):
            self.metrics.truncated_total += 1


def _normalize_provider(provider: str) -> str:
    value = str(provider or "generic").lower()
    if value not in PROVIDER_NAMES:
        raise ValueError(f"unsupported provider: {provider}")
    return value


def _increment(mapping: dict[str, int], key: str) -> None:
    mapping[key] = mapping.get(key, 0) + 1


def _metrics_tool_name(value: str | None) -> str:
    raw = str(value or "unknown")
    sanitized = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw)[:128].strip("._-")
    return sanitized or "unknown"


def _confirmation_provider_metadata(provider: Any) -> dict[str, Any]:
    metadata = getattr(provider, "metadata", None)
    if callable(metadata):
        try:
            value = metadata()
        except Exception:
            value = None
        if isinstance(value, dict):
            return dict(value)
    return {
        "provider_kind": str(getattr(provider, "provider_kind", "unknown")),
        "production_recommended": bool(getattr(provider, "production_recommended", False)),
    }


def _read_audit_records(audit_logger: Any) -> list[Any]:
    reader = getattr(audit_logger, "read_records", None)
    if not callable(reader):
        return []
    try:
        return list(reader())
    except Exception:
        return []


def _service_failure_envelope(request_id: str, code: str) -> dict[str, Any]:
    info = tool_error_info(code)
    return {
        "ok": False,
        "request_id": request_id,
        "plugin_id": None,
        "plugin_version": None,
        "tool_name": None,
        "model_tool_name": None,
        "error": {
            "code": info.code,
            "message": info.safe_message,
            "retryable": info.retryable,
        },
        "metadata": {},
    }


def _service_provider_response(
    provider: str,
    *,
    envelope: dict[str, Any],
    provider_call_id: str | None,
    provider_tool_name: str | None,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "provider_call_id": provider_call_id,
        "ok": bool(envelope.get("ok")),
        "safe_content": {
            "untrusted_tool_result": True,
            "tool_result_may_contain_user_or_plugin_controlled_text": True,
            "provider": provider,
            "provider_call_id": provider_call_id,
            "provider_tool_name": provider_tool_name,
            "envelope": envelope,
        },
        "envelope": envelope,
        "error_code": envelope.get("error", {}).get("code") if isinstance(envelope.get("error"), dict) else None,
        "request_id": envelope.get("request_id"),
        "audit_summary": {
            "provider": provider,
            "provider_call_id": provider_call_id,
            "provider_tool_name": provider_tool_name,
            "request_id": envelope.get("request_id"),
            "ok": bool(envelope.get("ok")),
        },
        "message": {},
    }


def service_contracts_schema() -> dict[str, Any]:
    return {
        "contract_version": TOOL_SERVICE_CONTRACT_VERSION,
        "responses": [
            "ToolListResponse",
            "ToolInvocationResponse",
            "ToolServiceHealth",
            "ToolServiceCapabilities",
            "RequestAuditSummary",
        ],
        "errors": tool_error_catalog(),
    }


def run_tool_service_selftest() -> dict[str, Any]:
    from .tool_selftest import SELFTEST_PLUGIN_NAME, _provider_name_for_tool, _write_selftest_plugin

    temp_root = Path(tempfile.mkdtemp(prefix="plugin-tool-service-selftest-"))
    plugins_dir = temp_root / "plugins"
    plugin_dir = plugins_dir / SELFTEST_PLUGIN_NAME
    try:
        _write_selftest_plugin(plugin_dir)
        engine = PluginEngine(
            plugins_dir=plugins_dir,
            sandbox_backend="python_guard",
            require_enforced_sandbox=False,
            production_mode=False,
        )
        try:
            installed = engine.loader.get_installed(SELFTEST_PLUGIN_NAME)
            if installed is not None and installed.status != PluginStatus.ENABLED:
                engine.enable_plugin(SELFTEST_PLUGIN_NAME)
            service = PluginToolService(engine=engine, production_mode=False)
            health = service.health()
            capabilities = service.capabilities()
            openai_tools = service.list_tools(provider="openai", actor_role="model")
            anthropic_tools = service.list_tools(provider="anthropic", actor_role="expert")
            echo_provider_name = _provider_name_for_tool(openai_tools.to_dict(), "echo")
            echo_model_name = str(
                openai_tools.name_mapping.get(echo_provider_name, {}).get("model_tool_name")
                if echo_provider_name
                else "unknown"
            )
            expert_openai = service.list_tools(provider="openai", actor_role="expert")
            bad_return_provider_name = _provider_name_for_tool(expert_openai.to_dict(), "bad_return")
            network_probe_provider_name = _provider_name_for_tool(expert_openai.to_dict(), "network_probe")
            valid = service.invoke_tool_call(
                provider="openai",
                payload={
                    "id": "svc-ok",
                    "type": "function",
                    "function": {"name": echo_provider_name, "arguments": json.dumps({"text": "hello", "repeat": 1})},
                },
                actor_role="model",
                conversation_id="svc-conv",
                request_id="svc-request-ok",
            )
            malformed = service.invoke_tool_call(
                provider="openai",
                payload={
                    "id": "svc-bad-json",
                    "type": "function",
                    "function": {"name": echo_provider_name, "arguments": "{\"text\":"},
                },
                actor_role="model",
                conversation_id="svc-conv",
            )
            unknown = service.invoke_tool_call(
                provider="openai",
                payload={
                    "id": "svc-unknown",
                    "type": "function",
                    "function": {"name": "missing_tool", "arguments": "{}"},
                },
                actor_role="model",
                conversation_id="svc-conv",
            )
            preview = service.invoke_tool_call(
                provider="openai",
                payload={
                    "id": "svc-preview",
                    "type": "function",
                    "function": {"name": echo_provider_name, "arguments": json.dumps({"text": "hello", "repeat": 1})},
                },
                actor_role="model",
                conversation_id="svc-preview",
                execution_mode=ToolExecutionMode.PREVIEW_ONLY,
            )
            dry_run = service.invoke_tool_call(
                provider="openai",
                payload={
                    "id": "svc-dry-run",
                    "type": "function",
                    "function": {"name": echo_provider_name, "arguments": json.dumps({"text": "hello", "repeat": 1})},
                },
                actor_role="model",
                conversation_id="svc-dry-run",
                execution_mode=ToolExecutionMode.DRY_RUN,
            )
            params_error = service.invoke_tool_call(
                provider="openai",
                payload={
                    "id": "svc-param",
                    "type": "function",
                    "function": {"name": echo_provider_name, "arguments": json.dumps({"repeat": 1})},
                },
                actor_role="model",
                conversation_id="svc-conv",
            )
            return_error = service.invoke_tool_call(
                provider="openai",
                payload={
                    "id": "svc-return",
                    "type": "function",
                    "function": {"name": bad_return_provider_name, "arguments": "{}"},
                },
                actor_role="expert",
                conversation_id="svc-expert",
            )
            permission_error = service.invoke_tool_call(
                provider="openai",
                payload={
                    "id": "svc-permission",
                    "type": "function",
                    "function": {"name": network_probe_provider_name, "arguments": "{}"},
                },
                actor_role="expert",
                conversation_id="svc-expert",
            )
            audit_summary = service.get_request_audit_summary("svc-request-ok")
            metrics_before_reset = service.metrics_snapshot()
            conversation_before_reset = service.get_conversation_summary("svc-conv")
            reset = service.reset_session("svc-conv")
            conversation_after_reset = service.get_conversation_summary("svc-conv")
            production_memory_service = PluginToolService(engine=engine, production_mode=True)
            production_memory_health = production_memory_service.health()
            strict_production_service = PluginToolService(
                engine=engine,
                production_mode=True,
                strict_production=True,
            )
            strict_production_health = strict_production_service.health()
            checks = {
                "contract_version_nonempty": bool(TOOL_SERVICE_CONTRACT_VERSION),
                "list_tools_contract_version": openai_tools.contract_version == TOOL_SERVICE_CONTRACT_VERSION,
                "invoke_contract_version": valid.contract_version == TOOL_SERVICE_CONTRACT_VERSION,
                "health_contract_version": health.contract_version == TOOL_SERVICE_CONTRACT_VERSION,
                "capabilities_contract_version": capabilities.contract_version == TOOL_SERVICE_CONTRACT_VERSION,
                "provider_export_contract_version": bool(
                    openai_tools.to_dict().get("contract_version") == TOOL_SERVICE_CONTRACT_VERSION
                ),
                "provider_export_metadata_contract_version": bool(
                    export_provider_tools(
                        service.catalog
                        or LLMToolCatalog.from_engine(
                            engine,
                            actor_role="model",
                            production_mode=False,
                            approved_only=True,
                            include_hidden=True,
                        ),
                        options=ProviderToolExportOptions(
                            provider="generic",
                            actor_role="model",
                            production_mode=False,
                        ),
                    ).get("metadata", {}).get("contract_version")
                    == TOOL_SERVICE_CONTRACT_VERSION
                ),
                "service_health_ok": health.ok,
                "service_health_readiness": health.ready_for_model_calls is True
                and health.ready_for_production is False,
                "service_capabilities_providers": set(capabilities.providers) == {"generic", "openai", "anthropic"},
                "service_governance_store_metadata": capabilities.governance_store.get("store_kind") == "memory"
                and capabilities.governance_store.get("production_recommended") is False,
                "service_confirmation_provider_metadata": capabilities.confirmation_provider.get("provider_kind") == "local"
                and capabilities.confirmation_provider.get("production_recommended") is False,
                "production_memory_not_ready_for_production": production_memory_health.ready_for_model_calls is True
                and production_memory_health.ready_for_production is False
                and "governance_store_not_persistent" in production_memory_health.warnings,
                "strict_production_memory_blocked": strict_production_health.ok is False
                and "governance_store_not_persistent" in strict_production_health.blockers,
                "service_openai_schema": openai_tools.ok and bool(openai_tools.tools),
                "service_anthropic_schema": anthropic_tools.ok and bool(anthropic_tools.tools),
                "service_valid_tool_call": valid.ok,
                "service_invalid_json": malformed.error is not None
                and malformed.error.get("code") == "INVALID_ARGUMENT_JSON",
                "service_unknown_tool": unknown.error is not None and unknown.error.get("code") == "TOOL_NOT_FOUND",
                "service_preview_no_execute": preview.ok
                and bool(preview.envelope.get("result", {}).get("governance_preview")),
                "service_dry_run_no_execute": dry_run.ok
                and bool(dry_run.envelope.get("result", {}).get("governance_preview")),
                "service_params_schema": params_error.error is not None
                and params_error.error.get("code") == "PARAM_SCHEMA_ERROR",
                "service_returns_schema": return_error.error is not None
                and return_error.error.get("code") == "RETURN_SCHEMA_ERROR",
                "service_permission_denied": permission_error.error is not None
                and permission_error.error.get("code") == "PERMISSION_DENIED",
                "service_audit_summary_safe": audit_summary.audit_event_count > 0
                and "hello" not in json.dumps(audit_summary.to_dict(), ensure_ascii=False),
                "service_metrics_increment": metrics_before_reset["tool_calls_total"] >= 6,
                "service_metrics_per_tool": metrics_before_reset["per_tool_calls"].get(echo_model_name, 0) >= 1,
                "service_metrics_denials": metrics_before_reset["per_tool_denials"].get(echo_model_name, 0) >= 1,
                "service_metrics_error_codes": metrics_before_reset["per_error_code"].get("PARAM_SCHEMA_ERROR", 0) >= 1,
                "service_reset_session": conversation_before_reset.get("session_found") is True
                and reset["removed"] is True
                and conversation_after_reset.get("session_found") is False,
            }
            failed = sorted(name for name, ok in checks.items() if not ok)
            return {
                "status": "success" if not failed else "error",
                "contract_version": TOOL_SERVICE_CONTRACT_VERSION,
                "checks": checks,
                "failed_checks": failed,
                "health": health.to_dict(),
                "capabilities": capabilities.to_dict(),
                "metrics": metrics_before_reset,
                "contracts": service_contracts_schema(),
                "generated_at": utc_now(),
            }
        finally:
            engine.stop_all()
    except Exception as exc:
        return {
            "status": "error",
            "contract_version": TOOL_SERVICE_CONTRACT_VERSION,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "failed_checks": ["tool_service_selftest_exception"],
            "generated_at": utc_now(),
        }
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def _engine_for_plugin_dir(plugin_dir: str | Path) -> tuple[PluginEngine, Path]:
    source = Path(plugin_dir).resolve()
    temp_root = Path(tempfile.mkdtemp(prefix="plugin-tool-service-cli-"))
    plugins_dir = temp_root / "plugins"
    target = plugins_dir / source.name
    shutil.copytree(source, target)
    engine = PluginEngine(
        plugins_dir=plugins_dir,
        sandbox_backend="python_guard",
        require_enforced_sandbox=False,
        production_mode=False,
    )
    installed = engine.loader.get_installed(source.name)
    if installed is not None and installed.status != PluginStatus.ENABLED:
        engine.enable_plugin(source.name)
    return engine, temp_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plugin tool service facade")
    parser.add_argument("plugin_dir", nargs="?")
    parser.add_argument("--provider", choices=list(PROVIDER_NAMES), default="openai")
    parser.add_argument("--actor-role", choices=["model", "expert", "admin"], default="model")
    parser.add_argument("--list-tools", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    if args.selftest:
        report = run_tool_service_selftest()
        if args.json_output:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"tool service selftest status={report['status']}")
        return 0 if report.get("status") == "success" else 1
    if args.list_tools and args.plugin_dir:
        engine, temp_root = _engine_for_plugin_dir(args.plugin_dir)
        try:
            service = PluginToolService(engine=engine, production_mode=False)
            response = service.list_tools(provider=args.provider, actor_role=args.actor_role)
            payload = response.to_dict()
            if args.json_output:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(f"provider={response.provider} tools={response.exported_count}")
            return 0 if response.ok else 1
        finally:
            engine.stop_all()
            shutil.rmtree(temp_root, ignore_errors=True)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
