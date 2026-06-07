from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from .production_evidence import (
    PRODUCTION_EVIDENCE_SCHEMA_VERSION,
    AuditAnchorEvidence,
    ProductionEvidenceBundle,
    RegistryEvidence,
    SandboxEvidence,
    ScannerEvidence,
    SignatureEvidence,
    audit_anchor_evidence_passes,
    normalize_evidence_dict,
    registry_evidence_passes,
    sandbox_evidence_passes,
    scanner_evidence_passes,
    signature_evidence_passes,
    validate_evidence_dict,
)
from .signing import LEGACY_SIGNATURE_ALGORITHM, SIGNATURE_ALGORITHM
from .tool_contracts import utc_now


STALE_REPORT_DAYS = 14


@dataclass(frozen=True)
class EvidenceValidationResult:
    status: str
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    normalized: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvidenceAdapter(Protocol):
    adapter_name: str

    def load(self, path_or_config: str | Path | dict[str, Any]) -> dict[str, Any]:
        ...

    def validate(self, bundle: dict[str, Any]) -> EvidenceValidationResult:
        ...

    def normalize(self, bundle: dict[str, Any]) -> dict[str, Any]:
        ...

    def summarize(self, bundle: dict[str, Any]) -> dict[str, Any]:
        ...


class BaseEvidenceAdapter:
    adapter_name = "base"

    def load(self, path_or_config: str | Path | dict[str, Any]) -> dict[str, Any]:
        if isinstance(path_or_config, dict):
            return path_or_config
        return _read_json(Path(path_or_config))

    def validate(self, bundle: dict[str, Any]) -> EvidenceValidationResult:
        normalized = self.normalize(bundle)
        validation = validate_evidence_dict(normalized)
        errors = list(validation.get("errors", []))
        warnings = list(validation.get("warnings", []))
        status = "fail" if errors else "pass"
        return EvidenceValidationResult(status=status, errors=errors, warnings=warnings, normalized=normalized)

    def normalize(self, bundle: dict[str, Any]) -> dict[str, Any]:
        return normalize_evidence_dict(bundle)

    def summarize(self, bundle: dict[str, Any]) -> dict[str, Any]:
        normalized = self.normalize(bundle)
        return {
            "adapter": self.adapter_name,
            "evidence_type": normalized.get("evidence_type"),
            "status": normalized.get("status"),
            "production_pass": _production_pass(normalized),
        }


class ScannerEvidenceAdapter(BaseEvidenceAdapter):
    adapter_name = "scanner"

    def normalize(self, bundle: dict[str, Any]) -> dict[str, Any]:
        source_path = str(bundle.get("_source_path") or "")
        raw_format = _scanner_format(bundle)
        findings = _scanner_findings(raw_format, bundle)
        generated_at = str(bundle.get("generated_at") or bundle.get("created_at") or utc_now())
        production_evidence = _production_source_allowed(bundle, source_path) and not _has_example_path(source_path)
        scanner_name = str(bundle.get("scanner_name") or bundle.get("tool") or raw_format)
        scanner_version = str(bundle.get("scanner_version") or bundle.get("version") or "unknown")
        warnings: list[str] = []
        if scanner_version == "unknown":
            warnings.append("scanner_version_missing")
        if _is_stale(generated_at):
            warnings.append("scanner_report_stale")
        if findings.get("critical", 0) or findings.get("high", 0):
            policy_decision = "fail"
            warnings.append("blocking_vulnerabilities_present")
        elif production_evidence and "scanner_report_stale" not in warnings:
            policy_decision = "pass"
        else:
            policy_decision = "example_only" if _has_example_path(source_path) else "warn"
        evidence = ScannerEvidence(
            status="pass" if policy_decision == "pass" else "warn" if policy_decision == "warn" else "fail",
            scanner_name=scanner_name,
            scanner_version=scanner_version,
            production_evidence=production_evidence and policy_decision == "pass",
            source=str(bundle.get("source") or _source_from_path(source_path)),
            findings_summary=findings,
            policy_decision=policy_decision,
            generated_at=generated_at,
            expires_at=_expires_at(generated_at),
        ).to_dict()
        evidence["raw_format"] = raw_format
        evidence["adapter_warnings"] = warnings
        return evidence


class SignatureEvidenceAdapter(BaseEvidenceAdapter):
    adapter_name = "signature"

    def normalize(self, bundle: dict[str, Any]) -> dict[str, Any]:
        algorithm = str(bundle.get("signature_algorithm") or bundle.get("algorithm") or "unknown")
        verified = _bool(bundle.get("signature_verified") or bundle.get("verified"))
        digest_verified = _bool(bundle.get("package_digest_verified") or bundle.get("package_sha256_verified"))
        key_revoked = _bool(bundle.get("key_revoked") or bundle.get("revoked"))
        production = (
            verified
            and digest_verified
            and not key_revoked
            and SIGNATURE_ALGORITHM.lower().split("-")[0] in algorithm.lower()
            and LEGACY_SIGNATURE_ALGORITHM.lower().split("-")[0] not in algorithm.lower()
        )
        return SignatureEvidence(
            status="pass" if production else "fail",
            signature_verified=verified,
            signature_algorithm=algorithm,
            signer_key_id=str(bundle.get("signer_key_id") or bundle.get("key_id") or "unknown"),
            key_revoked=key_revoked,
            package_digest_verified=digest_verified,
            production_evidence=production,
            policy_decision="pass" if production else "fail",
            generated_at=str(bundle.get("generated_at") or utc_now()),
        ).to_dict()


class RegistryEvidenceAdapter(BaseEvidenceAdapter):
    adapter_name = "registry"

    def normalize(self, bundle: dict[str, Any]) -> dict[str, Any]:
        payload = RegistryEvidence(
            status=str(bundle.get("status") or "unknown"),
            registry_signed=_bool(bundle.get("registry_signed") or bundle.get("index_signature_verified")),
            package_sha256_verified=_bool(bundle.get("package_sha256_verified")),
            unsigned_registry_rejected=_bool(bundle.get("unsigned_registry_rejected")),
            tampered_registry_rejected=_bool(bundle.get("tampered_registry_rejected")),
            revoked_version_rejected=_bool(bundle.get("revoked_version_rejected")),
            generated_at=str(bundle.get("generated_at") or utc_now()),
        ).to_dict()
        payload["rollback_or_downgrade_rejected"] = _bool(bundle.get("rollback_or_downgrade_rejected"))
        if registry_evidence_passes(payload):
            payload["status"] = "pass"
        return payload


class SandboxEvidenceAdapter(BaseEvidenceAdapter):
    adapter_name = "sandbox"

    def normalize(self, bundle: dict[str, Any]) -> dict[str, Any]:
        raw_backend = bundle.get("sandbox_backend")
        backend: dict[str, Any] = dict(raw_backend) if isinstance(raw_backend, dict) else {}
        raw_checks = bundle.get("checks")
        checks: list[dict[str, Any]] = (
            [dict(item) for item in raw_checks if isinstance(item, dict)]
            if isinstance(raw_checks, list)
            else _checks_from_mapping(bundle)
        )
        payload = SandboxEvidence(
            environment_class=str(bundle.get("environment_class") or bundle.get("environment") or "unknown"),
            mode=str(bundle.get("mode") or "production-required"),
            status=str(bundle.get("status") or "unknown"),
            production_blocking=_bool(bundle.get("production_blocking"), default=True),
            sandbox_backend={
                "name": backend.get("name") or bundle.get("sandbox_backend_name") or bundle.get("backend") or "unknown",
                "enforced": _bool(backend.get("enforced") if "enforced" in backend else bundle.get("sandbox_enforced")),
                "capabilities": backend.get("capabilities") if isinstance(backend.get("capabilities"), dict) else {},
            },
            checks=checks,
            generated_at=str(bundle.get("generated_at") or utc_now()),
            source=str(bundle.get("source") or "unknown"),
            artifact_path=bundle.get("artifact_path"),
        ).to_dict()
        if sandbox_evidence_passes(payload):
            payload["status"] = "pass"
            payload["production_blocking"] = False
        return payload


class AuditAnchorEvidenceAdapter(BaseEvidenceAdapter):
    adapter_name = "audit_anchor"

    def normalize(self, bundle: dict[str, Any]) -> dict[str, Any]:
        external = _bool(bundle.get("external_anchor_configured"))
        immutable = _bool(bundle.get("production_immutability")) if external else False
        payload = AuditAnchorEvidence(
            status="pass" if external and immutable else str(bundle.get("status") or "warn"),
            hash_chain_verified=_bool(bundle.get("hash_chain_verified")),
            checkpoint_verified=_bool(bundle.get("checkpoint_verified")),
            external_anchor_configured=external,
            production_immutability=immutable,
            anchor_type=str(bundle.get("anchor_type") or ("external" if external else "local_or_none")),
            generated_at=str(bundle.get("generated_at") or utc_now()),
            controlled_risk_required=not (external and immutable),
        ).to_dict()
        return payload


ADAPTERS: dict[str, type[BaseEvidenceAdapter]] = {
    "scanner": ScannerEvidenceAdapter,
    "signature": SignatureEvidenceAdapter,
    "registry": RegistryEvidenceAdapter,
    "sandbox": SandboxEvidenceAdapter,
    "audit_anchor": AuditAnchorEvidenceAdapter,
}


def normalize_evidence_file(path: str | Path, *, adapter_name: str | None = None) -> dict[str, Any]:
    path_obj = Path(path)
    raw = _read_json(path_obj)
    raw["_source_path"] = str(path_obj)
    adapter = _adapter_for(raw, adapter_name)
    normalized = adapter.normalize(raw)
    return {
        "status": "success",
        "adapter": adapter.adapter_name,
        "normalized": normalized,
        "summary": adapter.summarize(raw),
        "generated_at": utc_now(),
    }


def validate_evidence_file(path: str | Path, *, adapter_name: str | None = None) -> dict[str, Any]:
    normalized = normalize_evidence_file(path, adapter_name=adapter_name)
    adapter = ADAPTERS.get(normalized["adapter"], BaseEvidenceAdapter)()
    result = adapter.validate(normalized["normalized"])
    return {
        "status": result.status,
        "adapter": normalized["adapter"],
        "errors": result.errors,
        "warnings": result.warnings,
        "normalized": result.normalized,
        "production_pass": _production_pass(result.normalized),
        "generated_at": utc_now(),
    }


def adapters_status() -> dict[str, Any]:
    return {
        "status": "success",
        "schema_version": PRODUCTION_EVIDENCE_SCHEMA_VERSION,
        "supported_adapters": sorted(ADAPTERS),
        "scanner_formats": ["pip-audit", "osv", "grype", "safety", "enterprise_sca_generic"],
        "network_calls": False,
        "example_files_are_production_evidence": False,
        "offline_reports_are_production_evidence": False,
        "generated_at": utc_now(),
    }


def example_bundle() -> dict[str, Any]:
    return ProductionEvidenceBundle(
        evidences=[
            ScannerEvidenceAdapter().normalize({"source": "reference_only", "_source_path": "scanner_report.example.json"}),
            SignatureEvidenceAdapter().normalize({"signature_algorithm": "HMAC-SHA256"}),
            RegistryEvidenceAdapter().normalize({}),
            SandboxEvidenceAdapter().normalize({"source": "github_hosted_diagnostic", "environment_class": "github_hosted"}),
            AuditAnchorEvidenceAdapter().normalize({"hash_chain_verified": True, "checkpoint_verified": True}),
        ],
        summary={"note": "Adapter output shape only; this bundle is not production evidence."},
        warnings=["example_bundle_not_production_evidence"],
    ).to_dict()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize and validate local production evidence files")
    parser.add_argument("--normalize")
    parser.add_argument("--validate")
    parser.add_argument("--adapter", choices=sorted(ADAPTERS))
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--example-bundle", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    if args.normalize:
        payload = normalize_evidence_file(args.normalize, adapter_name=args.adapter)
    elif args.validate:
        payload = validate_evidence_file(args.validate, adapter_name=args.adapter)
    elif args.example_bundle:
        payload = example_bundle()
    else:
        payload = adapters_status() if args.status or args.json_output else {}
    if not payload:
        parser.print_help()
        return 0
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("status") != "fail" else 1


def _adapter_for(payload: dict[str, Any], adapter_name: str | None) -> BaseEvidenceAdapter:
    if adapter_name:
        return ADAPTERS[adapter_name]()
    evidence_type = str(payload.get("evidence_type") or "")
    if evidence_type in ADAPTERS:
        return ADAPTERS[evidence_type]()
    raw_format = _scanner_format(payload)
    if raw_format != "unknown":
        return ScannerEvidenceAdapter()
    if {"signature_verified", "signature_algorithm", "algorithm"} & set(payload):
        return SignatureEvidenceAdapter()
    if {"registry_signed", "index_signature_verified"} & set(payload):
        return RegistryEvidenceAdapter()
    if {"sandbox_backend", "sandbox_enforced", "bwrap_backend_enforced"} & set(payload):
        return SandboxEvidenceAdapter()
    if {"external_anchor_configured", "hash_chain_verified", "checkpoint_verified"} & set(payload):
        return AuditAnchorEvidenceAdapter()
    return BaseEvidenceAdapter()


def _scanner_format(payload: dict[str, Any]) -> str:
    if "dependencies" in payload and "vulnerabilities" in payload:
        return "pip-audit"
    if "results" in payload and "packages" in payload:
        return "osv"
    if "matches" in payload:
        return "grype"
    if "vulnerabilities" in payload and isinstance(payload.get("vulnerabilities"), list):
        return "safety"
    if "findings_summary" in payload or "findings" in payload:
        return "enterprise_sca_generic"
    if payload.get("evidence_type") == "scanner":
        return "production_evidence"
    return "unknown"


def _scanner_findings(raw_format: str, payload: dict[str, Any]) -> dict[str, int]:
    summary = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    existing = payload.get("findings_summary")
    if isinstance(existing, dict):
        for key in summary:
            summary[key] = int(existing.get(key) or 0)
        return summary
    items: list[Any] = []
    if raw_format == "grype":
        raw_items = payload.get("matches")
        items = list(raw_items) if isinstance(raw_items, list) else []
        severities = [_nested(item, ["vulnerability", "severity"]) for item in items if isinstance(item, dict)]
    elif raw_format == "osv":
        raw_items = payload.get("results")
        items = list(raw_items) if isinstance(raw_items, list) else []
        severities = [_osv_severity(item) for item in items if isinstance(item, dict)]
    else:
        raw_items = payload.get("vulnerabilities") or payload.get("findings") or []
        items = raw_items if isinstance(raw_items, list) else []
        severities = [str((item or {}).get("severity") or (item or {}).get("level") or "medium") for item in items if isinstance(item, dict)]
    for severity in severities:
        key = str(severity or "medium").lower()
        if key in summary:
            summary[key] += 1
        else:
            summary["medium"] += 1
    return summary


def _osv_severity(item: dict[str, Any]) -> str:
    packages = item.get("packages")
    vulns = item.get("vulns") or item.get("vulnerabilities")
    if isinstance(vulns, list) and vulns:
        raw = vulns[0]
        if isinstance(raw, dict):
            severity = raw.get("database_specific", {}).get("severity") if isinstance(raw.get("database_specific"), dict) else None
            return str(severity or raw.get("severity") or "medium")
    if isinstance(packages, list) and packages:
        return "medium"
    return "low"


def _production_source_allowed(payload: dict[str, Any], source_path: str) -> bool:
    source = str(payload.get("source") or _source_from_path(source_path)).lower()
    return source not in {"offline", "reference_only", "offline/reference_only", "example", "github_hosted_diagnostic"}


def _source_from_path(source_path: str) -> str:
    if _has_example_path(source_path):
        return "reference_only"
    return "local_file"


def _has_example_path(source_path: str) -> bool:
    lower = source_path.replace("\\", "/").lower()
    return ".example" in lower or lower.endswith("/example.json")


def _is_stale(generated_at: str) -> bool:
    try:
        parsed = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return parsed < datetime.now(UTC) - timedelta(days=STALE_REPORT_DAYS)


def _expires_at(generated_at: str) -> str:
    try:
        parsed = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(UTC)
    return (parsed + timedelta(days=STALE_REPORT_DAYS)).isoformat()


def _production_pass(normalized: dict[str, Any]) -> bool | None:
    evidence_type = normalized.get("evidence_type")
    if evidence_type == "scanner":
        return scanner_evidence_passes(normalized)
    if evidence_type == "signature":
        return signature_evidence_passes(normalized)
    if evidence_type == "registry":
        return registry_evidence_passes(normalized)
    if evidence_type == "sandbox":
        return sandbox_evidence_passes(normalized)
    if evidence_type == "audit_anchor":
        return audit_anchor_evidence_passes(normalized)
    return None


def _checks_from_mapping(payload: dict[str, Any]) -> list[dict[str, Any]]:
    checks = []
    for key, value in sorted(payload.items()):
        if key.startswith("check_"):
            checks.append({"name": key[6:], "status": "pass" if _bool(value) else "fail"})
        elif key in {
            "plugin_executed",
            "bwrap_backend_enforced",
            "bwrap_wrapped_command",
            "bwrap_unshared_network",
            "bwrap_private_tmp",
            "host_home_blocked",
            "env_blocked",
            "core_blocked",
            "code_readonly",
            "private_tmp_writable",
            "host_tmp_not_leaked",
            "direct_network_blocked",
            "data_write_allowed",
            "audit_records_present",
        }:
            checks.append({"name": key, "status": "pass" if _bool(value) else "fail"})
    return checks


def _nested(value: dict[str, Any], keys: list[str]) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "pass", "passed", "verified", "enabled"}:
            return True
        if normalized in {"false", "0", "no", "fail", "failed", "missing", "disabled"}:
            return False
    if value is None:
        return default
    return bool(value)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
