from __future__ import annotations

# TECH DEBT: 此适配器将新 PluginToolService 包装为旧 ToolManager 风格接口，
# 供 model_loop_adapter.py 等旧调用者使用。待所有调用者迁移到 PluginToolService 直接调用后可移除。
# 主要调用者: modules/plugin_system/model_loop_adapter.py

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .audit import AuditLogger, NullAuditLogger, new_request_id
from .engine import PluginEngine
from .models import PluginStatus
from .provider_tools import PROVIDER_NAMES, normalize_provider
from .tool_contracts import TOOL_SERVICE_CONTRACT_VERSION, utc_now
from .tool_governance import ToolExecutionMode
from .tool_service import PluginToolService


class PluginToolManagerAdapter:
    """Compatibility adapter for legacy tool-manager style callers.

    Execution intentionally stays behind PluginToolService so provider parsing,
    governance, schema validation, permissions, Gateway access, result
    sanitization, and audit all remain on the same model-facing contract.
    """

    def __init__(
        self,
        service: PluginToolService | None = None,
        *,
        engine: PluginEngine | None = None,
        production_mode: bool = True,
        audit_logger: AuditLogger | NullAuditLogger | None = None,
    ) -> None:
        if service is None:
            service = PluginToolService(
                engine=engine,
                production_mode=production_mode,
                audit_sink=audit_logger,
            )
        self.service = service
        self.audit_logger: AuditLogger | NullAuditLogger = (
            audit_logger
            or getattr(service, "audit_logger", None)
            or NullAuditLogger()
        )
        self._last_exports: dict[str, dict[str, Any]] = {}

    def list_tools(
        self,
        *,
        provider: str = "generic",
        actor_role: str = "model",
        conversation_id: str | None = None,
        include_hidden: bool = False,
        max_tools: int = 128,
        max_total_schema_bytes: int = 256 * 1024,
        request_id: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        request_id = request_id or new_request_id()
        provider = normalize_provider(provider)
        response = self.service.list_tools(
            provider=provider,
            actor_role=actor_role,
            conversation_id=conversation_id,
            include_hidden=include_hidden,
            max_tools=max_tools,
            max_total_schema_bytes=max_total_schema_bytes,
            request_id=request_id,
        )
        payload = response.to_dict()
        payload.update(
            {
                "status": "success" if response.ok else "error",
                "adapter": {
                    "name": self.__class__.__name__,
                    "contract_version": TOOL_SERVICE_CONTRACT_VERSION,
                    "delegates_to": "PluginToolService.list_tools",
                },
                "tool_definitions": payload["tools"],
                "internal_mapping": payload["name_mapping"],
                "name_mapping": _enrich_name_mapping(
                    payload["name_mapping"],
                    self._visibility_metadata(
                        actor_role=actor_role,
                        production_mode=getattr(self.service, "production_mode", False),
                        include_hidden=True,
                    ),
                ),
                "visibility_metadata": self._visibility_metadata(
                    actor_role=actor_role,
                    production_mode=getattr(self.service, "production_mode", False),
                    include_hidden=include_hidden,
                ),
                "visibility": {
                    "include_hidden": include_hidden,
                    "hidden_count": response.hidden_count,
                    "exported_count": response.exported_count,
                },
            }
        )
        cache_key = _cache_key(provider, actor_role, include_hidden)
        self._last_exports[cache_key] = payload
        self._audit(
            "plugin.tool_manager_adapter_listed",
            "success" if response.ok else "error",
            request_id=request_id,
            details={
                "provider": provider,
                "actor_role": actor_role,
                "conversation_id": conversation_id,
                "include_hidden": include_hidden,
                "exported_count": response.exported_count,
                "hidden_count": response.hidden_count,
                "warnings_count": len(response.warnings),
                "decision": "allow" if response.ok else "deny",
            },
            decision="allow" if response.ok else "deny",
            reason="tools_listed" if response.ok else "list_failed",
        )
        return payload

    def get_tool_definitions(self, **kwargs: Any) -> dict[str, Any]:
        return self.list_tools(**kwargs)

    def execute_tool(
        self,
        *,
        provider: str,
        payload: dict[str, Any],
        actor_role: str,
        conversation_id: str | None,
        request_id: str | None = None,
        execution_mode: str = ToolExecutionMode.EXECUTE,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        request_id = request_id or new_request_id()
        provider = normalize_provider(provider)
        summary = _safe_payload_summary(provider, payload)
        self._audit(
            "plugin.tool_manager_adapter_execute_requested",
            "success",
            request_id=request_id,
            details={
                **summary,
                "provider": provider,
                "actor_role": actor_role,
                "conversation_id": conversation_id,
                "execution_mode": execution_mode,
                "has_confirmation_token": bool(confirmation_token),
                "has_idempotency_key": bool(idempotency_key),
                "decision": "requested",
            },
            decision="requested",
            reason="execute_requested",
        )
        response = self.service.invoke_tool_call(
            provider=provider,
            payload=payload,
            actor_role=actor_role,
            conversation_id=conversation_id,
            request_id=request_id,
            execution_mode=execution_mode,
            confirmation_token=confirmation_token,
            idempotency_key=idempotency_key,
        )
        result = response.to_dict()
        result.update(
            {
                "status": "success" if response.ok else "error",
                "adapter": {
                    "name": self.__class__.__name__,
                    "contract_version": TOOL_SERVICE_CONTRACT_VERSION,
                    "delegates_to": "PluginToolService.invoke_tool_call",
                },
                "provider_safe_message": result.get("response"),
            }
        )
        error_code = response.error.get("code") if response.error else None
        event = (
            "plugin.tool_manager_adapter_execute_completed"
            if response.ok
            else "plugin.tool_manager_adapter_execute_denied"
        )
        self._audit(
            event,
            "success" if response.ok else "error",
            request_id=response.request_id,
            plugin=response.plugin_id,
            action=response.tool_name or response.model_tool_name,
            details={
                "provider": provider,
                "actor_role": actor_role,
                "conversation_id": conversation_id,
                "provider_call_id": response.provider_call_id,
                "model_tool_name": response.model_tool_name,
                "plugin_id": response.plugin_id,
                "plugin_version": response.plugin_version,
                "tool_name": response.tool_name,
                "ok": response.ok,
                "error_code": error_code,
                "sanitized": bool(response.envelope.get("sanitized")),
                "truncated": bool(response.envelope.get("truncated")),
                "decision": "allow" if response.ok else "deny",
            },
            plugin_id=response.plugin_id,
            plugin_version=response.plugin_version,
            decision="allow" if response.ok else "deny",
            reason="execute_completed" if response.ok else error_code or "execute_denied",
        )
        return result

    def can_handle_tool_name(self, tool_name: str) -> bool:
        return self.resolve_tool_name(tool_name) is not None

    def resolve_tool_name(self, tool_name: str) -> dict[str, Any] | None:
        target = str(tool_name or "").strip()
        if not target:
            return None
        for export in self._known_exports():
            mapping = _resolve_from_export(target, export)
            if mapping is not None:
                return mapping
        return None

    def status(self) -> dict[str, Any]:
        health = self.service.health()
        capabilities = self.service.capabilities()
        health_payload = health.to_dict()
        warnings = sorted(
            {
                *[str(item) for item in health.warnings],
                *[str(item) for item in health.degraded_capabilities],
            }
        )
        return {
            "status": "success" if health.ready_for_model_calls else "degraded",
            "ok": health.ok,
            "adapter": self.__class__.__name__,
            "contract_version": TOOL_SERVICE_CONTRACT_VERSION,
            "delegates_to": "PluginToolService",
            "provider_supported": list(PROVIDER_NAMES),
            "service_health": {
                "ok": health.ok,
                "ready_for_model_calls": health.ready_for_model_calls,
                "ready_for_production": health.ready_for_production,
                "blockers": list(health.blockers),
                "warnings": list(health.warnings),
                "degraded_capabilities": list(health.degraded_capabilities),
            },
            "warnings": warnings,
            "ready_for_model_calls": health.ready_for_model_calls,
            "ready_for_production": health.ready_for_production,
            "health": health_payload,
            "capabilities": capabilities.to_dict(),
            "cached_exports": len(self._last_exports),
            "generated_at": utc_now(),
        }

    def _known_exports(self) -> list[dict[str, Any]]:
        if self._last_exports:
            return list(self._last_exports.values())
        exports: list[dict[str, Any]] = []
        for provider in PROVIDER_NAMES:
            for actor_role in ("model", "expert", "admin"):
                try:
                    exports.append(
                        self.list_tools(
                            provider=provider,
                            actor_role=actor_role,
                            include_hidden=True,
                            max_tools=512,
                        )
                    )
                except Exception:
                    continue
        return exports

    def _visibility_metadata(
        self,
        *,
        actor_role: str,
        production_mode: bool,
        include_hidden: bool,
    ) -> dict[str, Any]:
        engine = getattr(self.service, "engine", None)
        catalog = getattr(self.service, "catalog", None)
        if catalog is None and engine is not None:
            try:
                from .llm_tools import LLMToolCatalog

                catalog = LLMToolCatalog.from_engine(
                    engine,
                    actor_role=actor_role,
                    production_mode=production_mode,
                    approved_only=True,
                    include_hidden=include_hidden,
                    audit_logger=self.audit_logger,
                )
            except Exception:
                return {}
        specs = getattr(catalog, "specs", None)
        if not isinstance(specs, list):
            return {}
        return {
            spec.name: {
                "plugin_id": spec.plugin_id,
                "plugin_version": spec.plugin_version,
                "tool_name": spec.tool_name,
                "model_tool_name": spec.name,
                "exposure": spec.exposure,
                "risk_level": spec.risk_level,
                "hidden": bool(spec.hidden),
                "status": "hidden" if spec.hidden else "exported",
                "required_permissions": list(spec.required_permissions),
            }
            for spec in specs
        }

    def _audit(
        self,
        event: str,
        result: str,
        *,
        request_id: str,
        details: dict[str, Any],
        plugin: str | None = None,
        action: str | None = None,
        plugin_id: str | None = None,
        plugin_version: str | None = None,
        decision: str | None = None,
        reason: str | None = None,
    ) -> None:
        self.audit_logger.record(
            event,
            result,
            request_id=request_id,
            plugin=plugin,
            action=action,
            details={key: value for key, value in details.items() if value is not None},
            plugin_id=plugin_id,
            plugin_version=plugin_version,
            decision=decision,
            reason=reason,
        )


def run_tool_manager_adapter_selftest() -> dict[str, Any]:
    from .tool_selftest import SELFTEST_PLUGIN_NAME, _provider_name_for_tool, _write_selftest_plugin

    temp_root = Path(tempfile.mkdtemp(prefix="plugin-tool-manager-adapter-"))
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
            adapter = PluginToolManagerAdapter(
                service=PluginToolService(engine=engine, production_mode=False)
            )
            model_tools = adapter.list_tools(provider="openai", actor_role="model")
            expert_tools = adapter.list_tools(provider="openai", actor_role="expert")
            admin_tools = adapter.list_tools(provider="openai", actor_role="admin")
            echo_name = _provider_name_for_tool(model_tools, "echo")
            network_model = _provider_name_for_tool(model_tools, "network_allowed")
            network_expert = _provider_name_for_tool(expert_tools, "network_allowed")
            network_admin = _provider_name_for_tool(admin_tools, "network_allowed")
            success = adapter.execute_tool(
                provider="openai",
                payload={
                    "id": "adapter-ok",
                    "type": "function",
                    "function": {
                        "name": echo_name,
                        "arguments": json.dumps({"text": "hello", "repeat": 1}),
                    },
                },
                actor_role="model",
                conversation_id="adapter-selftest",
                request_id="adapter-request-ok",
            )
            params_error = adapter.execute_tool(
                provider="openai",
                payload={
                    "id": "adapter-param",
                    "type": "function",
                    "function": {"name": echo_name, "arguments": json.dumps({"repeat": 1})},
                },
                actor_role="model",
                conversation_id="adapter-selftest",
            )
            hidden_denied = adapter.execute_tool(
                provider="openai",
                payload={
                    "id": "adapter-hidden",
                    "type": "function",
                    "function": {"name": network_expert, "arguments": json.dumps({"url": "safe-token"})},
                },
                actor_role="model",
                conversation_id="adapter-selftest",
            )
            resolved = adapter.resolve_tool_name(str(echo_name))
            resolved_plugin_id = resolved.get("plugin_id") if resolved is not None else None
            resolved_tool_name = resolved.get("tool_name") if resolved is not None else None
            events = {record.event for record in engine.audit_logger.read_records()}
            audit_text = json.dumps(
                [record.details for record in engine.audit_logger.read_records()],
                ensure_ascii=False,
                sort_keys=True,
            )
            checks = {
                "model_list_low_risk_tool": bool(echo_name),
                "model_high_risk_hidden": network_model is None,
                "expert_high_risk_visible": bool(network_expert),
                "admin_high_risk_visible": bool(network_admin),
                "execute_delegates_success": success.get("ok") is True
                and success.get("adapter", {}).get("delegates_to") == "PluginToolService.invoke_tool_call",
                "params_error_safe": params_error.get("error", {}).get("code") == "PARAM_SCHEMA_ERROR",
                "hidden_tool_not_executable_by_model": hidden_denied.get("error", {}).get("code")
                in {"TOOL_NOT_FOUND", "TOOL_NOT_VISIBLE"},
                "can_handle_provider_name": adapter.can_handle_tool_name(str(echo_name)),
                "resolve_tool_name": bool(resolved)
                and resolved_plugin_id == SELFTEST_PLUGIN_NAME
                and resolved_tool_name == "echo",
                "status_ready": adapter.status().get("ready_for_model_calls") is True,
                "audit_listed": "plugin.tool_manager_adapter_listed" in events,
                "audit_requested": "plugin.tool_manager_adapter_execute_requested" in events,
                "audit_completed": "plugin.tool_manager_adapter_execute_completed" in events,
                "audit_denied": "plugin.tool_manager_adapter_execute_denied" in events,
                "audit_does_not_log_full_args": "hello" not in audit_text,
            }
            failed = sorted(name for name, ok in checks.items() if not ok)
            return {
                "status": "success" if not failed else "error",
                "contract_version": TOOL_SERVICE_CONTRACT_VERSION,
                "checks": checks,
                "failed_checks": failed,
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
            "failed_checks": ["tool_manager_adapter_selftest_exception"],
            "generated_at": utc_now(),
        }
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def _cache_key(provider: str, actor_role: str, include_hidden: bool) -> str:
    return f"{provider}:{actor_role}:{str(include_hidden).lower()}"


def _resolve_from_export(tool_name: str, export: dict[str, Any]) -> dict[str, Any] | None:
    raw_mapping = export.get("name_mapping")
    if not isinstance(raw_mapping, dict):
        return None
    definitions = _definitions_by_provider_tool_name(export)
    visibility = export.get("visibility_metadata")
    visibility = visibility if isinstance(visibility, dict) else {}
    for provider_name, item in raw_mapping.items():
        if not isinstance(item, dict):
            continue
        candidates = {
            str(provider_name),
            str(item.get("provider_tool_name") or ""),
            str(item.get("model_tool_name") or ""),
            f"{item.get('plugin_id')}.{item.get('tool_name')}",
        }
        if tool_name in candidates:
            provider_tool_name = str(item.get("provider_tool_name") or provider_name)
            definition_metadata = definitions.get(provider_tool_name, {})
            visibility_metadata = visibility.get(str(item.get("model_tool_name") or ""))
            visibility_metadata = visibility_metadata if isinstance(visibility_metadata, dict) else {}
            return {
                "provider": item.get("provider") or export.get("provider"),
                "provider_tool_name": provider_tool_name,
                "model_tool_name": item.get("model_tool_name"),
                "plugin_id": item.get("plugin_id"),
                "plugin_version": item.get("plugin_version"),
                "tool_name": item.get("tool_name"),
                "exposure": (
                    visibility_metadata.get("exposure")
                    or definition_metadata.get("exposure")
                    or item.get("exposure")
                    or "unknown"
                ),
                "risk_level": (
                    visibility_metadata.get("risk_level")
                    or definition_metadata.get("risk_level")
                    or item.get("risk_level")
                    or "unknown"
                ),
                "status": visibility_metadata.get("status") or definition_metadata.get("status") or "exported",
            }
    return None


def _definitions_by_provider_tool_name(export: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tools = export.get("tools")
    if not isinstance(tools, list):
        return {}
    definitions: dict[str, dict[str, Any]] = {}
    for definition in tools:
        if not isinstance(definition, dict):
            continue
        metadata = definition.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        name = definition.get("name")
        if not name and isinstance(definition.get("function"), dict):
            name = definition["function"].get("name")
        if isinstance(name, str) and name:
            definitions[name] = {
                "exposure": metadata.get("exposure"),
                "risk_level": metadata.get("risk_level"),
                "status": metadata.get("status") or "exported",
            }
    return definitions


def _enrich_name_mapping(
    name_mapping: dict[str, Any],
    visibility_metadata: dict[str, Any],
) -> dict[str, Any]:
    enriched: dict[str, Any] = {}
    for provider_name, item in name_mapping.items():
        if not isinstance(item, dict):
            continue
        model_tool_name = str(item.get("model_tool_name") or "")
        metadata = visibility_metadata.get(model_tool_name)
        metadata = metadata if isinstance(metadata, dict) else {}
        enriched[str(provider_name)] = {
            **item,
            "exposure": metadata.get("exposure", item.get("exposure", "unknown")),
            "risk_level": metadata.get("risk_level", item.get("risk_level", "unknown")),
            "hidden": metadata.get("hidden", item.get("hidden", False)),
            "status": metadata.get("status", item.get("status", "exported")),
        }
    return enriched


def _safe_payload_summary(provider: str, payload: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "payload_type": type(payload).__name__,
        "payload_keys": sorted(str(key) for key in payload) if isinstance(payload, dict) else [],
    }
    if not isinstance(payload, dict):
        return summary
    summary["provider_call_id"] = payload.get("id") or payload.get("call_id") or payload.get("tool_call_id")
    provider_tool_name = None
    raw_args: Any = None
    if provider == "openai":
        function = payload.get("function")
        if isinstance(function, dict):
            provider_tool_name = function.get("name")
            raw_args = function.get("arguments")
        elif isinstance(payload.get("function_call"), dict):
            function_call = payload["function_call"]
            provider_tool_name = function_call.get("name")
            raw_args = function_call.get("arguments")
        else:
            provider_tool_name = payload.get("name")
            raw_args = payload.get("arguments")
    elif provider == "anthropic":
        provider_tool_name = payload.get("name")
        raw_args = payload.get("input")
    else:
        provider_tool_name = payload.get("name")
        raw_args = payload.get("arguments", payload.get("input"))
    summary["provider_tool_name"] = str(provider_tool_name) if provider_tool_name is not None else None
    summary["argument_keys"] = _argument_keys(raw_args)
    summary["raw_args_size_bytes"] = _arg_size(raw_args)
    return summary


def _argument_keys(raw_args: Any) -> list[str]:
    if isinstance(raw_args, dict):
        return sorted(str(key) for key in raw_args)
    if not isinstance(raw_args, str):
        return []
    try:
        parsed = json.loads(raw_args)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, dict):
        return sorted(str(key) for key in parsed)
    return []


def _arg_size(raw_args: Any) -> int | None:
    if raw_args is None:
        return None
    try:
        return len(json.dumps(raw_args, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plugin ToolManager compatibility adapter")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    if not args.selftest:
        parser.print_help()
        return 2
    report = run_tool_manager_adapter_selftest()
    if args.json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"tool manager adapter selftest status={report['status']}")
    return 0 if report.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
