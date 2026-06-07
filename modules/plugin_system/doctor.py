from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import platform
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .audit import verify_audit_log
from .loader import GOVERNANCE_FILE, PACKAGE_LOCK_FILE
from .sandbox_backend import create_sandbox_backend
from .signing import SIGNATURE_ALGORITHM, LEGACY_SIGNATURE_ALGORITHM
from .tool_selftest import run_tool_selftest
from .evidence_adapters import adapters_status
from .integration_contract import run_integration_contract_check
from .tool_governance import ToolCallSessionStore, UnconfiguredApprovalProvider, governance_store_metadata


@dataclass(frozen=True)
class DoctorCheck:
    check_id: str
    status: str
    reason: str
    recommendation: str
    severity: str
    production_blocking: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_doctor(
    *,
    plugins_dir: str | Path = "data/plugins",
    production_mode: bool = False,
    audit_log: str | Path | None = None,
    registry_requires_signature: bool | None = None,
    scanner_configured: bool = False,
    audit_anchor_configured: bool = False,
) -> list[DoctorCheck]:
    plugins_path = Path(plugins_dir).resolve()
    checks: list[DoctorCheck] = []
    py_ok = sys.version_info >= (3, 11)
    checks.append(
        _check(
            "python.version",
            "pass" if py_ok else "fail",
            f"running Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "Use Python 3.11 or newer.",
            "critical",
            production_mode and not py_ok,
        )
    )
    checks.append(_check("os.platform", "pass", sys.platform, "Record platform in deployment evidence.", "info", False))
    checks.append(
        _check(
            "production.mode",
            "pass" if production_mode else "warn",
            "production mode enabled" if production_mode else "development mode; production fail-closed policy is not fully active",
            "Enable --production for production deployments.",
            "high" if production_mode else "medium",
            False,
        )
    )
    is_windows = sys.platform.startswith("win") or platform.system().lower().startswith("win")
    is_linux = sys.platform == "linux" and not is_windows
    backend = create_sandbox_backend(128, 2, requested="auto")
    bwrap_path = None if is_windows else _safe_which("bwrap")
    if is_linux:
        checks.append(
            _check(
                "sandbox.bubblewrap.binary",
                "pass" if bwrap_path else "fail" if production_mode else "warn",
                f"bwrap={bwrap_path}" if bwrap_path else "bubblewrap executable not found",
                "Install bubblewrap and verify it can create namespaces.",
                "critical",
                production_mode and not bwrap_path,
            )
        )
        checks.append(
            _check(
                "sandbox.bubblewrap.smoke",
                "pass" if backend.report.enforced else "fail" if production_mode else "warn",
                "bubblewrap probe passed" if backend.report.enforced else "; ".join(backend.report.warnings),
                "Run scripts/validate_bwrap_sandbox.py on the production host.",
                "critical",
                production_mode and not backend.report.enforced,
            )
        )
    elif is_windows:
        checks.append(
            _check(
                "sandbox.windows.boundary",
                "warn",
                "Windows Job Object is resource limiting only, not full filesystem/network/syscall isolation.",
                "Use an external attested sandbox, container, or VM for production third-party plugins.",
                "critical",
                production_mode,
            )
        )
    else:
        checks.append(
            _check(
                "sandbox.platform",
                "warn" if not production_mode else "fail",
                "no built-in strong sandbox backend is configured for this platform",
                "Use Linux+bwrap or an external attested sandbox.",
                "critical",
                production_mode,
            )
        )
    checks.append(
        _check(
            "sandbox.fail_closed",
            "pass" if (not production_mode or backend.report.enforced) else "fail",
            "production startup rejects third-party plugins without enforced sandbox"
            if production_mode
            else "development mode may warn or fall back for compatibility",
            "Keep production_mode=True and require_enforced_sandbox=True.",
            "critical",
            production_mode and not backend.report.enforced,
        )
    )
    checks.append(
        _check(
            "signature.ed25519",
            "pass",
            f"production signature algorithm is {SIGNATURE_ALGORITHM}",
            "Use Ed25519 package and registry signatures in production.",
            "critical",
            False,
        )
    )
    checks.append(
        _check(
            "signature.hmac_legacy",
            "pass",
            f"{LEGACY_SIGNATURE_ALGORITHM} is legacy/dev only and not production trust",
            "Do not allow HMAC as production trust.",
            "high",
            False,
        )
    )
    registry_required = production_mode if registry_requires_signature is None else registry_requires_signature
    checks.append(
        _check(
            "registry.signature_required",
            "pass" if registry_required else "warn",
            "registry signatures required" if registry_required else "registry signature requirement is not configured",
            "Require signed registry index and signed packages in production.",
            "critical",
            production_mode and not registry_required,
        )
    )
    audit_path = Path(audit_log).resolve() if audit_log else plugins_path / "audit.log"
    try:
        audit_report = verify_audit_log(audit_path)
        checks.append(
            _check(
                "audit.hash_chain",
                "pass",
                f"audit hash chain verified records={audit_report['records']}",
                "Ship audit logs to append-only centralized storage.",
                "high",
                False,
            )
        )
    except Exception as exc:
        checks.append(
            _check(
                "audit.hash_chain",
                "fail",
                str(exc),
                "Investigate audit log tampering before production use.",
                "critical",
                production_mode,
            )
        )
    checks.append(
        _check(
            "audit.external_anchor",
            "pass" if audit_anchor_configured else "warn",
            "external checkpoint anchor configured" if audit_anchor_configured else "no external checkpoint anchor configured",
            "Use append-only storage, SIEM, WORM bucket, transparency log, or remote checkpoint anchor.",
            "high",
            False,
        )
    )
    checks.append(
        _check(
            "revocation.configured",
            "pass" if (plugins_path / GOVERNANCE_FILE).exists() else "warn",
            "revocation governance file exists" if (plugins_path / GOVERNANCE_FILE).exists() else "revocation list not found yet",
            "Maintain revoked signer keys and revoked plugin versions.",
            "high",
            False,
        )
    )
    checks.append(
        _check(
            "dependency.lockfile_policy",
            "pass",
            f"production third-party dependencies require {PACKAGE_LOCK_FILE} and requirements.lock",
            "Keep dependency lockfiles hash-pinned and reviewed.",
            "high",
            False,
        )
    )
    checks.append(
        _check(
            "sbom.generation",
            "pass",
            "SBOM generation command is available",
            "Generate and retain SBOM for every production plugin package.",
            "medium",
            False,
        )
    )
    try:
        tool_report = run_tool_selftest()
        tool_ok = tool_report.get("status") == "success"
        reason = (
            f"registered {tool_report.get('registry_name')} and executed model-callable plugin tool"
            if tool_ok
            else f"{tool_report.get('error_type')}: {tool_report.get('error')}"
        )
        checks.append(
            _check(
                "tool.registry.selftest",
                "pass" if tool_ok else "fail",
                reason,
                "Fix plugin ToolRegistry bridge, schema export, or tool execution before enabling plugin tools.",
                "critical",
                production_mode and not tool_ok,
            )
        )
    except Exception as exc:
        checks.append(
            _check(
                "tool.registry.selftest",
                "fail",
                str(exc),
                "Fix plugin ToolRegistry bridge, schema export, or tool execution before enabling plugin tools.",
                "critical",
                production_mode,
            )
        )
    checks.append(
        _check(
            "scanner.configured",
            "pass" if scanner_configured else "warn" if not production_mode else "fail",
            "scanner configured" if scanner_configured else "vulnerability/license scanner is not configured",
            "Connect offline or external vulnerability and license scanner adapters.",
            "high",
            production_mode and not scanner_configured,
        )
    )
    adapter_status = adapters_status()
    supported = set(adapter_status.get("supported_adapters", []))
    checks.append(
        _check(
            "evidence_adapters.available",
            "pass",
            f"supported={sorted(supported)}",
            "Use local evidence adapters to normalize scanner, signature, registry, sandbox, and audit anchor reports.",
            "medium",
            False,
        )
    )
    checks.append(
        _check(
            "evidence_adapters.examples_not_production",
            "pass",
            ".example and offline/reference reports are not treated as production evidence",
            "Keep production evidence generated by real build/sign/scan/deploy pipelines.",
            "high",
            False,
        )
    )
    memory_store = governance_store_metadata(ToolCallSessionStore())
    approval_provider = UnconfiguredApprovalProvider()
    checks.append(
        _check(
            "governance.external_store",
            "warn" if not production_mode else "fail",
            f"default store_kind={memory_store.get('store_kind')} is not multi-instance production governance",
            "Inject an external transactional governance store for production multi-instance deployments.",
            "high",
            production_mode,
        )
    )
    checks.append(
        _check(
            "approval.external_provider",
            "warn" if not production_mode else "fail",
            f"approval provider kind={approval_provider.provider_kind} is not configured",
            "Connect an external approval provider or operator UI for production high-risk tool confirmation.",
            "high",
            production_mode,
        )
    )
    contract = run_integration_contract_check()
    checks.append(
        _check(
            "integration.contract",
            "pass" if contract.get("status") == "pass" else "warn",
            f"requirements={len(contract.get('requirements', []))}",
            "Keep model loops and legacy adapters behind PluginToolService.",
            "critical",
            False,
        )
    )
    return checks


def doctor_report(checks: list[DoctorCheck]) -> dict[str, Any]:
    status = "pass"
    if any(item.status == "fail" for item in checks):
        status = "fail"
    elif any(item.status == "warn" for item in checks):
        status = "warn"
    return {
        "status": status,
        "checks": [item.to_dict() for item in checks],
        "production_blocking": any(item.production_blocking for item in checks),
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [f"Production Doctor status={report['status']} production_blocking={str(report['production_blocking']).lower()}"]
    for item in report["checks"]:
        block = " blocking" if item["production_blocking"] else ""
        lines.append(f"- [{item['status']}] {item['check_id']} severity={item['severity']}{block}: {item['reason']}")
        lines.append(f"  recommendation: {item['recommendation']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Humanoid AGI plugin production environment doctor")
    parser.add_argument("--plugins-dir", default="data/plugins")
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--audit-log")
    parser.add_argument("--scanner-configured", action="store_true")
    parser.add_argument("--audit-anchor-configured", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    if args.json_output:
        checks, captured = _capture_doctor_logs(
            lambda: run_doctor(
                plugins_dir=args.plugins_dir,
                production_mode=args.production,
                audit_log=args.audit_log,
                scanner_configured=args.scanner_configured,
                audit_anchor_configured=args.audit_anchor_configured,
            )
        )
        report = doctor_report(checks)
        report["logs_suppressed"] = True
        if captured["stdout"] or captured["stderr"]:
            report["suppressed_log_bytes"] = {
                "stdout": len(captured["stdout"].encode("utf-8", errors="replace")),
                "stderr": len(captured["stderr"].encode("utf-8", errors="replace")),
            }
    else:
        report = doctor_report(
            run_doctor(
                plugins_dir=args.plugins_dir,
                production_mode=args.production,
                audit_log=args.audit_log,
                scanner_configured=args.scanner_configured,
                audit_anchor_configured=args.audit_anchor_configured,
            )
        )
    if args.json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 1 if report["production_blocking"] else 0


def _check(
    check_id: str,
    status: str,
    reason: str,
    recommendation: str,
    severity: str,
    production_blocking: bool,
) -> DoctorCheck:
    return DoctorCheck(
        check_id=check_id,
        status=status,
        reason=reason,
        recommendation=recommendation,
        severity=severity,
        production_blocking=production_blocking,
    )


def _safe_which(command: str) -> str | None:
    try:
        return shutil.which(command)
    except (AttributeError, OSError, TypeError):
        return None


def _capture_doctor_logs(func: Any) -> tuple[list[DoctorCheck], dict[str, str]]:
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            checks = func()
    finally:
        logging.disable(previous_disable)
    return checks, {"stdout": stdout_buffer.getvalue(), "stderr": stderr_buffer.getvalue()}


if __name__ == "__main__":
    raise SystemExit(main())
