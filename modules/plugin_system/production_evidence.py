from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .tool_contracts import utc_now


PRODUCTION_EVIDENCE_SCHEMA_VERSION = "2026-06-rc1"
EVIDENCE_TYPES = {
    "sandbox",
    "registry",
    "signature",
    "governance",
    "confirmation",
    "audit_anchor",
    "scanner",
    "ci",
}


@dataclass(frozen=True)
class SandboxEvidence:
    evidence_type: str = "sandbox"
    schema_version: str = PRODUCTION_EVIDENCE_SCHEMA_VERSION
    environment_class: str = "unknown"
    mode: str = "production-required"
    status: str = "unknown"
    production_blocking: bool = True
    sandbox_backend: dict[str, Any] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)
    generated_at: str = field(default_factory=utc_now)
    source: str = "unknown"
    artifact_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(asdict(self))


@dataclass(frozen=True)
class RegistryEvidence:
    evidence_type: str = "registry"
    schema_version: str = PRODUCTION_EVIDENCE_SCHEMA_VERSION
    status: str = "unknown"
    registry_signed: bool = False
    package_sha256_verified: bool = False
    unsigned_registry_rejected: bool = False
    tampered_registry_rejected: bool = False
    revoked_version_rejected: bool = False
    rollback_or_downgrade_rejected: bool = False
    generated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SignatureEvidence:
    evidence_type: str = "signature"
    schema_version: str = PRODUCTION_EVIDENCE_SCHEMA_VERSION
    status: str = "unknown"
    signature_verified: bool = False
    signature_algorithm: str = "unknown"
    signer_key_id: str = "unknown"
    key_revoked: bool = False
    package_digest_verified: bool = False
    production_evidence: bool = False
    policy_decision: str = "unknown"
    generated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GovernanceEvidence:
    evidence_type: str = "governance"
    schema_version: str = PRODUCTION_EVIDENCE_SCHEMA_VERSION
    status: str = "unknown"
    governance_store_kind: str = "unknown"
    persistent: bool = False
    multi_instance_safe: bool = False
    confirmation_provider_kind: str = "unknown"
    local_only: bool = True
    production_recommended: bool = False
    warnings: list[str] = field(default_factory=list)
    generated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConfirmationEvidence:
    evidence_type: str = "confirmation"
    schema_version: str = PRODUCTION_EVIDENCE_SCHEMA_VERSION
    status: str = "unknown"
    provider_kind: str = "unknown"
    token_bound_to_args_hash: bool = False
    token_expiry_enforced: bool = False
    denial_supported: bool = False
    expiration_supported: bool = False
    production_recommended: bool = False
    generated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AuditAnchorEvidence:
    evidence_type: str = "audit_anchor"
    schema_version: str = PRODUCTION_EVIDENCE_SCHEMA_VERSION
    status: str = "unknown"
    hash_chain_verified: bool = False
    checkpoint_verified: bool = False
    external_anchor_configured: bool = False
    production_immutability: bool = False
    anchor_type: str = "local_or_none"
    generated_at: str = field(default_factory=utc_now)
    controlled_risk_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ScannerEvidence:
    evidence_type: str = "scanner"
    schema_version: str = PRODUCTION_EVIDENCE_SCHEMA_VERSION
    status: str = "unknown"
    scanner_name: str = "unknown"
    scanner_version: str = "unknown"
    production_evidence: bool = False
    source: str = "unknown"
    findings_summary: dict[str, Any] = field(default_factory=dict)
    policy_decision: str = "unknown"
    generated_at: str = field(default_factory=utc_now)
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(asdict(self))


@dataclass(frozen=True)
class CiEvidence:
    evidence_type: str = "ci"
    schema_version: str = PRODUCTION_EVIDENCE_SCHEMA_VERSION
    status: str = "unknown"
    head_sha: str = ""
    run_url: str = ""
    matrix: dict[str, Any] = field(default_factory=dict)
    coverage_percent: float | int | None = None
    generated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(asdict(self))


@dataclass(frozen=True)
class ProductionEvidenceBundle:
    schema_version: str = PRODUCTION_EVIDENCE_SCHEMA_VERSION
    generated_at: str = field(default_factory=utc_now)
    evidences: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    production_blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evidence_schema() -> dict[str, Any]:
    return {
        "schema_version": PRODUCTION_EVIDENCE_SCHEMA_VERSION,
        "evidence_types": sorted(EVIDENCE_TYPES),
        "bundle": ProductionEvidenceBundle(
            evidences=[
                SandboxEvidence().to_dict(),
                RegistryEvidence().to_dict(),
                SignatureEvidence().to_dict(),
                GovernanceEvidence().to_dict(),
                ConfirmationEvidence().to_dict(),
                AuditAnchorEvidence().to_dict(),
                ScannerEvidence().to_dict(),
                CiEvidence().to_dict(),
            ],
            summary={
                "note": "Examples are schema shape only. They are not production evidence.",
            },
        ).to_dict(),
        "production_rules": {
            "sandbox": "status=pass, mode=production-required, sandbox_backend.enforced=true, production_blocking=false, target/self-hosted evidence, all required bwrap checks pass",
            "scanner": "production_evidence=true, source not offline/reference_only, policy_decision=pass",
            "signature": "Ed25519 signature_verified=true, package_digest_verified=true, key_revoked=false, policy_decision=pass",
            "registry": "status=pass with signed registry, package digest verification, unsigned/tampered/revoked/rollback rejection",
            "audit_anchor": "external_anchor_configured=true and production_immutability=true",
            "governance": "persistent=true, multi_instance_safe=true, production_recommended=true",
            "confirmation": "production_recommended=true with token binding, expiry, denial, and expiration support",
        },
    }


def validate_evidence_dict(evidence: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_evidence_dict(evidence)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if normalized.get("bundle"):
        for item in normalized.get("evidences", []):
            item_result = validate_evidence_dict(item)
            errors.extend(
                _prefix_issue(issue, str(item.get("evidence_type") or "unknown"))
                for issue in item_result.get("errors", [])
            )
            warnings.extend(
                _prefix_issue(issue, str(item.get("evidence_type") or "unknown"))
                for issue in item_result.get("warnings", [])
            )
        return {
            "status": "pass" if not errors else "fail",
            "schema_version": normalized.get("schema_version"),
            "errors": errors,
            "warnings": warnings,
            "normalized": normalized,
            "generated_at": utc_now(),
        }
    evidence_type = normalized.get("evidence_type")
    if evidence_type not in EVIDENCE_TYPES:
        errors.append({"code": "unknown_evidence_type", "message": f"unknown evidence_type: {evidence_type}"})
    schema_version = str(normalized.get("schema_version") or "")
    if not schema_version:
        errors.append({"code": "missing_schema_version", "message": "schema_version is required"})
    if schema_version and schema_version != PRODUCTION_EVIDENCE_SCHEMA_VERSION:
        warnings.append(
            {
                "code": "schema_version_mismatch",
                "message": f"expected {PRODUCTION_EVIDENCE_SCHEMA_VERSION}, got {schema_version}",
            }
        )
    errors.extend(_required_field_errors(normalized, _required_fields(str(evidence_type))))
    warnings.extend(_safety_warnings(normalized))
    return {
        "status": "pass" if not errors else "fail",
        "schema_version": schema_version,
        "evidence_type": evidence_type,
        "errors": errors,
        "warnings": warnings,
        "normalized": normalized,
        "generated_at": utc_now(),
    }


def normalize_evidence_dict(evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        return {"evidence_type": "unknown", "schema_version": "", "status": "invalid"}
    if "evidences" in evidence:
        evidences = evidence.get("evidences")
        evidences = evidences if isinstance(evidences, list) else []
        return {
            "bundle": True,
            "schema_version": str(evidence.get("schema_version") or PRODUCTION_EVIDENCE_SCHEMA_VERSION),
            "generated_at": str(evidence.get("generated_at") or utc_now()),
            "evidences": [
                normalize_evidence_dict(item) for item in evidences if isinstance(item, dict)
            ],
            "summary": evidence.get("summary") if isinstance(evidence.get("summary"), dict) else {},
            "production_blockers": _string_list(evidence.get("production_blockers")),
            "warnings": _string_list(evidence.get("warnings")),
        }
    evidence_type = str(evidence.get("evidence_type") or _infer_legacy_evidence_type(evidence))
    normalized = {
        **evidence,
        "evidence_type": evidence_type,
        "schema_version": str(evidence.get("schema_version") or PRODUCTION_EVIDENCE_SCHEMA_VERSION),
        "status": str(evidence.get("status") or "unknown"),
        "generated_at": str(evidence.get("generated_at") or utc_now()),
    }
    if evidence_type == "sandbox":
        backend = normalized.get("sandbox_backend")
        if not isinstance(backend, dict):
            backend = {
                "name": normalized.get("sandbox_backend_name") or normalized.get("backend") or "unknown",
                "enforced": _bool(normalized.get("sandbox_enforced") or normalized.get("enforced")),
                "capabilities": normalized.get("capabilities") if isinstance(normalized.get("capabilities"), dict) else {},
            }
        normalized["sandbox_backend"] = backend
        normalized["production_blocking"] = _bool(normalized.get("production_blocking"), default=True)
    if evidence_type == "governance":
        if "governance_store_kind" not in normalized and "store_kind" in normalized:
            normalized["governance_store_kind"] = normalized.get("store_kind")
        normalized["persistent"] = _bool(normalized.get("persistent"))
        normalized["multi_instance_safe"] = _bool(normalized.get("multi_instance_safe"))
        normalized["production_recommended"] = _bool(normalized.get("production_recommended"))
        normalized["local_only"] = _bool(normalized.get("local_only"), default=not normalized["multi_instance_safe"])
    if evidence_type == "confirmation":
        normalized["production_recommended"] = _bool(normalized.get("production_recommended"))
        normalized["token_bound_to_args_hash"] = _bool(normalized.get("token_bound_to_args_hash"))
        normalized["token_expiry_enforced"] = _bool(normalized.get("token_expiry_enforced"))
        normalized["denial_supported"] = _bool(normalized.get("denial_supported"))
        normalized["expiration_supported"] = _bool(normalized.get("expiration_supported"))
    if evidence_type == "audit_anchor":
        normalized["external_anchor_configured"] = _bool(normalized.get("external_anchor_configured"))
        normalized["production_immutability"] = (
            _bool(normalized.get("production_immutability"))
            if normalized["external_anchor_configured"]
            else False
        )
        normalized["hash_chain_verified"] = _bool(normalized.get("hash_chain_verified"))
        normalized["checkpoint_verified"] = _bool(normalized.get("checkpoint_verified"))
    if evidence_type == "scanner":
        normalized["production_evidence"] = _bool(normalized.get("production_evidence"))
        normalized["policy_decision"] = str(normalized.get("policy_decision") or normalized.get("decision") or "unknown")
        normalized["source"] = str(normalized.get("source") or "unknown")
    if evidence_type == "registry":
        for key in (
            "registry_signed",
            "package_sha256_verified",
            "unsigned_registry_rejected",
            "tampered_registry_rejected",
            "revoked_version_rejected",
            "rollback_or_downgrade_rejected",
        ):
            normalized[key] = _bool(normalized.get(key))
    if evidence_type == "signature":
        normalized["signature_verified"] = _bool(normalized.get("signature_verified"))
        normalized["package_digest_verified"] = _bool(normalized.get("package_digest_verified"))
        normalized["key_revoked"] = _bool(normalized.get("key_revoked"))
        normalized["production_evidence"] = _bool(normalized.get("production_evidence"))
        normalized["policy_decision"] = str(normalized.get("policy_decision") or normalized.get("decision") or "unknown")
        normalized["signature_algorithm"] = str(normalized.get("signature_algorithm") or normalized.get("algorithm") or "unknown")
    return _json_safe_dict(normalized)


def evidence_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = normalize_evidence_dict(payload)
    if normalized.get("bundle"):
        return [item for item in normalized.get("evidences", []) if isinstance(item, dict)]
    return [normalized]


def first_evidence(payload: dict[str, Any] | None, evidence_type: str) -> dict[str, Any] | None:
    if not payload:
        return None
    for item in evidence_list(payload):
        if item.get("evidence_type") == evidence_type:
            return item
    return None


def is_legacy_evidence(evidence: Any) -> bool:
    return isinstance(evidence, dict) and "schema_version" not in evidence and "evidence_type" not in evidence


def sandbox_evidence_passes(evidence: dict[str, Any] | None) -> bool | None:
    if not evidence:
        return None
    normalized = normalize_evidence_dict(evidence)
    if normalized.get("evidence_type") != "sandbox":
        return False
    backend = normalized.get("sandbox_backend")
    backend = backend if isinstance(backend, dict) else {}
    environment_class = str(normalized.get("environment_class") or "unknown").lower()
    source = str(normalized.get("source") or "").lower()
    checks = _checks_by_name(normalized.get("checks"))
    required_checks = {
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
    }
    if environment_class in {"github_hosted", "github_hosted_diagnostic"} or source == "github_hosted_diagnostic":
        return False
    if environment_class and environment_class not in {
        "target_linux",
        "self_hosted",
        "unknown_but_not_github_hosted",
        "unknown",
    }:
        return False
    if not checks:
        return False
    if not all(checks.get(name) is True for name in required_checks):
        return False
    return bool(
        normalized.get("status") == "pass"
        and normalized.get("mode") == "production-required"
        and backend.get("enforced") is True
        and normalized.get("production_blocking") is False
    )


def scanner_evidence_passes(evidence: dict[str, Any] | None) -> bool | None:
    if not evidence:
        return None
    normalized = normalize_evidence_dict(evidence)
    if normalized.get("evidence_type") != "scanner":
        return False
    source = str(normalized.get("source") or "").lower()
    return bool(
        normalized.get("production_evidence") is True
        and source not in {"offline", "reference_only", "offline/reference_only"}
        and normalized.get("policy_decision") == "pass"
    )


def audit_anchor_evidence_passes(evidence: dict[str, Any] | None) -> bool | None:
    if not evidence:
        return None
    normalized = normalize_evidence_dict(evidence)
    if normalized.get("evidence_type") != "audit_anchor":
        return False
    return bool(
        normalized.get("external_anchor_configured") is True
        and normalized.get("production_immutability") is True
    )


def governance_evidence_passes(evidence: dict[str, Any] | None) -> bool | None:
    if not evidence:
        return None
    normalized = normalize_evidence_dict(evidence)
    if normalized.get("evidence_type") != "governance":
        return False
    return bool(
        normalized.get("persistent") is True
        and normalized.get("multi_instance_safe") is True
        and normalized.get("production_recommended") is True
    )


def confirmation_evidence_passes(evidence: dict[str, Any] | None) -> bool | None:
    if not evidence:
        return None
    normalized = normalize_evidence_dict(evidence)
    if normalized.get("evidence_type") != "confirmation":
        return False
    return bool(
        normalized.get("production_recommended") is True
        and normalized.get("token_bound_to_args_hash") is True
        and normalized.get("token_expiry_enforced") is True
        and normalized.get("denial_supported") is True
        and normalized.get("expiration_supported") is True
    )


def registry_evidence_passes(evidence: dict[str, Any] | None) -> bool | None:
    if not evidence:
        return None
    normalized = normalize_evidence_dict(evidence)
    if normalized.get("evidence_type") != "registry":
        return False
    return bool(
        normalized.get("status") == "pass"
        and normalized.get("registry_signed") is True
        and normalized.get("package_sha256_verified") is True
        and normalized.get("unsigned_registry_rejected") is True
        and normalized.get("tampered_registry_rejected") is True
        and normalized.get("revoked_version_rejected") is True
        and normalized.get("rollback_or_downgrade_rejected") is True
    )


def signature_evidence_passes(evidence: dict[str, Any] | None) -> bool | None:
    if not evidence:
        return None
    normalized = normalize_evidence_dict(evidence)
    if normalized.get("evidence_type") != "signature":
        return False
    algorithm = str(normalized.get("signature_algorithm") or "").lower()
    return bool(
        normalized.get("status") == "pass"
        and normalized.get("signature_verified") is True
        and normalized.get("package_digest_verified") is True
        and normalized.get("key_revoked") is False
        and normalized.get("production_evidence") is True
        and normalized.get("policy_decision") == "pass"
        and "ed25519" in algorithm
        and "hmac" not in algorithm
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plugin production evidence schema helper")
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--validate")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    if args.schema:
        payload = evidence_schema()
    elif args.validate:
        payload = validate_evidence_dict(_read_json(Path(args.validate)))
    else:
        parser.print_help()
        return 2
    if args.json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("status", "pass") != "fail" else 1


def _required_fields(evidence_type: str) -> tuple[str, ...]:
    table = {
        "sandbox": ("evidence_type", "schema_version", "mode", "status", "sandbox_backend", "generated_at", "source"),
        "registry": ("evidence_type", "schema_version", "status", "registry_signed", "generated_at"),
        "signature": ("evidence_type", "schema_version", "status", "signature_verified", "signature_algorithm", "generated_at"),
        "governance": ("evidence_type", "schema_version", "status", "persistent", "multi_instance_safe", "generated_at"),
        "confirmation": ("evidence_type", "schema_version", "status", "provider_kind", "generated_at"),
        "audit_anchor": ("evidence_type", "schema_version", "status", "external_anchor_configured", "generated_at"),
        "scanner": ("evidence_type", "schema_version", "status", "scanner_name", "policy_decision", "generated_at"),
        "ci": ("evidence_type", "schema_version", "status", "head_sha", "generated_at"),
    }
    return table.get(evidence_type, ("evidence_type", "schema_version", "status"))


def _required_field_errors(payload: dict[str, Any], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    errors = []
    for field_name in fields:
        if field_name not in payload or payload.get(field_name) in (None, ""):
            errors.append({"code": "missing_required_field", "message": f"{field_name} is required"})
    return errors


def _safety_warnings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_type = payload.get("evidence_type")
    warnings = []
    if evidence_type == "scanner" and not scanner_evidence_passes(payload):
        warnings.append(
            {
                "code": "scanner_not_production_evidence",
                "message": "offline/reference scanner evidence is not production scanner evidence",
            }
        )
    if evidence_type == "audit_anchor" and payload.get("external_anchor_configured") is not True:
        warnings.append(
            {
                "code": "local_checkpoint_not_immutable",
                "message": "local audit checkpoint is integrity evidence only, not immutable production audit",
            }
        )
    if evidence_type == "sandbox" and payload.get("source") == "github_hosted_diagnostic":
        warnings.append(
            {
                "code": "diagnostic_not_target_sandbox",
                "message": "GitHub-hosted diagnostics are not target production sandbox evidence",
            }
        )
    if evidence_type == "signature" and not signature_evidence_passes(payload):
        warnings.append(
            {
                "code": "signature_not_production_trust",
                "message": "only non-revoked Ed25519 package signatures with digest verification can pass production trust",
            }
        )
    return warnings


def _prefix_issue(issue: dict[str, Any], evidence_type: str) -> dict[str, Any]:
    return {**issue, "evidence_type": evidence_type}


def _infer_legacy_evidence_type(evidence: dict[str, Any]) -> str:
    keys = set(evidence)
    if keys & {"sandbox_enforced", "sandbox_backend", "strong_sandbox"}:
        return "sandbox"
    if keys & {"signature_verified", "signature_algorithm", "signer_key_id"}:
        return "signature"
    if keys & {"registry_signed", "signed", "index_signature_verified"}:
        return "registry"
    if keys & {"persistent", "multi_instance_safe", "governance_store_kind", "store_kind"}:
        return "governance"
    if keys & {"provider_kind", "token_bound_to_args_hash", "external_ui"}:
        return "confirmation"
    if keys & {"external_anchor_configured", "hash_chain_verified", "checkpoint_verified"}:
        return "audit_anchor"
    if keys & {"scanner_name", "policy_decision", "findings_summary"}:
        return "scanner"
    return "unknown"


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


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _json_safe_dict(value: dict[str, Any]) -> dict[str, Any]:
    try:
        json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):
        return {key: str(item) for key, item in value.items()}
    return value


def _drop_none(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def _checks_by_name(value: Any) -> dict[str, bool]:
    if not isinstance(value, list):
        return {}
    checks: dict[str, bool] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("check_id") or "")
        if not name:
            continue
        status = item.get("status")
        checks[name] = bool(item.get("ok") is True or status in {"pass", "passed", True})
    return checks


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
