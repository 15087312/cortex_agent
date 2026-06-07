from __future__ import annotations

import argparse
import json
import platform
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from .loader import GOVERNANCE_FILE, MANIFEST_FILE, PACKAGE_LOCK_FILE
from .models import PermissionName, PluginMetadata, RunMode, TrustLevel
from .policy import PluginPolicy, validate_scan_report
from .production_evidence import (
    PRODUCTION_EVIDENCE_SCHEMA_VERSION,
    audit_anchor_evidence_passes,
    confirmation_evidence_passes,
    first_evidence,
    governance_evidence_passes,
    is_legacy_evidence,
    normalize_evidence_dict,
    registry_evidence_passes,
    sandbox_evidence_passes,
    scanner_evidence_passes,
    signature_evidence_passes,
    validate_evidence_dict,
)
from .signing import LEGACY_SIGNATURE_ALGORITHM, SIGNATURE_ALGORITHM
from .tool_contracts import TOOL_SERVICE_CONTRACT_VERSION, utc_now


SENSITIVE_GATEWAY_PERMISSIONS = {
    PermissionName.NETWORK_OUTBOUND.value,
    PermissionName.FS_READ.value,
    PermissionName.FS_WRITE.value,
    PermissionName.MEMORY_READ.value,
    PermissionName.MEMORY_WRITE.value,
    PermissionName.OUTPUT_SEND.value,
}
DIRECT_ACCESS_PATTERNS = (
    re.compile(r"\brequests\.", re.IGNORECASE),
    re.compile(r"\bhttpx\.", re.IGNORECASE),
    re.compile(r"\bsocket\.", re.IGNORECASE),
    re.compile(r"\burllib\.", re.IGNORECASE),
    re.compile(r"\bopen\s*\(", re.IGNORECASE),
    re.compile(r"\bPath\s*\(", re.IGNORECASE),
    re.compile(r"\bsubprocess\.", re.IGNORECASE),
)


@dataclass(frozen=True)
class ProductionPolicyFinding:
    check_id: str
    status: str
    reason: str
    severity: str = "high"
    production_blocking: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_production_policy_check(
    *,
    plugin_dir: str | Path | None = None,
    plugins_dir: str | Path | None = None,
    scan_report: dict[str, Any] | None = None,
    scanner_risk_accepted: bool = False,
    registry_signed: bool | None = None,
    sandbox_enforced: bool | None = None,
    model_adapter_available: bool = True,
    production_mode: bool = True,
    governance_store: dict[str, Any] | None = None,
    confirmation_provider: dict[str, Any] | None = None,
    external_anchor_configured: bool | None = None,
    evidence_bundle: dict[str, Any] | None = None,
    registry_evidence: dict[str, Any] | None = None,
    signature_evidence: dict[str, Any] | None = None,
    sandbox_evidence: dict[str, Any] | None = None,
    governance_evidence: dict[str, Any] | None = None,
    confirmation_evidence: dict[str, Any] | None = None,
    audit_anchor_evidence: dict[str, Any] | None = None,
    scanner_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = PluginPolicy()
    evidence_context = _evidence_context(
        evidence_bundle=evidence_bundle,
        registry_evidence=registry_evidence,
        signature_evidence=signature_evidence,
        sandbox_evidence=sandbox_evidence,
        governance_evidence=governance_evidence,
        confirmation_evidence=confirmation_evidence,
        audit_anchor_evidence=audit_anchor_evidence,
        scanner_evidence=scanner_evidence,
    )
    if evidence_context["registry_signed"] is not None:
        registry_signed = evidence_context["registry_signed"]
    if evidence_context["sandbox_enforced"] is not None:
        sandbox_enforced = evidence_context["sandbox_enforced"]
    if evidence_context["governance_store"] is not None:
        governance_store = evidence_context["governance_store"]
    if evidence_context["confirmation_provider"] is not None:
        confirmation_provider = evidence_context["confirmation_provider"]
    if evidence_context["external_anchor_configured"] is not None:
        external_anchor_configured = evidence_context["external_anchor_configured"]
    if evidence_context["scan_report"] is not None:
        scan_report = evidence_context["scan_report"]
    findings: list[ProductionPolicyFinding] = []
    findings.extend(evidence_context["findings"])
    findings.extend(_default_policy_findings(policy, registry_signed=registry_signed))
    findings.extend(_platform_findings(production_mode=production_mode, sandbox_enforced=sandbox_enforced))
    findings.extend(
        _governance_findings(
            production_mode=production_mode,
            governance_store=governance_store,
            confirmation_provider=confirmation_provider,
            external_anchor_configured=external_anchor_configured,
        )
    )

    source_path = Path(plugin_dir).resolve() if plugin_dir is not None else None
    metadata = _read_metadata(source_path) if source_path is not None else None
    manifest = _read_json(source_path / MANIFEST_FILE) if source_path is not None else {}
    governance = _read_json(Path(plugins_dir).resolve() / GOVERNANCE_FILE) if plugins_dir else {}
    if source_path is None:
        findings.append(
            _warn(
                "plugin.source",
                "plugin source not provided; per-plugin package evidence was not evaluated",
                severity="medium",
            )
        )
    elif metadata is None:
        findings.append(_fail("plugin.source", "plugin.yaml is missing or invalid"))
    else:
        findings.extend(
            _plugin_findings(
                source_path,
                metadata,
                manifest=manifest,
                scan_report=scan_report,
                scanner_risk_accepted=scanner_risk_accepted,
                sandbox_enforced=sandbox_enforced,
                governance=governance,
            )
        )

    findings.append(
        _pass(
            "tool_service.gateway",
            "model-facing adapter entry points delegate to PluginToolService, which delegates through ModelToolBridge, LLMToolRuntime, Engine, and Gateway",
            details={"adapter_available": model_adapter_available},
        )
        if model_adapter_available
        else _fail(
            "tool_service.gateway",
            "PluginToolService adapter entry point is not available",
            details={"adapter_available": False},
        )
    )

    status = "pass"
    if any(item.status == "fail" for item in findings):
        status = "fail"
    elif any(item.status == "warn" for item in findings):
        status = "warn"
    errors = [item.to_dict() for item in findings if item.status == "fail"]
    warnings = [item.to_dict() for item in findings if item.status == "warn"]
    blockers = [item.check_id for item in findings if item.production_blocking]
    return {
        "status": status,
        "contract_version": TOOL_SERVICE_CONTRACT_VERSION,
        "production_mode": production_mode,
        "production_blocking": any(item.production_blocking for item in findings),
        "blockers": blockers,
        "errors": errors,
        "warnings": warnings,
        "evidence_schema_version": evidence_context["schema_version"],
        "legacy_evidence_detected": evidence_context["legacy_evidence_detected"],
        "recommendations": _recommendations(findings),
        "checks": [item.to_dict() for item in findings],
        "generated_at": utc_now(),
    }


def _default_policy_findings(
    policy: PluginPolicy,
    *,
    registry_signed: bool | None,
) -> list[ProductionPolicyFinding]:
    findings = [
        _pass("third_party.subprocess_required", "third-party production plugins must run in sub_process")
        if not policy.third_party_allow_in_process
        else _fail("third_party.subprocess_required", "policy allows third-party production plugins to run in-process"),
        _pass("third_party.sandbox_required", "third-party production plugins require an enforced sandbox")
        if policy.third_party_require_sandbox
        else _fail("third_party.sandbox_required", "policy does not require enforced sandbox for third-party plugins"),
        _pass("signature.required", "third-party production packages require signatures")
        if policy.third_party_require_signature
        else _fail("signature.required", "policy does not require third-party package signatures"),
        _pass("signature.ed25519_required", f"production signature algorithm is {SIGNATURE_ALGORITHM}"),
        _pass("signature.hmac_not_production_trust", f"{LEGACY_SIGNATURE_ALGORITHM} is legacy/dev only"),
        _pass("sbom.required", "third-party production packages require SBOM")
        if policy.third_party_require_sbom
        else _fail("sbom.required", "policy does not require third-party SBOM"),
        _pass("lockfile.required", f"third-party production packages require {PACKAGE_LOCK_FILE}")
        if policy.third_party_require_lockfile
        else _fail("lockfile.required", "policy does not require a package lockfile"),
        _pass("scanner.required", "third-party production packages require passing scanner evidence")
        if policy.third_party_require_scan_pass
        else _fail("scanner.required", "policy does not require scan evidence"),
    ]
    if registry_signed is True:
        findings.append(_pass("registry.signed", "signed registry evidence was provided"))
    elif registry_signed is False:
        findings.append(_fail("registry.signed", "signed registry evidence is missing"))
    else:
        findings.append(_warn("registry.signed", "signed registry evidence was not provided", severity="high"))
    return findings


def _evidence_context(
    *,
    evidence_bundle: dict[str, Any] | None,
    registry_evidence: dict[str, Any] | None,
    signature_evidence: dict[str, Any] | None,
    sandbox_evidence: dict[str, Any] | None,
    governance_evidence: dict[str, Any] | None,
    confirmation_evidence: dict[str, Any] | None,
    audit_anchor_evidence: dict[str, Any] | None,
    scanner_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    findings: list[ProductionPolicyFinding] = []
    all_payloads = [
        item
        for item in [
            evidence_bundle,
            registry_evidence,
            signature_evidence,
            sandbox_evidence,
            governance_evidence,
            confirmation_evidence,
            audit_anchor_evidence,
            scanner_evidence,
        ]
        if item
    ]
    legacy = any(is_legacy_evidence(item) for item in all_payloads)
    if legacy:
        findings.append(
            _warn(
                "evidence.legacy_schema",
                "legacy evidence JSON was provided; prefer production_evidence schema",
                severity="medium",
            )
        )
    normalized_bundle = normalize_evidence_dict(evidence_bundle) if evidence_bundle else None
    schema_version = (
        str((normalized_bundle or {}).get("schema_version") or PRODUCTION_EVIDENCE_SCHEMA_VERSION)
    )
    evidence_validation_errors: list[dict[str, Any]] = []
    evidence_validation_warnings: list[dict[str, Any]] = []
    for payload in all_payloads:
        validation = validate_evidence_dict(payload)
        evidence_validation_errors.extend(validation.get("errors", []))
        evidence_validation_warnings.extend(validation.get("warnings", []))
    if evidence_validation_errors:
        findings.append(
            _fail(
                "evidence.schema",
                "production evidence schema validation failed",
                details={"errors": evidence_validation_errors[:20]},
            )
        )
    for warning in evidence_validation_warnings[:20]:
        findings.append(
            _warn(
                f"evidence.{warning.get('code') or 'warning'}",
                str(warning.get("message") or "production evidence warning"),
                severity="medium",
                details={"evidence_type": warning.get("evidence_type")},
            )
        )

    registry = registry_evidence or first_evidence(evidence_bundle, "registry")
    signature = signature_evidence or first_evidence(evidence_bundle, "signature")
    sandbox = sandbox_evidence or first_evidence(evidence_bundle, "sandbox")
    governance = governance_evidence or first_evidence(evidence_bundle, "governance")
    confirmation = confirmation_evidence or first_evidence(evidence_bundle, "confirmation")
    audit_anchor = audit_anchor_evidence or first_evidence(evidence_bundle, "audit_anchor")
    scanner = scanner_evidence or first_evidence(evidence_bundle, "scanner")

    registry_pass = registry_evidence_passes(registry)
    signature_pass = signature_evidence_passes(signature)
    sandbox_pass = sandbox_evidence_passes(sandbox)
    governance_pass = governance_evidence_passes(governance)
    confirmation_pass = confirmation_evidence_passes(confirmation)
    audit_pass = audit_anchor_evidence_passes(audit_anchor)
    scanner_pass = scanner_evidence_passes(scanner)

    return {
        "schema_version": schema_version,
        "legacy_evidence_detected": legacy,
        "findings": findings,
        "registry_signed": registry_pass,
        "signature_verified": signature_pass,
        "sandbox_enforced": sandbox_pass,
        "governance_store": _governance_store_from_evidence(governance, governance_pass),
        "confirmation_provider": _confirmation_provider_from_evidence(confirmation, confirmation_pass),
        "external_anchor_configured": audit_pass,
        "scan_report": _scan_report_from_evidence(scanner, scanner_pass),
    }


def _governance_store_from_evidence(evidence: dict[str, Any] | None, passes: bool | None) -> dict[str, Any] | None:
    if evidence is None:
        return None
    normalized = normalize_evidence_dict(evidence)
    return {
        "store_kind": normalized.get("governance_store_kind") or normalized.get("store_kind"),
        "persistent": normalized.get("persistent") is True,
        "multi_instance_safe": normalized.get("multi_instance_safe") is True,
        "multi_process_safe": normalized.get("multi_process_safe") is True,
        "production_recommended": passes is True,
        "evidence_type": "governance",
    }


def _confirmation_provider_from_evidence(evidence: dict[str, Any] | None, passes: bool | None) -> dict[str, Any] | None:
    if evidence is None:
        return None
    normalized = normalize_evidence_dict(evidence)
    return {
        "provider_kind": normalized.get("provider_kind"),
        "production_recommended": passes is True,
        "token_bound_to_args_hash": normalized.get("token_bound_to_args_hash") is True,
        "token_expiry_enforced": normalized.get("token_expiry_enforced") is True,
        "evidence_type": "confirmation",
    }


def _scan_report_from_evidence(evidence: dict[str, Any] | None, passes: bool | None) -> dict[str, Any] | None:
    if evidence is None:
        return None
    normalized = normalize_evidence_dict(evidence)
    return {
        "status": "pass" if passes else "fail",
        "policy_decision": normalized.get("policy_decision"),
        "production_evidence": normalized.get("production_evidence") is True,
        "source": normalized.get("source"),
        "findings_summary": normalized.get("findings_summary") if isinstance(normalized.get("findings_summary"), dict) else {},
    }


def _platform_findings(*, production_mode: bool, sandbox_enforced: bool | None) -> list[ProductionPolicyFinding]:
    is_windows = sys.platform.startswith("win") or platform.system().lower().startswith("win")
    if is_windows and sandbox_enforced is True:
        return [
            _pass(
                "platform.strong_sandbox",
                "external target production sandbox evidence was provided; Windows Job Object remains resource-limits-only",
                details={"platform": sys.platform, "sandbox_enforced_evidence": True},
            )
        ]
    if is_windows:
        if production_mode:
            return [
                _fail(
                    "platform.strong_sandbox",
                    "Windows Job Object or missing sandbox evidence is not accepted as a strong production sandbox for third-party plugins",
                    details={"platform": sys.platform, "sandbox_enforced_evidence": sandbox_enforced},
                )
            ]
        return [
            _warn(
                "platform.strong_sandbox",
                "Windows Job Object is resource limiting only; do not treat it as strong filesystem/network/syscall sandbox evidence",
                details={"platform": sys.platform, "sandbox_enforced_evidence": sandbox_enforced},
            )
        ]
    if sandbox_enforced is False and production_mode:
        return [_fail("platform.strong_sandbox", "production sandbox enforcement evidence is false")]
    if sandbox_enforced is True:
        return [_pass("platform.strong_sandbox", "strong sandbox evidence was provided")]
    if production_mode:
        return [_fail("platform.strong_sandbox", "strong sandbox evidence was not provided")]
    return [_warn("platform.strong_sandbox", "strong sandbox evidence was not provided", severity="medium")]


def _governance_findings(
    *,
    production_mode: bool,
    governance_store: dict[str, Any] | None,
    confirmation_provider: dict[str, Any] | None,
    external_anchor_configured: bool | None,
) -> list[ProductionPolicyFinding]:
    findings: list[ProductionPolicyFinding] = []
    store = governance_store or {}
    if not store:
        findings.append(
            _warn(
                "governance.store_persistent",
                "governance store evidence was not provided; in-memory governance is not production multi-instance safe",
                details={"required": "persistent_or_external_store"},
            )
        )
    elif not bool(store.get("persistent")):
        finding = _fail if production_mode else _warn
        findings.append(
            finding(
                "governance.store_persistent",
                "governance store is not persistent",
                details={"store_kind": store.get("store_kind")},
            )
        )
    elif not bool(store.get("production_recommended")):
        finding = _fail if production_mode else _warn
        findings.append(
            finding(
                "governance.store_production_recommended",
                "governance store is not marked production recommended",
                details={"store_kind": store.get("store_kind")},
            )
        )
    elif not bool(store.get("multi_instance_safe", False)):
        findings.append(
            _warn(
                "governance.store_multi_instance_safe",
                "governance store is persistent but not multi-instance safe; use an external transactional store for multi-instance production",
                details={"store_kind": store.get("store_kind")},
            )
        )
    else:
        findings.append(_pass("governance.store_persistent", "governance store evidence is production suitable"))

    provider = confirmation_provider or {}
    provider_kind = str(provider.get("provider_kind") or "unknown")
    if not provider:
        findings.append(
            _warn(
                "confirmation.external_approval",
                "confirmation provider evidence was not provided; local confirmation is not external approval",
                details={"required": "external_or_operator_approval_provider"},
            )
        )
    elif provider_kind == "local" or not bool(provider.get("production_recommended")):
        finding = _fail if production_mode else _warn
        findings.append(
            finding(
                "confirmation.external_approval",
                "local confirmation provider is not external production approval",
                details={"provider_kind": provider_kind},
            )
        )
    else:
        findings.append(_pass("confirmation.external_approval", "confirmation provider is production suitable"))

    if external_anchor_configured is True:
        findings.append(_pass("audit.external_anchor", "external immutable audit anchor evidence was provided"))
    elif external_anchor_configured is False:
        finding = _fail if production_mode else _warn
        findings.append(finding("audit.external_anchor", "local audit checkpoint is not an immutable external anchor"))
    else:
        findings.append(
            _warn(
                "audit.external_anchor",
                "external immutable audit anchor evidence was not provided; local checkpoints are tamper-evident only",
            )
        )
    return findings


def _plugin_findings(
    plugin_dir: Path,
    metadata: PluginMetadata,
    *,
    manifest: dict[str, Any],
    scan_report: dict[str, Any] | None,
    scanner_risk_accepted: bool,
    sandbox_enforced: bool | None,
    governance: dict[str, Any],
) -> list[ProductionPolicyFinding]:
    findings: list[ProductionPolicyFinding] = []
    is_third_party = metadata.runtime.trust == TrustLevel.THIRD_PARTY
    if is_third_party:
        findings.append(
            _pass("plugin.third_party_subprocess", f"{metadata.name} effective runtime is sub_process")
            if metadata.effective_run_mode == RunMode.SUB_PROCESS
            else _fail(
                "plugin.third_party_subprocess",
                f"{metadata.name} effective runtime is {metadata.effective_run_mode.value}, not sub_process",
                plugin=metadata.name,
            )
        )
        if sandbox_enforced is True:
            findings.append(_pass("plugin.third_party_sandbox", "enforced sandbox evidence was provided"))
        elif sandbox_enforced is False:
            findings.append(_fail("plugin.third_party_sandbox", "enforced sandbox evidence is missing"))
        else:
            findings.append(_warn("plugin.third_party_sandbox", "sandbox enforcement evidence was not provided"))
        findings.extend(_signature_findings(metadata, manifest))
        findings.append(
            _pass("plugin.sbom", "SBOM file is present")
            if (plugin_dir / "sbom.cdx.json").exists()
            else _fail("plugin.sbom", "third-party production package is missing sbom.cdx.json")
        )
        findings.append(
            _pass("plugin.lockfile", f"{PACKAGE_LOCK_FILE} is present")
            if (plugin_dir / PACKAGE_LOCK_FILE).exists()
            else _fail("plugin.lockfile", f"third-party production package is missing {PACKAGE_LOCK_FILE}")
        )
        findings.extend(_scan_findings(scan_report, scanner_risk_accepted=scanner_risk_accepted))
    else:
        findings.append(_pass("plugin.trust", f"{metadata.name} trust={metadata.runtime.trust.value}"))

    findings.append(
        _fail(
            "plugin.legacy_local_layout",
            "legacy metadata.json + plugin.py local plugin layout is forbidden for production",
        )
        if (plugin_dir / "metadata.json").exists() and (plugin_dir / "plugin.py").exists()
        else _pass("plugin.legacy_local_layout", "legacy metadata.json + plugin.py local layout not present")
    )
    findings.extend(_revocation_findings(metadata, governance))
    findings.extend(_gateway_usage_findings(plugin_dir, metadata))
    findings.extend(_tool_service_findings(metadata))
    return findings


def _signature_findings(metadata: PluginMetadata, manifest: dict[str, Any]) -> list[ProductionPolicyFinding]:
    signature = manifest.get("signature")
    if not isinstance(signature, dict):
        return [_fail("plugin.signature", f"third-party production package {metadata.name} is missing signature record")]
    algorithm = signature.get("algorithm")
    if algorithm == SIGNATURE_ALGORITHM:
        return [_pass("plugin.signature", f"{metadata.name} signature uses {SIGNATURE_ALGORITHM}")]
    if algorithm == LEGACY_SIGNATURE_ALGORITHM:
        return [
            _fail(
                "plugin.signature",
                f"{metadata.name} uses {LEGACY_SIGNATURE_ALGORITHM}; HMAC is not production trust",
            )
        ]
    return [_fail("plugin.signature", f"{metadata.name} signature algorithm is unsupported: {algorithm}")]


def _scan_findings(
    scan_report: dict[str, Any] | None,
    *,
    scanner_risk_accepted: bool,
) -> list[ProductionPolicyFinding]:
    if scan_report is None:
        if scanner_risk_accepted:
            return [_warn("plugin.scanner", "scanner evidence is missing; explicit risk acceptance was provided")]
        return [_fail("plugin.scanner", "third-party production package is missing scanner report")]
    if scan_report.get("production_evidence") is True and scan_report.get("policy_decision") == "pass":
        return [_pass("plugin.scanner", "production scanner evidence passed policy")]
    if scan_report.get("production_evidence") is False:
        if scanner_risk_accepted:
            return [_warn("plugin.scanner", "scanner evidence is not production evidence; risk accepted")]
        return [_fail("plugin.scanner", "scanner evidence is not production evidence")]
    decision = validate_scan_report(scan_report)
    if decision.status == "pass":
        return [_pass("plugin.scanner", "scanner report passed policy")]
    if scanner_risk_accepted:
        return [_warn("plugin.scanner", f"scanner report did not pass; risk accepted: {decision.reason}")]
    return [_fail("plugin.scanner", decision.reason)]


def _revocation_findings(metadata: PluginMetadata, governance: dict[str, Any]) -> list[ProductionPolicyFinding]:
    revoked_versions = governance.get("revoked_plugin_versions")
    if not isinstance(revoked_versions, list):
        return [_warn("revocation.configured", "revocation governance list is unavailable")]
    current = {"name": metadata.name, "version": metadata.version}
    if any(item == current for item in revoked_versions if isinstance(item, dict)):
        return [_fail("revocation.effective", f"{metadata.name} {metadata.version} is revoked")]
    return [_pass("revocation.effective", f"{metadata.name} {metadata.version} is not in revoked version list")]


def _gateway_usage_findings(plugin_dir: Path, metadata: PluginMetadata) -> list[ProductionPolicyFinding]:
    sensitive_tools = {
        tool_name
        for tool_name in metadata.tool_entries()
        if metadata.tool_requested_permissions(tool_name) & SENSITIVE_GATEWAY_PERMISSIONS
    }
    if not sensitive_tools:
        return [_pass("gateway.required", "plugin tools do not request sensitive gateway permissions")]
    suspicious = _direct_access_markers(plugin_dir)
    if suspicious:
        return [
            _fail(
                "gateway.required",
                "source contains direct network/filesystem/process access markers; sensitive capabilities must use injected Gateway api",
                details={"tools": sorted(sensitive_tools), "markers": suspicious[:20]},
            )
        ]
    return [
        _pass(
            "gateway.required",
            "sensitive tool permissions are declared and no direct access markers were found",
            details={"tools": sorted(sensitive_tools)},
        )
    ]


def _tool_service_findings(metadata: PluginMetadata) -> list[ProductionPolicyFinding]:
    tool_count = len(metadata.tool_entries())
    if tool_count <= 0:
        return [_warn("model_tools.service_path", "plugin has no model-facing tools", severity="medium")]
    findings = [
        _pass(
            "model_tools.service_path",
            "model-facing tools are exposed through PluginToolService/provider adapters in plugin_system",
            details={"tool_count": tool_count},
        )
    ]
    high_risk_tools = []
    for tool_name in metadata.tool_entries():
        permissions = metadata.tool_requested_permissions(tool_name)
        if permissions & {
            PermissionName.NETWORK_OUTBOUND.value,
            PermissionName.FS_WRITE.value,
            PermissionName.MEMORY_WRITE.value,
            PermissionName.OUTPUT_SEND.value,
        }:
            high_risk_tools.append(tool_name)
    if high_risk_tools:
        findings.append(
            _pass(
                "model_tools.high_risk_hidden_by_default",
                "plugin_system exposure policy maps high-risk tool permissions to expert/admin visibility, not normal model default",
                details={"static_policy": True, "high_risk_tools": sorted(high_risk_tools)},
            )
        )
    else:
        findings.append(_pass("model_tools.high_risk_hidden_by_default", "plugin declares no high-risk model tools"))
    return findings


def _direct_access_markers(plugin_dir: Path) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    src_dir = plugin_dir / "src"
    if not src_dir.exists():
        return markers
    for path in sorted(src_dir.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for pattern in DIRECT_ACCESS_PATTERNS:
            if pattern.search(text):
                markers.append(
                    {
                        "path": path.relative_to(plugin_dir).as_posix(),
                        "pattern": pattern.pattern,
                    }
                )
    return markers


def _read_metadata(plugin_dir: Path | None) -> PluginMetadata | None:
    if plugin_dir is None:
        return None
    metadata_path = plugin_dir / "plugin.yaml"
    if not metadata_path.exists():
        return None
    try:
        payload = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        return PluginMetadata(**payload)
    except Exception:
        return None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_json_value(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _evidence_bool(value: Any, keys: tuple[str, ...]) -> bool | None:
    if isinstance(value, bool):
        return value
    if not isinstance(value, dict):
        return None
    for key in keys:
        if key in value:
            raw = value.get(key)
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str):
                normalized = raw.strip().lower()
                if normalized in {"true", "1", "yes", "pass", "passed", "verified", "enabled"}:
                    return True
                if normalized in {"false", "0", "no", "fail", "failed", "missing", "disabled"}:
                    return False
    return None


def _pass(check_id: str, reason: str, *, details: dict[str, Any] | None = None) -> ProductionPolicyFinding:
    return ProductionPolicyFinding(
        check_id=check_id,
        status="pass",
        reason=reason,
        severity="info",
        production_blocking=False,
        details=details or {},
    )


def _warn(
    check_id: str,
    reason: str,
    *,
    severity: str = "high",
    details: dict[str, Any] | None = None,
) -> ProductionPolicyFinding:
    return ProductionPolicyFinding(
        check_id=check_id,
        status="warn",
        reason=reason,
        severity=severity,
        production_blocking=False,
        details=details or {},
    )


def _fail(
    check_id: str,
    reason: str,
    *,
    plugin: str | None = None,
    details: dict[str, Any] | None = None,
) -> ProductionPolicyFinding:
    payload = details or {}
    if plugin is not None:
        payload = {**payload, "plugin": plugin}
    return ProductionPolicyFinding(
        check_id=check_id,
        status="fail",
        reason=reason,
        severity="critical",
        production_blocking=True,
        details=payload,
    )


def _recommendations(findings: list[ProductionPolicyFinding]) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for finding in findings:
        if finding.status == "pass" or finding.check_id in seen:
            continue
        seen.add(finding.check_id)
        recommendations.append(
            {
                "check_id": finding.check_id,
                "severity": finding.severity,
                "production_blocking": finding.production_blocking,
                "recommendation": _recommendation_for(finding.check_id, finding.reason),
            }
        )
    return recommendations


def _recommendation_for(check_id: str, reason: str) -> str:
    table = {
        "plugin.source": "Provide a plugin_dir with plugin.yaml when evaluating a concrete production package.",
        "registry.signed": "Require and verify a signed plugin registry entry before production enablement.",
        "platform.strong_sandbox": "Use a production-supported enforced sandbox and keep Windows Job Object evidence as warning-only.",
        "governance.store_persistent": "Use a persistent or external governance store for budgets, confirmation, and idempotency.",
        "governance.store_production_recommended": "Switch to a governance store marked production recommended.",
        "governance.store_multi_instance_safe": "Use an external transactional store for multi-instance production deployments.",
        "confirmation.external_approval": "Replace the local confirmation provider with an external operator/user approval provider.",
        "audit.external_anchor": "Anchor audit checkpoints in append-only external storage, SIEM, WORM storage, or a transparency log.",
        "plugin.third_party_sandbox": "Provide enforced sandbox evidence from the actual production runtime.",
        "plugin.scanner": "Provide a passing scanner report or a formal risk acceptance record.",
        "plugin.sbom": "Include sbom.cdx.json in the plugin package.",
        "plugin.lockfile": f"Include {PACKAGE_LOCK_FILE} in the plugin package.",
        "plugin.signature": f"Sign the package with {SIGNATURE_ALGORITHM}; do not rely on {LEGACY_SIGNATURE_ALGORITHM} for production.",
    }
    return table.get(check_id, reason)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plugin production policy checker")
    parser.add_argument("plugin_dir", nargs="?")
    parser.add_argument("--plugins-dir")
    parser.add_argument("--scan-report")
    parser.add_argument("--evidence-bundle")
    parser.add_argument("--scanner-risk-accepted", action="store_true")
    parser.add_argument("--registry-signed", action="store_true")
    parser.add_argument("--registry-evidence")
    parser.add_argument("--signature-evidence")
    parser.add_argument("--sandbox-enforced", action="store_true")
    parser.add_argument("--sandbox-evidence")
    parser.add_argument("--governance-store-evidence")
    parser.add_argument("--confirmation-provider-evidence")
    parser.add_argument("--dev-mode", action="store_true")
    parser.add_argument("--external-anchor-configured", action="store_true")
    parser.add_argument("--external-anchor-evidence")
    parser.add_argument("--fail-on-blocking", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    scan_report = _read_json(Path(args.scan_report).resolve()) if args.scan_report else None
    evidence_bundle = _read_json(Path(args.evidence_bundle).resolve()) if args.evidence_bundle else None
    registry_evidence = _read_json_value(Path(args.registry_evidence).resolve()) if args.registry_evidence else None
    signature_evidence = _read_json_value(Path(args.signature_evidence).resolve()) if args.signature_evidence else None
    sandbox_evidence = _read_json_value(Path(args.sandbox_evidence).resolve()) if args.sandbox_evidence else None
    external_anchor_evidence = (
        _read_json_value(Path(args.external_anchor_evidence).resolve())
        if args.external_anchor_evidence
        else None
    )
    governance_store = (
        _read_json(Path(args.governance_store_evidence).resolve())
        if args.governance_store_evidence
        else None
    )
    confirmation_provider = (
        _read_json(Path(args.confirmation_provider_evidence).resolve())
        if args.confirmation_provider_evidence
        else None
    )
    registry_signed = _evidence_bool(
        registry_evidence,
        ("registry_signed", "signed", "verified", "signature_verified"),
    )
    if registry_signed is None and args.registry_signed:
        registry_signed = True
    sandbox_enforced = _evidence_bool(
        sandbox_evidence,
        ("sandbox_enforced", "enforced", "strong_sandbox", "production_sandbox"),
    )
    if sandbox_enforced is None and args.sandbox_enforced:
        sandbox_enforced = True
    external_anchor_configured = _evidence_bool(
        external_anchor_evidence,
        ("external_anchor_configured", "configured", "enabled", "immutable_anchor"),
    )
    if external_anchor_configured is None and args.external_anchor_configured:
        external_anchor_configured = True
    report = run_production_policy_check(
        plugin_dir=args.plugin_dir,
        plugins_dir=args.plugins_dir,
        scan_report=scan_report,
        scanner_risk_accepted=args.scanner_risk_accepted,
        registry_signed=registry_signed,
        sandbox_enforced=sandbox_enforced,
        production_mode=not args.dev_mode,
        governance_store=governance_store,
        confirmation_provider=confirmation_provider,
        external_anchor_configured=external_anchor_configured,
        evidence_bundle=evidence_bundle,
        registry_evidence=registry_evidence if isinstance(registry_evidence, dict) else None,
        signature_evidence=signature_evidence if isinstance(signature_evidence, dict) else None,
        sandbox_evidence=sandbox_evidence if isinstance(sandbox_evidence, dict) else None,
        governance_evidence=governance_store if isinstance(governance_store, dict) else None,
        confirmation_evidence=confirmation_provider if isinstance(confirmation_provider, dict) else None,
        audit_anchor_evidence=external_anchor_evidence if isinstance(external_anchor_evidence, dict) else None,
        scanner_evidence=scan_report if isinstance(scan_report, dict) and scan_report.get("evidence_type") == "scanner" else None,
    )
    if args.json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    if args.fail_on_blocking and report.get("production_blocking"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
