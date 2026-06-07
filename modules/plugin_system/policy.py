from __future__ import annotations

import sys
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from .audit import AuditLogger, NullAuditLogger, new_request_id
from .models import PluginMetadata, TrustLevel


PACKAGE_LOCK_FILE = "manifest.lock"


class PolicyError(RuntimeError):
    """Raised when organization policy rejects a plugin action."""


@dataclass(frozen=True)
class PolicyDecision:
    status: str
    reason: str
    action: str
    plugin: str | None = None
    severity: str = "medium"
    production_blocking: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PluginPolicy:
    deny_permissions: set[str] = field(default_factory=set)
    require_admin_approval: set[str] = field(default_factory=lambda: {
        "network.outbound",
        "fs.write",
        "memory.write",
        "output.send",
    })
    third_party_require_signature: bool = True
    third_party_require_sandbox: bool = True
    third_party_allow_in_process: bool = False
    third_party_require_sbom: bool = True
    third_party_require_lockfile: bool = True
    third_party_require_scan_pass: bool = True
    prevent_downgrade: bool = True
    prevent_same_version_replace: bool = True
    allow_prerelease: bool = False
    linux_required_for_third_party_production: bool = True
    windows_third_party_production_allowed: bool = False
    require_audit: bool = True
    require_audit_checkpoint: bool = True
    deny_ip_literal: bool = True
    deny_private_ip: bool = True
    deny_metadata_service: bool = True
    max_response_bytes: int = 1024 * 1024
    timeout_ms: int = 5000


class PolicyEngine:
    def __init__(
        self,
        policy: PluginPolicy | None = None,
        *,
        audit_logger: AuditLogger | NullAuditLogger | None = None,
    ) -> None:
        self.policy = policy or PluginPolicy()
        self.audit_logger = audit_logger or NullAuditLogger()

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        audit_logger: AuditLogger | NullAuditLogger | None = None,
    ) -> "PolicyEngine":
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise PolicyError("policy file must contain a mapping")
        return cls(_policy_from_mapping(payload), audit_logger=audit_logger)

    def evaluate_install(
        self,
        source: str | Path,
        *,
        production_mode: bool,
        signature: dict[str, Any] | None = None,
        scan_report: dict[str, Any] | None = None,
    ) -> list[PolicyDecision]:
        source_path = Path(source).resolve()
        metadata = _metadata_from_source(source_path)
        decisions: list[PolicyDecision] = []
        decisions.extend(self._base_metadata_decisions(metadata, production_mode, include_platform=False))
        if production_mode and metadata.runtime.trust == TrustLevel.THIRD_PARTY:
            if self.policy.third_party_require_signature and not signature:
                decisions.append(_fail(metadata, "install", "third-party production install requires signature"))
            if self.policy.third_party_require_lockfile and not _source_has_file(source_path, PACKAGE_LOCK_FILE):
                decisions.append(_fail(metadata, "install", f"third-party production install requires {PACKAGE_LOCK_FILE}"))
            if self.policy.third_party_require_sbom and not _source_has_file(source_path, "sbom.cdx.json"):
                decisions.append(_fail(metadata, "install", "third-party production install requires SBOM"))
            if self.policy.third_party_require_scan_pass:
                scan_decision = validate_scan_report(scan_report)
                if not scan_report:
                    decisions.append(_fail(metadata, "install", "third-party production install requires passing scan report"))
                elif scan_decision.status != "pass":
                    decisions.append(_fail(metadata, "install", scan_decision.reason))
        return self._audit_decisions(decisions, metadata, "install")

    def evaluate_enable(
        self,
        metadata: PluginMetadata,
        granted_permissions: set[str],
        *,
        admin_approved: bool,
        production_mode: bool = False,
    ) -> list[PolicyDecision]:
        decisions = self._base_metadata_decisions(metadata, production_mode, include_platform=False)
        denied = sorted(metadata.requested_permissions & self.policy.deny_permissions)
        if denied:
            decisions.append(_fail(metadata, "enable", f"permissions denied by organization policy: {denied}"))
        risky = sorted((metadata.requested_permissions | granted_permissions) & self.policy.require_admin_approval)
        if risky and not admin_approved:
            decisions.append(_fail(metadata, "enable", f"admin approval required for permissions: {risky}"))
        return self._audit_decisions(decisions, metadata, "enable")

    def evaluate_start(
        self,
        metadata: PluginMetadata,
        *,
        production_mode: bool,
        sandbox_enforced: bool,
        audit_checkpoint_configured: bool = False,
    ) -> list[PolicyDecision]:
        decisions = self._base_metadata_decisions(metadata, production_mode, include_platform=True)
        if production_mode and metadata.runtime.trust == TrustLevel.THIRD_PARTY:
            if self.policy.third_party_require_sandbox and not sandbox_enforced:
                decisions.append(_fail(metadata, "start", "third-party production start requires enforced sandbox"))
            if self.policy.require_audit_checkpoint and not audit_checkpoint_configured:
                decisions.append(
                    PolicyDecision(
                        status="warn",
                        reason="audit checkpoint anchor is not configured",
                        action="start",
                        plugin=metadata.name,
                        severity="high",
                        production_blocking=False,
                    )
                )
        return self._audit_decisions(decisions, metadata, "start")

    def enforce(self, decisions: list[PolicyDecision]) -> None:
        failures = [item for item in decisions if item.status == "fail"]
        if failures:
            raise PolicyError("; ".join(item.reason for item in failures))

    def check_source(
        self,
        source: str | Path,
        *,
        production_mode: bool,
        signature: dict[str, Any] | None = None,
        scan_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = _metadata_from_source(Path(source).resolve())
        decisions = self.evaluate_install(
            source,
            production_mode=production_mode,
            signature=signature,
            scan_report=scan_report,
        )
        decisions.extend(
            self.evaluate_enable(
                metadata,
                metadata.requested_permissions,
                admin_approved=False,
                production_mode=production_mode,
            )
        )
        status = "fail" if any(item.status == "fail" for item in decisions) else (
            "warn" if any(item.status == "warn" for item in decisions) else "pass"
        )
        return {
            "status": status,
            "generated_at": datetime.now(UTC).isoformat(),
            "decisions": [item.to_dict() for item in decisions],
        }

    def _base_metadata_decisions(
        self,
        metadata: PluginMetadata,
        production_mode: bool,
        *,
        include_platform: bool,
    ) -> list[PolicyDecision]:
        decisions: list[PolicyDecision] = []
        if not self.policy.allow_prerelease and "-" in metadata.version:
            decisions.append(_fail(metadata, "metadata", "pre-release versions are not allowed by policy"))
        if production_mode and metadata.runtime.trust == TrustLevel.THIRD_PARTY:
            if not self.policy.third_party_allow_in_process and metadata.effective_run_mode.value == "in_process":
                decisions.append(_fail(metadata, "metadata", "third-party production plugins cannot run in-process"))
            if include_platform and self.policy.linux_required_for_third_party_production and sys.platform != "linux":
                if sys.platform == "win32" and not self.policy.windows_third_party_production_allowed:
                    decisions.append(_fail(metadata, "metadata", "Windows third-party production is not allowed by policy"))
                elif sys.platform != "win32":
                    decisions.append(_fail(metadata, "metadata", "Linux is required for third-party production"))
        return decisions

    def _audit_decisions(
        self,
        decisions: list[PolicyDecision],
        metadata: PluginMetadata,
        action: str,
    ) -> list[PolicyDecision]:
        if not decisions:
            decisions = [
                PolicyDecision(
                    status="pass",
                    reason="policy passed",
                    action=action,
                    plugin=metadata.name,
                    severity="info",
                    production_blocking=False,
                )
            ]
        for decision in decisions:
            self.audit_logger.record(
                "plugin.policy_decision",
                "success" if decision.status == "pass" else "error" if decision.status == "fail" else "skipped",
                request_id=new_request_id(),
                plugin=metadata.name,
                action=decision.action,
                details=decision.to_dict() | {"version": metadata.version},
            )
        return decisions


def _policy_from_mapping(payload: dict[str, Any]) -> PluginPolicy:
    third_party = payload.get("third_party") or {}
    version = payload.get("version") or {}
    platform = payload.get("platform") or {}
    audit = payload.get("audit") or {}
    network = payload.get("network") or {}
    if not isinstance(third_party, dict) or not isinstance(version, dict):
        raise PolicyError("policy sections must be mappings")
    return PluginPolicy(
        deny_permissions=set(payload.get("deny_permissions") or []),
        require_admin_approval=set(payload.get("require_admin_approval") or []),
        third_party_require_signature=bool(third_party.get("require_signature", True)),
        third_party_require_sandbox=bool(third_party.get("require_sandbox", True)),
        third_party_allow_in_process=bool(third_party.get("allow_in_process", False)),
        third_party_require_sbom=bool(third_party.get("require_sbom", True)),
        third_party_require_lockfile=bool(third_party.get("require_lockfile", True)),
        third_party_require_scan_pass=bool(third_party.get("require_scan_pass", True)),
        prevent_downgrade=bool(version.get("prevent_downgrade", True)),
        prevent_same_version_replace=bool(version.get("prevent_same_version_replace", True)),
        allow_prerelease=bool(version.get("allow_prerelease", False)),
        linux_required_for_third_party_production=bool(platform.get("linux_required_for_third_party_production", True)),
        windows_third_party_production_allowed=bool(platform.get("windows_third_party_production_allowed", False)),
        require_audit=bool(audit.get("require_audit", True)),
        require_audit_checkpoint=bool(audit.get("require_audit_checkpoint", True)),
        deny_ip_literal=bool(network.get("deny_ip_literal", True)),
        deny_private_ip=bool(network.get("deny_private_ip", True)),
        deny_metadata_service=bool(network.get("deny_metadata_service", True)),
        max_response_bytes=int(network.get("max_response_bytes", 1024 * 1024)),
        timeout_ms=int(network.get("timeout_ms", 5000)),
    )


def _metadata_from_source(source: Path) -> PluginMetadata:
    if source.is_dir():
        raw = yaml.safe_load((source / "plugin.yaml").read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise PolicyError("plugin.yaml must contain a mapping")
        return PluginMetadata(**raw)
    if source.is_file() and source.suffix == ".zip":
        with zipfile.ZipFile(source, "r") as archive:
            raw = yaml.safe_load(archive.read("plugin.yaml").decode("utf-8"))
        if not isinstance(raw, dict):
            raise PolicyError("plugin.yaml must contain a mapping")
        return PluginMetadata(**raw)
    raise PolicyError(f"unsupported policy check source: {source}")


def _source_has_file(source: Path, relative_path: str) -> bool:
    if source.is_dir():
        return (source / relative_path).exists()
    if source.is_file() and source.suffix == ".zip":
        with zipfile.ZipFile(source, "r") as archive:
            return relative_path in archive.namelist()
    return False


def _fail(metadata: PluginMetadata, action: str, reason: str) -> PolicyDecision:
    return PolicyDecision(
        status="fail",
        reason=reason,
        action=action,
        plugin=metadata.name,
        severity="high",
        production_blocking=True,
    )


def validate_scan_report(
    scan_report: dict[str, Any] | None,
    *,
    max_age_hours: int = 24,
) -> PolicyDecision:
    if not scan_report:
        return PolicyDecision(
            status="fail",
            reason="scan report is missing",
            action="scan",
            severity="high",
            production_blocking=True,
        )
    scanner_name = str(scan_report.get("scanner_name", "")).strip()
    scanner_version = str(scan_report.get("scanner_version", "")).strip()
    generated_at = str(scan_report.get("generated_at", "")).strip()
    policy_decision = str(scan_report.get("policy_decision", "")).strip()
    if not scanner_name or scanner_name == "unknown" or not scanner_version:
        return PolicyDecision(
            status="fail",
            reason="scan report has unknown scanner identity",
            action="scan",
            severity="high",
            production_blocking=True,
        )
    if policy_decision != "pass":
        return PolicyDecision(
            status="fail",
            reason=f"scan policy did not pass: {scan_report.get('reason')}",
            action="scan",
            severity="high",
            production_blocking=True,
        )
    try:
        timestamp = datetime.fromisoformat(generated_at)
    except ValueError:
        return PolicyDecision(
            status="fail",
            reason="scan report generated_at is invalid",
            action="scan",
            severity="high",
            production_blocking=True,
        )
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    if datetime.now(UTC) - timestamp.astimezone(UTC) > timedelta(hours=max_age_hours):
        return PolicyDecision(
            status="fail",
            reason="scan report is expired",
            action="scan",
            severity="high",
            production_blocking=True,
        )
    return PolicyDecision(
        status="pass",
        reason="scan report passed",
        action="scan",
        severity="info",
        production_blocking=False,
    )
