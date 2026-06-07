from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from .audit import AuditLogger, AuditRecord, NullAuditLogger, verify_audit_log
from .engine import PluginEngine
from .llm_tools import LLMToolCatalog, LLMToolExposure, LLMToolRiskLevel
from .loader import GOVERNANCE_FILE, MANIFEST_FILE, PACKAGE_LOCK_FILE
from .models import InstalledPlugin, PermissionName, PluginMetadata, PluginStatus, TrustLevel
from .policy import PluginPolicy
from .provider_tools import PROVIDER_NAMES
from .sandbox_backend import create_sandbox_backend
from .signing import LEGACY_SIGNATURE_ALGORITHM, SIGNATURE_ALGORITHM
from .tool_contracts import TOOL_SERVICE_CONTRACT_VERSION, utc_now
from .tool_governance import governance_store_metadata, tool_risk_decision
from .tool_service import PluginToolService
from .evidence_adapters import adapters_status
from .integration_contract import run_integration_contract_check


UNKNOWN = "unknown"
UNAVAILABLE = "unavailable"


class PluginSystemStatusProvider:
    """Conservative plugin platform status snapshot provider."""

    def __init__(
        self,
        *,
        engine: PluginEngine | None = None,
        service: PluginToolService | None = None,
        plugins_dir: str | Path | None = None,
        audit_logger: AuditLogger | NullAuditLogger | None = None,
        production_mode: bool | None = None,
        scanner_configured: bool | None = None,
        registry_signed: bool | None = None,
        external_anchor_configured: bool | None = None,
    ) -> None:
        self.engine = engine or getattr(service, "engine", None)
        self.service = service or (
            PluginToolService(engine=engine, production_mode=bool(production_mode))
            if engine is not None
            else None
        )
        raw_plugins_dir: str | Path
        if plugins_dir is not None:
            raw_plugins_dir = plugins_dir
        elif self.engine is not None:
            raw_plugins_dir = getattr(self.engine, "plugins_dir", "data/plugins")
        else:
            raw_plugins_dir = "data/plugins"
        self.plugins_dir = Path(raw_plugins_dir).resolve()
        self.audit_logger = (
            audit_logger
            or getattr(self.service, "audit_logger", None)
            or getattr(self.engine, "audit_logger", None)
            or NullAuditLogger()
        )
        self.production_mode = (
            bool(getattr(self.engine, "production_mode", False))
            if production_mode is None
            else bool(production_mode)
        )
        self.scanner_configured = scanner_configured
        self.registry_signed = registry_signed
        self.external_anchor_configured = external_anchor_configured

    def get_platform_status(self) -> dict[str, Any]:
        backend_name = str(getattr(self.engine, "sandbox_backend", "auto") if self.engine else "auto")
        sandbox_report = create_sandbox_backend(128, 2, requested=backend_name).report
        platform_name = sys.platform
        is_windows = platform_name.startswith("win") or platform.system().lower().startswith("win")
        bwrap_available = bool(sandbox_report.name == "bubblewrap" and sandbox_report.enforced)
        enforced = bool(sandbox_report.enforced and not sandbox_report.missing_capabilities())
        return {
            "platform": platform_name,
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "production_mode": self.production_mode,
            "sandbox_backend": sandbox_report.name,
            "sandbox_enforced": enforced,
            "strong_sandbox_ready": bool(self.production_mode and enforced and not is_windows),
            "sandbox_evidence_status": UNAVAILABLE,
            "sandbox_evidence_source": UNAVAILABLE,
            "sandbox_evidence_warnings": [
                "sandbox_evidence_not_provided",
                *(
                    ["windows_job_object_resource_limits_only"]
                    if is_windows
                    else []
                ),
            ],
            "sandbox_capabilities": dict(sandbox_report.capabilities),
            "sandbox_warnings": list(sandbox_report.warnings),
            "bwrap_available": bwrap_available,
            "windows_job_object_warning": (
                "Windows Job Object is resource limiting only, not a strong filesystem/network/syscall sandbox."
                if is_windows
                else None
            ),
            "production_ready_for_third_party_plugins": bool(
                self.production_mode and enforced and not is_windows
            ),
            "generated_at": utc_now(),
        }

    def get_plugin_status(self) -> dict[str, Any]:
        installed = self._installed_plugins()
        counts = {status.value: 0 for status in PluginStatus}
        for plugin in installed:
            counts[plugin.status.value] = counts.get(plugin.status.value, 0) + 1
        raw_examples = _raw_plugin_dirs(self.plugins_dir)
        not_installed_examples = _not_installed_raw_plugins(self.plugins_dir)
        return {
            "installed_plugins": len(installed),
            "enabled_plugins": counts.get(PluginStatus.ENABLED.value, 0),
            "running_plugins": len(getattr(self.engine, "sandboxes", {}) or {}),
            "disabled_plugins": counts.get(PluginStatus.DISABLED.value, 0),
            "quarantined_plugins": counts.get(PluginStatus.QUARANTINED.value, 0),
            "revoked_plugins": counts.get(PluginStatus.REVOKED.value, 0),
            "raw_example_plugins": len(raw_examples),
            "raw_example_plugin_names": raw_examples,
            "not_installed_examples": len(not_installed_examples),
            "not_installed_example_names": not_installed_examples,
            "by_status": counts,
            "generated_at": utc_now(),
        }

    def get_tool_status(self) -> dict[str, Any]:
        installed = self._installed_plugins()
        total = 0
        model_visible = 0
        expert_only = 0
        admin_only = 0
        hidden = 0
        high_risk = 0
        confirmation_required = 0
        provider_exportable_tools: dict[str, int | str] = {provider: UNAVAILABLE for provider in PROVIDER_NAMES}
        for plugin in installed:
            total += len(plugin.metadata.tool_entries())
        if self.engine is not None:
            for actor_role in ("model", "expert", "admin"):
                catalog = LLMToolCatalog.from_engine(
                    self.engine,
                    actor_role=actor_role,
                    production_mode=self.production_mode,
                    approved_only=True,
                    include_hidden=True,
                    audit_logger=self.audit_logger,
                )
                if actor_role == "model":
                    model_visible = sum(1 for spec in catalog.specs if not spec.hidden)
                    hidden = sum(1 for spec in catalog.specs if spec.hidden)
                    high_risk = sum(
                        1 for spec in catalog.specs if spec.risk_level in {LLMToolRiskLevel.HIGH, LLMToolRiskLevel.CRITICAL}
                    )
                    confirmation_required = sum(
                        1 for spec in catalog.specs if tool_risk_decision(spec).requires_confirmation
                    )
                elif actor_role == "expert":
                    expert_only = sum(
                        1
                        for spec in catalog.specs
                        if spec.exposure == LLMToolExposure.EXPERT_ONLY and not spec.hidden
                    )
                else:
                    admin_only = sum(
                        1
                        for spec in catalog.specs
                        if spec.exposure == LLMToolExposure.ADMIN_ONLY and not spec.hidden
                    )
        if self.service is not None:
            for provider in PROVIDER_NAMES:
                try:
                    response = self.service.list_tools(provider=provider, actor_role="model", include_hidden=False)
                    provider_exportable_tools[provider] = response.exported_count if response.ok else 0
                except Exception:
                    provider_exportable_tools[provider] = UNAVAILABLE
        return {
            "total_plugin_tools": total,
            "model_visible_tools": model_visible,
            "expert_only_tools": expert_only,
            "admin_only_tools": admin_only,
            "hidden_tools": hidden,
            "high_risk_tools": high_risk,
            "provider_exportable_tools": provider_exportable_tools,
            "high_risk_tools_requiring_confirmation": confirmation_required,
            "generated_at": utc_now(),
        }

    def get_security_status(self) -> dict[str, Any]:
        installed = self._installed_plugins()
        pending = 0
        denied_permissions = 0
        high_risk_tools_requiring_confirmation = 0
        for plugin in installed:
            review = plugin.permission_review or {}
            pending += 1 if review.get("required") else 0
            denied_permissions += len(review.get("denied_permissions") or [])
            for tool_name in plugin.metadata.tool_entries():
                permissions = plugin.metadata.tool_requested_permissions(tool_name)
                if permissions & _high_risk_permissions():
                    high_risk_tools_requiring_confirmation += 1
        platform_status = self.get_platform_status()
        policy = PluginPolicy()
        return {
            "pending_permission_approvals": pending,
            "denied_permissions_count": denied_permissions,
            "high_risk_tools_requiring_confirmation": high_risk_tools_requiring_confirmation,
            "sandbox_backend": platform_status["sandbox_backend"],
            "sandbox_enforced": platform_status["sandbox_enforced"],
            "platform": platform_status["platform"],
            "bwrap_available": platform_status["bwrap_available"],
            "windows_job_object_warning": platform_status["windows_job_object_warning"],
            "production_ready_for_third_party_plugins": platform_status["production_ready_for_third_party_plugins"],
            "signature_required": bool(policy.third_party_require_signature),
            "ed25519_required": SIGNATURE_ALGORITHM,
            "hmac_production_trust": False,
            "legacy_hmac_algorithm": LEGACY_SIGNATURE_ALGORITHM,
            "generated_at": utc_now(),
        }

    def get_audit_status(self) -> dict[str, Any]:
        records = self._audit_records()
        log_path = getattr(self.audit_logger, "log_path", None)
        verify_status: dict[str, Any] = {"status": UNAVAILABLE}
        hash_chain_enabled = False
        if log_path is not None:
            try:
                verify_status = verify_audit_log(Path(log_path))
                hash_chain_enabled = verify_status.get("status") == "success"
            except Exception as exc:
                verify_status = {"status": "error", "reason": str(exc), "error_type": type(exc).__name__}
        checkpoint_status = verify_status.get("checkpoint") if isinstance(verify_status, dict) else None
        checkpoint_enabled = bool(isinstance(checkpoint_status, dict) and checkpoint_status.get("status") == "success")
        return {
            "audit_log_path": str(log_path) if log_path is not None else UNAVAILABLE,
            "hash_chain_enabled": hash_chain_enabled,
            "checkpoint_enabled": checkpoint_enabled,
            "checkpoint_kind": "external" if self.external_anchor_configured else "local_or_none",
            "external_anchor_configured": bool(self.external_anchor_configured),
            "local_checkpoint_is_immutable": False,
            "last_audit_event_time": records[-1].created_at if records else None,
            "record_count": len(records),
            "verify_status": _safe_verify_status(verify_status),
            "generated_at": utc_now(),
        }

    def get_supply_chain_status(self) -> dict[str, Any]:
        installed = self._installed_plugins()
        third_party = [plugin for plugin in installed if plugin.metadata.runtime.trust == TrustLevel.THIRD_PARTY]
        signed = 0
        sbom_present = 0
        lockfile_present = 0
        legacy_local_layout = 0
        for plugin in installed:
            plugin_dir = Path(plugin.path)
            manifest = _read_json(plugin_dir / MANIFEST_FILE)
            if isinstance(manifest.get("signature"), dict):
                signed += 1
            if (plugin_dir / "sbom.cdx.json").exists():
                sbom_present += 1
            if (plugin_dir / PACKAGE_LOCK_FILE).exists():
                lockfile_present += 1
            if (plugin_dir / "metadata.json").exists() and (plugin_dir / "plugin.py").exists():
                legacy_local_layout += 1
        governance = _read_json(self.plugins_dir / GOVERNANCE_FILE)
        revoked_versions = governance.get("revoked_plugin_versions") if isinstance(governance, dict) else []
        return {
            "signature_required": True,
            "signature_algorithm_required": SIGNATURE_ALGORITHM,
            "hmac_production_trust": False,
            "registry_signed": _unknown_bool(self.registry_signed),
            "sbom_required": True,
            "scanner_configured": _unknown_bool(self.scanner_configured),
            "offline_scanner_is_real_scanner": False,
            "installed_plugins_with_signature": signed,
            "third_party_plugins": len(third_party),
            "plugins_with_sbom": sbom_present,
            "plugins_with_lockfile": lockfile_present,
            "legacy_local_layout_count": legacy_local_layout,
            "revoked_keys_count": _revoked_key_count(governance),
            "revoked_versions_count": len(revoked_versions) if isinstance(revoked_versions, list) else 0,
            "generated_at": utc_now(),
        }

    def get_llm_tool_status(self) -> dict[str, Any]:
        if self.service is None:
            return {
                "contract_version": TOOL_SERVICE_CONTRACT_VERSION,
                "supported_providers": list(PROVIDER_NAMES),
                "ready_for_model_calls": False,
                "ready_for_production": False,
                "governance_store_kind": UNAVAILABLE,
                "confirmation_provider_kind": UNAVAILABLE,
                "model_visible_tool_count": 0,
                "generated_at": utc_now(),
            }
        health = self.service.health()
        capabilities = self.service.capabilities()
        model_visible = 0
        if self.engine is not None:
            response = self.service.list_tools(provider="generic", actor_role="model", include_hidden=False)
            model_visible = response.exported_count if response.ok else 0
        metrics = self.service.metrics_snapshot()
        return {
            "contract_version": TOOL_SERVICE_CONTRACT_VERSION,
            "supported_providers": list(PROVIDER_NAMES),
            "ready_for_model_calls": health.ready_for_model_calls,
            "ready_for_production": health.ready_for_production,
            "governance_store_kind": capabilities.governance_store.get("store_kind", UNKNOWN),
            "confirmation_provider_kind": capabilities.confirmation_provider.get("provider_kind", UNKNOWN),
            "model_visible_tool_count": model_visible,
            "health": health.to_dict(),
            "capabilities": capabilities.to_dict(),
            "metrics": metrics,
            "generated_at": utc_now(),
        }

    def get_governance_status(self) -> dict[str, Any]:
        service = self.service
        metrics = service.metrics_snapshot() if service is not None else {}
        store = (
            governance_store_metadata(getattr(service, "governance_store", None))
            if service is not None
            else {"store_kind": UNAVAILABLE}
        )
        confirmation_provider = {}
        if service is not None:
            capabilities = service.capabilities()
            confirmation_provider = dict(capabilities.confirmation_provider)
        return {
            "tool_calls_total": int(metrics.get("tool_calls_total", 0)),
            "tool_calls_allowed": int(metrics.get("tool_calls_allowed", 0)),
            "tool_calls_denied": int(metrics.get("tool_calls_denied", 0)),
            "confirmation_required_total": int(metrics.get("confirmation_required_total", 0)),
            "budget_exceeded_total": int(metrics.get("budget_exceeded_total", 0)),
            "rate_limited_total": int(metrics.get("rate_limited_total", 0)),
            "per_tool_calls": dict(metrics.get("per_tool_calls", {})),
            "per_error_code": dict(metrics.get("per_error_code", {})),
            "permission_denied_total": int(metrics.get("permission_denied_total", 0)),
            "params_schema_error_total": int(metrics.get("params_schema_error_total", 0)),
            "return_schema_error_total": int(metrics.get("return_schema_error_total", 0)),
            "duplicate_total": int(metrics.get("duplicate_total", 0)),
            "governance_store_kind": store.get("store_kind", UNAVAILABLE),
            "governance_store_persistent": store.get("persistent", UNAVAILABLE),
            "governance_store_multi_process_safe": store.get("multi_process_safe", UNAVAILABLE),
            "governance_store_multi_instance_safe": store.get("multi_instance_safe", UNAVAILABLE),
            "governance_store_production_recommended": store.get("production_recommended", UNAVAILABLE),
            "governance_store": store,
            "confirmation_provider_kind": confirmation_provider.get("provider_kind", UNAVAILABLE),
            "approval_provider_kind": confirmation_provider.get("provider_kind", UNAVAILABLE),
            "confirmation_provider_production_recommended": confirmation_provider.get("production_recommended", UNAVAILABLE),
            "approval_provider_ready_for_production": bool(confirmation_provider.get("production_recommended") is True),
            "governance_store_ready_for_production": bool(
                store.get("persistent") is True
                and store.get("multi_instance_safe") is True
                and store.get("production_recommended") is True
            ),
            "generated_at": utc_now(),
        }

    def get_full_status(self) -> dict[str, Any]:
        return {
            "status": "success",
            "contract_version": TOOL_SERVICE_CONTRACT_VERSION,
            "platform": self.get_platform_status(),
            "plugins": self.get_plugin_status(),
            "tools": self.get_tool_status(),
            "security": self.get_security_status(),
            "governance": self.get_governance_status(),
            "audit": self.get_audit_status(),
            "supply_chain": self.get_supply_chain_status(),
            "llm_tool_service": self.get_llm_tool_status(),
            "template_status": self.get_template_status(),
            "evidence_adapters": self.get_evidence_adapter_status(),
            "integration_contract": self.get_integration_contract_status(),
            "generated_at": utc_now(),
        }

    def get_evidence_adapter_status(self) -> dict[str, Any]:
        status = adapters_status()
        supported = set(status.get("supported_adapters", []))
        return {
            "status": status.get("status"),
            "schema_version": status.get("schema_version"),
            "scanner_adapters_supported": status.get("scanner_formats", []),
            "signature_evidence_supported": "signature" in supported,
            "registry_evidence_supported": "registry" in supported,
            "sandbox_evidence_supported": "sandbox" in supported,
            "audit_anchor_evidence_supported": "audit_anchor" in supported,
            "offline_reports_are_production_evidence": False,
            "example_files_are_production_evidence": False,
            "generated_at": utc_now(),
        }

    def get_integration_contract_status(self) -> dict[str, Any]:
        report = run_integration_contract_check()
        return {
            "status": report.get("status"),
            "integration_contract_status": report.get("status"),
            "model_loop_adapter_available": True,
            "tool_manager_adapter_available": True,
            "requirements_count": len(report.get("requirements", [])),
            "forbidden_patterns_count": len(report.get("forbidden_patterns", [])),
            "generated_at": utc_now(),
        }

    def get_template_status(self) -> dict[str, Any]:
        templates_dir = Path(__file__).resolve().parent / "templates"
        templates: list[dict[str, Any]] = []
        if templates_dir.exists() and templates_dir.is_dir():
            for child in sorted(templates_dir.iterdir()):
                if not child.is_dir():
                    continue
                templates.append(_template_status(child))
        return {
            "templates_dir": str(templates_dir),
            "total_templates": len(templates),
            "available_templates": [item["name"] for item in templates],
            "business_templates_count": sum(1 for item in templates if item.get("template_kind") == "business"),
            "production_package_template_available": any(item["name"] == "production_plugin_package" for item in templates),
            "templates": templates,
            "example_scanner_reports_are_real": False,
            "example_signatures_are_valid": False,
            "generated_at": utc_now(),
        }

    def _governance_status(self) -> dict[str, Any]:
        metrics = self.service.metrics_snapshot() if self.service is not None else {}
        return {
            "recent_tool_calls": metrics.get("tool_calls_total", 0),
            "recent_failures": metrics.get("tool_calls_failed", 0),
            "top_error_codes": metrics.get("per_error_code", {}),
            "permission_denied_count": metrics.get("permission_denied_total", 0),
            "schema_violation_count": int(metrics.get("params_schema_error_total", 0))
            + int(metrics.get("return_schema_error_total", 0)),
            "governance_denied_count": metrics.get("tool_calls_denied", 0),
            "budget_exceeded_count": metrics.get("budget_exceeded_total", 0),
            "confirmation_required_count": metrics.get("confirmation_required_total", 0),
            "governance_store": (
                governance_store_metadata(getattr(self.service, "governance_store", None))
                if self.service is not None
                else {"store_kind": UNAVAILABLE}
            ),
            "generated_at": utc_now(),
        }

    def _installed_plugins(self) -> list[Any]:
        if self.engine is None:
            return _read_installed_plugins(self.plugins_dir)
        try:
            self.engine.discover()
        except Exception:
            pass
        return list(getattr(self.engine.loader, "installed_plugins", {}).values())

    def _audit_records(self) -> list[AuditRecord]:
        reader = getattr(self.audit_logger, "read_records", None)
        if not callable(reader):
            return []
        try:
            return list(reader())
        except Exception:
            return []


def _high_risk_permissions() -> set[str]:
    return {
        PermissionName.NETWORK_OUTBOUND.value,
        PermissionName.FS_WRITE.value,
        PermissionName.MEMORY_WRITE.value,
        PermissionName.OUTPUT_SEND.value,
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_installed_plugins(plugins_dir: Path) -> list[InstalledPlugin]:
    if not plugins_dir.exists() or not plugins_dir.is_dir():
        return []
    installed: list[InstalledPlugin] = []
    for child in sorted(plugins_dir.iterdir()):
        if not child.is_dir():
            continue
        metadata_path = child / "plugin.yaml"
        if not metadata_path.exists() or not (child / MANIFEST_FILE).exists():
            continue
        try:
            raw = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                continue
            metadata = PluginMetadata(**raw)
            manifest = _read_json(child / MANIFEST_FILE)
            status = PluginStatus(manifest.get("status") or PluginStatus.DISCOVERED.value)
            installed.append(
                InstalledPlugin(
                    metadata=metadata,
                    path=str(child.resolve()),
                    package_hash=manifest.get("package_hash"),
                    installed_at=manifest.get("installed_at"),
                    status=status,
                    granted_permissions=manifest.get("granted_permissions") or [],
                    permission_review=manifest.get("permission_review") or {},
                )
            )
        except Exception:
            continue
    return installed


def _raw_plugin_dirs(plugins_dir: Path) -> list[str]:
    if not plugins_dir.exists() or not plugins_dir.is_dir():
        return []
    names: list[str] = []
    for child in sorted(plugins_dir.iterdir()):
        if child.is_dir() and (child / "plugin.yaml").exists():
            names.append(child.name)
    return names


def _not_installed_raw_plugins(plugins_dir: Path) -> list[str]:
    if not plugins_dir.exists() or not plugins_dir.is_dir():
        return []
    names: list[str] = []
    for child in sorted(plugins_dir.iterdir()):
        if child.is_dir() and (child / "plugin.yaml").exists() and not (child / MANIFEST_FILE).exists():
            names.append(child.name)
    return names


def _template_status(template_dir: Path) -> dict[str, Any]:
    plugin_yaml = template_dir / "plugin.yaml"
    readme = template_dir / "README.md"
    warnings: list[str] = []
    lint_status = UNKNOWN
    lint_warnings: list[str] = []
    risk_level = UNKNOWN
    has_params_schema = False
    has_returns_schema = False
    if plugin_yaml.exists():
        try:
            from .manifest_lint import lint_plugin_manifest

            lint = lint_plugin_manifest(template_dir, production_mode=False)
            lint_status = str(lint.get("status") or UNKNOWN)
            lint_warnings = [str(item.get("code") or "warning") for item in lint.get("warnings", [])[:20]]
            visibility = lint.get("model_visibility_status")
            if isinstance(visibility, dict) and visibility:
                risks = [
                    str(item.get("risk_level") or UNKNOWN)
                    for item in visibility.values()
                    if isinstance(item, dict)
                ]
                risk_level = _max_risk(risks)
        except Exception as exc:
            lint_status = "unknown"
            warnings.append(f"lint_unavailable:{type(exc).__name__}")
        try:
            raw = yaml.safe_load(plugin_yaml.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for extension in raw.get("extensions") or []:
                    if not isinstance(extension, dict) or extension.get("type") != "tool":
                        continue
                    has_params_schema = has_params_schema or bool(extension.get("params"))
                    has_returns_schema = has_returns_schema or bool(extension.get("returns"))
        except Exception as exc:
            warnings.append(f"plugin_yaml_unreadable:{type(exc).__name__}")
    production_examples = {
        "manifest_lock_example": (template_dir / "manifest.lock.example").exists(),
        "sbom_example": (template_dir / "sbom.cdx.json.example").exists(),
        "scanner_report_example": (template_dir / "scanner_report.example.json").exists(),
        "signature_example": (template_dir / "SIGNATURE.example").exists(),
    }
    template_kind = "production_package" if template_dir.name == "production_plugin_package" else (
        "business"
        if template_dir.name in {"readonly_retrieval_plugin", "controlled_network_plugin", "file_summary_plugin"}
        else "basic"
    )
    return {
        "name": template_dir.name,
        "path": str(template_dir),
        "template_kind": template_kind,
        "has_plugin_yaml": plugin_yaml.exists(),
        "has_readme": readme.exists(),
        "has_params_schema_example": has_params_schema,
        "has_returns_schema_example": has_returns_schema,
        "has_production_package_examples": any(production_examples.values()),
        "production_package_examples": production_examples,
        "example_scanner_report_is_real": False,
        "example_signature_is_valid": False,
        "lint_status": lint_status,
        "risk_level": risk_level,
        "warnings": [*warnings, *lint_warnings],
        "generated_at": utc_now(),
    }


def _max_risk(values: list[str]) -> str:
    order = {"unknown": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    if not values:
        return UNKNOWN
    return max(values, key=lambda item: order.get(item, 0))


def _safe_verify_status(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in report.items()
        if key in {"status", "records", "last_hash", "checkpoint", "reason", "error_type"}
    }


def _unknown_bool(value: bool | None) -> bool | str:
    if value is None:
        return UNKNOWN
    return bool(value)


def _revoked_key_count(governance: dict[str, Any]) -> int:
    publishers = governance.get("publishers")
    if not isinstance(publishers, dict):
        return 0
    count = 0
    for publisher in publishers.values():
        if not isinstance(publisher, dict):
            continue
        keys = publisher.get("keys")
        if not isinstance(keys, dict):
            continue
        count += sum(1 for item in keys.values() if isinstance(item, dict) and item.get("status") == "revoked")
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plugin system status snapshot")
    parser.add_argument("--plugins-dir", default="data/plugins")
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--scanner-configured", action="store_true")
    parser.add_argument("--registry-signed", action="store_true")
    parser.add_argument("--external-anchor-configured", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    provider = PluginSystemStatusProvider(
        plugins_dir=args.plugins_dir,
        production_mode=args.production,
        scanner_configured=args.scanner_configured if args.scanner_configured else None,
        registry_signed=args.registry_signed if args.registry_signed else None,
        external_anchor_configured=args.external_anchor_configured if args.external_anchor_configured else None,
    )
    report = provider.get_full_status()
    if args.json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
