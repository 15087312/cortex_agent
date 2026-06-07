from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


GO = "GO"
CONTROLLED_GO = "CONTROLLED_GO"
NO_GO = "NO_GO"


@dataclass(frozen=True)
class GateInput:
    doctor: dict[str, Any] | None = None
    bwrap: dict[str, Any] | None = None
    audit: dict[str, Any] | None = None
    scan: dict[str, Any] | None = None
    registry: dict[str, Any] | None = None
    revocation: dict[str, Any] | None = None
    quarantine: dict[str, Any] | None = None
    rollback: dict[str, Any] | None = None
    ci: dict[str, Any] | None = None
    risk_acceptance: dict[str, Any] | None = None


@dataclass(frozen=True)
class GateFinding:
    check_id: str
    status: str
    reason: str
    production_blocking: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "status": self.status,
            "reason": self.reason,
            "production_blocking": self.production_blocking,
        }


@dataclass(frozen=True)
class GateResult:
    decision: str
    reason: str
    findings: list[GateFinding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "findings": [item.to_dict() for item in self.findings],
        }


def evaluate_release_gate(inputs: GateInput) -> GateResult:
    findings: list[GateFinding] = []
    risks_accepted = _risks_accepted(inputs.risk_acceptance)
    scan_finding = _scan_finding(inputs.scan, risks_accepted)
    license_finding = _license_scan_finding(inputs.scan, risks_accepted)
    scanner_configured_finding = _scanner_configured_finding(inputs.scan, risks_accepted)
    audit_findings = _audit_findings(inputs.audit, risks_accepted)

    findings.append(_ci_finding(inputs.ci, risks_accepted))
    findings.extend(_doctor_findings(inputs.doctor, suppress_ids=_suppressed_doctor_ids(scan_finding, audit_findings)))
    findings.append(_bwrap_finding(inputs.bwrap))
    findings.extend(audit_findings)
    findings.append(scanner_configured_finding)
    findings.append(scan_finding)
    findings.append(license_finding)
    findings.append(_status_finding("registry.verify", inputs.registry, require_pass=True))
    findings.append(_status_finding("revocation.drill", inputs.revocation, require_pass=True))
    findings.append(_status_finding("quarantine.drill", inputs.quarantine, require_pass=True))
    findings.append(_status_finding("rollback.drill", inputs.rollback, require_pass=True))
    findings = [item for item in findings if item is not None]

    if any(item.production_blocking or item.status == "fail" for item in findings):
        return GateResult(
            decision=NO_GO,
            reason="one or more production-blocking release checks failed",
            findings=findings,
        )

    controlled_reasons = [
        item.reason
        for item in findings
        if item.status in {"warn", "skipped"} or "accepted risk" in item.reason.lower()
    ]
    if controlled_reasons:
        return GateResult(
            decision=CONTROLLED_GO,
            reason="release is gated by accepted risks or incomplete non-blocking evidence",
            findings=findings,
        )

    return GateResult(decision=GO, reason="all release gate evidence passed", findings=findings)


def load_gate_input(
    *,
    doctor: str | Path | None = None,
    bwrap: str | Path | None = None,
    audit: str | Path | None = None,
    scan: str | Path | None = None,
    registry: str | Path | None = None,
    revocation: str | Path | None = None,
    quarantine: str | Path | None = None,
    rollback: str | Path | None = None,
    ci: str | Path | None = None,
    risk_acceptance: str | Path | None = None,
) -> GateInput:
    return GateInput(
        doctor=_read_json(doctor),
        bwrap=_read_json(bwrap),
        audit=_read_json(audit),
        scan=_read_json(scan),
        registry=_read_json(registry),
        revocation=_read_json(revocation),
        quarantine=_read_json(quarantine),
        rollback=_read_json(rollback),
        ci=_read_json(ci),
        risk_acceptance=_read_json(risk_acceptance),
    )


def _read_json(path: str | Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"release gate input must be a JSON object: {path}")
    return payload


def _risks_accepted(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    accepted = payload.get("accepted_risks")
    risk_items = accepted if isinstance(accepted, list) and accepted else [payload]
    if not risk_items or not all(isinstance(item, dict) for item in risk_items):
        return False
    return all(_risk_item_formally_accepted(item) for item in risk_items)


def _risk_item_formally_accepted(item: dict[str, Any]) -> bool:
    if item.get("accepted") is not True and item.get("status") != "accepted":
        return False
    required_text = ["accepted_by", "role", "scope", "expiry"]
    if any(not str(item.get(key, "")).strip() for key in required_text):
        return False
    controls = item.get("compensating_controls")
    if not isinstance(controls, list) or not controls:
        return False
    try:
        expiry = datetime.fromisoformat(str(item["expiry"]).replace("Z", "+00:00"))
    except ValueError:
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    return expiry > datetime.now(UTC)


def _ci_finding(payload: dict[str, Any] | None, risks_accepted: bool) -> GateFinding:
    if payload is None:
        return GateFinding(
            "ci.matrix",
            "warn" if risks_accepted else "fail",
            "CI result metadata is missing" + ("; accepted risk" if risks_accepted else ""),
            production_blocking=not risks_accepted,
        )
    status = _ci_status(payload)
    return GateFinding(
        "ci.matrix",
        "pass" if status == "pass" else "fail",
        str(payload.get("reason") or payload.get("summary") or f"CI status={status}"),
        production_blocking=status != "pass",
    )


def _ci_status(payload: dict[str, Any]) -> str:
    run_status = str(payload.get("status", "")).strip().lower()
    if run_status == "completed":
        conclusion = str(payload.get("conclusion", "")).strip().lower()
        if conclusion not in {"success", "ok", "passed", "pass"}:
            return "fail"
        return "pass" if _ci_results_pass(payload) else "fail"

    status = _normalized_status(payload)
    if status == "pass" and not _ci_results_pass(payload):
        return "fail"
    return status


def _ci_results_pass(payload: dict[str, Any]) -> bool:
    matrix_keys = [
        "linux_python_3_11",
        "linux_python_3_12",
        "linux_python_3_13",
        "windows_python_3_11",
        "windows_python_3_12",
        "windows_python_3_13",
    ]
    result_keys = ["ruff_result", "mypy_result", "unittest_result", "coverage_result"]
    return _optional_group_passes(payload, matrix_keys) and _optional_group_passes(payload, result_keys)


def _optional_group_passes(payload: dict[str, Any], keys: list[str]) -> bool:
    if not any(key in payload for key in keys):
        return True
    return all(_is_pass_value(payload.get(key)) for key in keys)


def _is_pass_value(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() in {"success", "ok", "passed", "pass"}


def _doctor_findings(payload: dict[str, Any] | None, *, suppress_ids: set[str] | None = None) -> list[GateFinding]:
    if payload is None:
        return [
            GateFinding(
                "doctor.production",
                "fail",
                "Production Doctor evidence is missing",
                production_blocking=True,
            )
        ]
    suppressed = suppress_ids or set()
    findings: list[GateFinding] = []
    suppressed_blocking = False
    for item in payload.get("checks", []):
        if not isinstance(item, dict):
            continue
        check_id = str(item.get("check_id", "doctor.unknown"))
        status = str(item.get("status", "fail"))
        reason = str(item.get("reason", ""))
        blocking = bool(item.get("production_blocking"))
        if check_id in suppressed:
            suppressed_blocking = suppressed_blocking or blocking
            continue
        findings.append(GateFinding(check_id, status, reason, production_blocking=blocking))
    if payload.get("production_blocking") and any(item.production_blocking for item in findings):
        findings.append(
            GateFinding(
                "doctor.production_blocking",
                "fail",
                "Production Doctor reported production_blocking=true",
                production_blocking=True,
            )
        )
    elif payload.get("production_blocking") and suppressed_blocking:
        findings.append(
            GateFinding(
                "doctor.production_blocking",
                "pass",
                "Production Doctor blocking checks are represented by specific release gate findings",
            )
        )
    return findings or [GateFinding("doctor.production", _normalized_status(payload), "doctor evidence parsed")]


def _status_finding(check_id: str, payload: dict[str, Any] | None, *, require_pass: bool) -> GateFinding:
    if payload is None:
        return GateFinding(check_id, "fail", f"{check_id} evidence is missing", production_blocking=require_pass)
    status = _normalized_status(payload)
    if check_id == "bwrap.validation" and status == "skipped":
        return GateFinding(
            check_id,
            "fail",
            "bwrap validation was skipped; target Linux sandbox evidence is required for full production",
            production_blocking=True,
        )
    return GateFinding(
        check_id,
        "pass" if status == "pass" else "fail",
        str(payload.get("reason") or payload.get("summary") or f"{check_id} status={status}"),
        production_blocking=require_pass and status != "pass",
    )


def _bwrap_finding(payload: dict[str, Any] | None) -> GateFinding:
    check_id = "bwrap.validation"
    if payload is None:
        return GateFinding(check_id, "fail", f"{check_id} evidence is missing", production_blocking=True)

    status = _normalized_status(payload)
    mode = str(payload.get("mode", "")).strip().lower()
    environment_class = str(payload.get("environment_class", "")).strip().lower()

    if mode == "diagnostic":
        return GateFinding(
            "sandbox.target_linux_required",
            "fail",
            "GitHub-hosted or diagnostic bwrap evidence cannot satisfy target production Linux+bwrap validation",
            production_blocking=True,
        )

    if environment_class == "github_hosted":
        return GateFinding(
            "sandbox.target_linux_required",
            "fail",
            "GitHub-hosted runner evidence cannot satisfy target production Linux+bwrap validation",
            production_blocking=True,
        )

    if status == "skipped":
        return GateFinding(
            check_id,
            "fail",
            "bwrap validation was skipped; target Linux sandbox evidence is required for full production",
            production_blocking=True,
        )

    if mode and mode != "production-required":
        return GateFinding(
            check_id,
            "fail",
            f"bwrap evidence mode={mode} is not production-required",
            production_blocking=True,
        )

    if status != "pass":
        failure_id = _bwrap_failure_check_id(payload)
        return GateFinding(
            failure_id,
            "fail",
            str(payload.get("reason") or payload.get("summary") or f"{check_id} status={status}"),
            production_blocking=True,
        )

    backend = payload.get("sandbox_backend")
    backend_ok = _bwrap_backend_enforced(backend)
    checks_ok = _bwrap_critical_checks_pass(payload.get("checks"))
    if backend_ok and checks_ok:
        return GateFinding(check_id, "pass", "production-required target Linux+bwrap validation passed")

    return GateFinding(
        check_id,
        "fail",
        "bwrap evidence is missing enforced backend capabilities or critical pass checks",
        production_blocking=True,
    )


def _bwrap_failure_check_id(payload: dict[str, Any]) -> str:
    backend = payload.get("sandbox_backend")
    if _bwrap_backend_enforced(backend):
        preflight = payload.get("preflight")
        if isinstance(preflight, dict) and preflight.get("status") != "pass":
            reason = str(payload.get("reason") or "").lower()
            if preflight.get("import_runtime") == "fail" or "import" in reason:
                return "bwrap.validation.runtime_import_failed"
            return "bwrap.validation.preflight_failed"
        result = payload.get("result")
        if isinstance(result, dict):
            if result.get("worker_started") is False or result.get("json_result_received") is False:
                return "bwrap.validation.worker_execution_failed"
            if str(result.get("error_type", "")).lower() in {"workernooutput", "workerimporterror", "bwraplauncherror", "runtimemounterror"}:
                return "bwrap.validation.worker_execution_failed"
    return "bwrap.validation"


def _bwrap_backend_enforced(backend: Any) -> bool:
    if not isinstance(backend, dict) or backend.get("enforced") is not True:
        return False
    capabilities = backend.get("capabilities")
    if not isinstance(capabilities, dict):
        return False
    required = ["process_containment", "resource_limits", "filesystem_isolation", "network_isolation"]
    return all(capabilities.get(name) is True for name in required)


def _bwrap_critical_checks_pass(checks: Any) -> bool:
    if not isinstance(checks, list):
        return False
    required = {
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
    statuses = {
        str(item.get("check_id")): str(item.get("status"))
        for item in checks
        if isinstance(item, dict)
    }
    return all(statuses.get(check_id) == "pass" for check_id in required)


def _audit_findings(payload: dict[str, Any] | None, risks_accepted: bool) -> list[GateFinding]:
    if payload is None:
        return [GateFinding("audit.verify", "fail", "audit verification evidence is missing", production_blocking=True)]

    local_verified = _audit_local_verified(payload)
    if not local_verified:
        return [
            GateFinding(
                "audit.verify",
                "fail",
                str(payload.get("reason") or "audit hash chain or checkpoint verification failed"),
                production_blocking=True,
            )
        ]

    findings = [GateFinding("audit.verify", "pass", "audit hash chain and checkpoint verified")]
    external_anchor = payload.get("external_anchor_configured") is True
    immutable = payload.get("production_immutability") is True
    if external_anchor and immutable:
        findings.append(GateFinding("audit.external_anchor", "pass", "external audit anchor and production immutability verified"))
    else:
        findings.append(
            GateFinding(
                "audit.external_anchor_missing",
                "warn" if risks_accepted else "fail",
                (
                    "external append-only/SIEM/WORM audit anchor is missing; "
                    "local checkpoint is tamper-evident only"
                    + ("; accepted risk" if risks_accepted else "")
                ),
                production_blocking=not risks_accepted,
            )
        )
    return findings


def _audit_local_verified(payload: dict[str, Any]) -> bool:
    if payload.get("hash_chain_verified") is True and payload.get("checkpoint_verified") is True:
        return True
    status = _normalized_status(payload)
    checkpoint = payload.get("checkpoint")
    checkpoint_status = checkpoint.get("status") if isinstance(checkpoint, dict) else None
    return status == "pass" and checkpoint_status in {None, "success", "pass"}


def _suppressed_doctor_ids(scan_finding: GateFinding, audit_findings: list[GateFinding]) -> set[str]:
    suppressed: set[str] = set()
    if scan_finding.check_id == "scanner.policy":
        suppressed.add("scanner.configured")
    if any(item.check_id == "audit.external_anchor_missing" for item in audit_findings):
        suppressed.add("audit.external_anchor")
    return suppressed


def _scanner_configured_finding(payload: dict[str, Any] | None, risks_accepted: bool) -> GateFinding:
    if _real_scanner_report(payload):
        return GateFinding("scanner.configured", "pass", "real vulnerability scanner evidence is configured")
    reason = "real vulnerability/license scanner evidence is missing"
    if payload is not None:
        source = str(payload.get("source") or payload.get("scanner_name") or payload.get("status") or "unknown")
        reason = f"scanner evidence is not production evidence: {source}"
    return GateFinding(
        "scanner.configured",
        "warn" if risks_accepted else "fail",
        reason + ("; accepted risk" if risks_accepted else ""),
        production_blocking=not risks_accepted,
    )


def _scan_finding(payload: dict[str, Any] | None, risks_accepted: bool) -> GateFinding:
    if payload is None:
        return GateFinding(
            "scanner.policy",
            "warn" if risks_accepted else "fail",
            "scanner evidence is missing" + ("; accepted risk" if risks_accepted else ""),
            production_blocking=not risks_accepted,
        )
    if not _real_scanner_report(payload):
        return GateFinding(
            "scanner.policy",
            "warn" if risks_accepted else "fail",
            "scanner report is not real production scanner evidence" + ("; accepted risk" if risks_accepted else ""),
            production_blocking=not risks_accepted,
        )
    status = _normalized_status(payload)
    policy_decision = str(payload.get("policy_decision", "")).lower()
    passed = status == "pass" or policy_decision == "pass"
    return GateFinding(
        "scanner.policy",
        "pass" if passed else "fail",
        str(payload.get("reason") or f"scanner status={status} policy_decision={policy_decision}"),
        production_blocking=not passed,
    )


def _license_scan_finding(payload: dict[str, Any] | None, risks_accepted: bool) -> GateFinding:
    if not _real_scanner_report(payload):
        return GateFinding(
            "scanner.license",
            "warn" if risks_accepted else "fail",
            "license scanner evidence is missing" + ("; accepted risk" if risks_accepted else ""),
            production_blocking=not risks_accepted,
        )
    coverage = payload.get("coverage")
    license_scan = isinstance(coverage, dict) and coverage.get("license_scan") is True
    if license_scan:
        return GateFinding("scanner.license", "pass", "license scanner evidence is present")
    return GateFinding(
        "scanner.license",
        "warn" if risks_accepted else "fail",
        "pip-audit covers Python dependency vulnerabilities, not license policy"
        + ("; accepted risk" if risks_accepted else ""),
        production_blocking=not risks_accepted,
    )


def _real_scanner_report(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    source = str(payload.get("source", "")).strip().lower()
    scanner_name = str(payload.get("scanner_name", "")).strip().lower()
    status = str(payload.get("status", "")).strip().lower()
    if source in {"offline", "reference_only", "fixture"} or status in {"missing", "reference_only"}:
        return False
    if payload.get("production_evidence") is True and source == "real_scanner":
        return True
    return scanner_name in {"pip-audit", "osv", "safety", "grype"} and source == "real_scanner"


def _normalized_status(payload: dict[str, Any]) -> str:
    for key in ["status", "conclusion", "decision", "policy_decision"]:
        value = payload.get(key)
        if isinstance(value, str) and value:
            normalized = value.strip().lower()
            if normalized in {"success", "ok", "passed", "pass", "go"}:
                return "pass"
            if normalized in {"controlled_go", "warn", "warning"}:
                return "warn"
            if normalized in {"skipped", "skip"}:
                return "skipped"
            return "fail"
    if payload.get("production_blocking"):
        return "fail"
    return "pass"


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate Humanoid AGI plugin RC release gate evidence")
    parser.add_argument("--doctor")
    parser.add_argument("--bwrap")
    parser.add_argument("--audit")
    parser.add_argument("--scan")
    parser.add_argument("--registry")
    parser.add_argument("--revocation")
    parser.add_argument("--quarantine")
    parser.add_argument("--rollback")
    parser.add_argument("--ci")
    parser.add_argument("--risk-acceptance")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    result = evaluate_release_gate(
        load_gate_input(
            doctor=args.doctor,
            bwrap=args.bwrap,
            audit=args.audit,
            scan=args.scan,
            registry=args.registry,
            revocation=args.revocation,
            quarantine=args.quarantine,
            rollback=args.rollback,
            ci=args.ci,
            risk_acceptance=args.risk_acceptance,
        )
    )
    payload = result.to_dict()
    if args.json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"{payload['decision']}: {payload['reason']}")
        for item in payload["findings"]:
            blocking = " blocking" if item["production_blocking"] else ""
            print(f"- [{item['status']}] {item['check_id']}{blocking}: {item['reason']}")
    return 0 if result.decision in {GO, CONTROLLED_GO} else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
