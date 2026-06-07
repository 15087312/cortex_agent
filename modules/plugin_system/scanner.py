from __future__ import annotations

import json
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


class PluginScanError(RuntimeError):
    """Raised when a plugin security or license scan fails policy."""


@dataclass(frozen=True)
class Finding:
    type: str
    package: str
    version: str
    id: str
    severity: str
    description: str
    recommendation: str
    source: str


@dataclass(frozen=True)
class ScanPolicy:
    fail_on_critical: bool = True
    high_vulnerability: str = "fail"
    denied_licenses: set[str] = field(default_factory=lambda: {"GPL-3.0", "AGPL-3.0"})
    allowed_licenses: set[str] = field(default_factory=set)
    unknown_license: str = "warn"
    native_extension: str = "fail"
    scanner_required: bool = False


@dataclass(frozen=True)
class ScanReport:
    scanner_name: str
    scanner_version: str
    generated_at: str
    findings: list[Finding]
    severity_summary: dict[str, int]
    policy_decision: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["findings"] = [asdict(item) for item in self.findings]
        return payload


class ScannerAdapter(Protocol):
    scanner_name: str
    scanner_version: str

    def scan_sbom(self, sbom: dict[str, Any]) -> ScanReport:
        ...

    def scan_package(self, path: str | Path) -> ScanReport:
        ...

    def scan_dependencies(self, lockfile: str | Path) -> ScanReport:
        ...


class OfflineVulnerabilityScanner:
    scanner_name = "offline-vulnerability-fixture"
    scanner_version = "1"

    def __init__(self, fixture: str | Path | dict[str, Any] | None = None, policy: ScanPolicy | None = None):
        self.fixture = _load_fixture(fixture)
        self.policy = policy or ScanPolicy()

    def scan_sbom(self, sbom: dict[str, Any]) -> ScanReport:
        components = _components_from_sbom(sbom)
        findings = self._findings_for_components(components)
        return _report(self.scanner_name, self.scanner_version, findings, self.policy)

    def scan_package(self, path: str | Path) -> ScanReport:
        return _report(self.scanner_name, self.scanner_version, _native_findings(path, self.policy), self.policy)

    def scan_dependencies(self, lockfile: str | Path) -> ScanReport:
        components = _components_from_lockfile(lockfile)
        findings = self._findings_for_components(components)
        return _report(self.scanner_name, self.scanner_version, findings, self.policy)

    def _findings_for_components(self, components: list[dict[str, str]]) -> list[Finding]:
        fixture_findings = _vulnerability_fixture_findings(self.fixture)
        findings: list[Finding] = []
        for component in components:
            key = _component_key(component["name"], component["version"])
            for raw in fixture_findings.get(key, []):
                findings.append(
                    Finding(
                        type="vulnerability",
                        package=component["name"],
                        version=component["version"],
                        id=str(raw.get("id", "VULN-UNKNOWN")),
                        severity=str(raw.get("severity", "unknown")).lower(),
                        description=str(raw.get("description", "offline fixture finding")),
                        recommendation=str(raw.get("recommendation", "upgrade or remove the dependency")),
                        source=str(raw.get("source", self.scanner_name)),
                    )
                )
        return findings


class OfflineLicenseScanner:
    scanner_name = "offline-license-fixture"
    scanner_version = "1"

    def __init__(self, fixture: str | Path | dict[str, Any] | None = None, policy: ScanPolicy | None = None):
        self.fixture = _load_fixture(fixture)
        self.policy = policy or ScanPolicy()

    def scan_sbom(self, sbom: dict[str, Any]) -> ScanReport:
        components = _components_from_sbom(sbom)
        findings = self._license_findings(components)
        return _report(self.scanner_name, self.scanner_version, findings, self.policy)

    def scan_package(self, path: str | Path) -> ScanReport:
        return _report(self.scanner_name, self.scanner_version, _native_findings(path, self.policy), self.policy)

    def scan_dependencies(self, lockfile: str | Path) -> ScanReport:
        components = _components_from_lockfile(lockfile)
        findings = self._license_findings(components)
        return _report(self.scanner_name, self.scanner_version, findings, self.policy)

    def _license_findings(self, components: list[dict[str, str]]) -> list[Finding]:
        license_map = _license_fixture_findings(self.fixture)
        findings: list[Finding] = []
        for component in components:
            key = _component_key(component["name"], component["version"])
            license_id = str(license_map.get(key) or component.get("license") or "UNKNOWN")
            if license_id in self.policy.denied_licenses:
                findings.append(
                    Finding(
                        type="license",
                        package=component["name"],
                        version=component["version"],
                        id=license_id,
                        severity="critical",
                        description=f"license is denied by policy: {license_id}",
                        recommendation="replace dependency or request legal approval",
                        source=self.scanner_name,
                    )
                )
            elif license_id == "UNKNOWN" and self.policy.unknown_license in {"warn", "fail"}:
                severity = "high" if self.policy.unknown_license == "fail" else "medium"
                findings.append(
                    Finding(
                        type="license",
                        package=component["name"],
                        version=component["version"],
                        id="UNKNOWN",
                        severity=severity,
                        description="dependency license is unknown",
                        recommendation="provide license metadata or legal review",
                        source=self.scanner_name,
                    )
                )
            elif self.policy.allowed_licenses and license_id not in self.policy.allowed_licenses:
                findings.append(
                    Finding(
                        type="license",
                        package=component["name"],
                        version=component["version"],
                        id=license_id,
                        severity="high",
                        description=f"license is not in allowlist: {license_id}",
                        recommendation="update allowlist or replace dependency",
                        source=self.scanner_name,
                    )
                )
        return findings


def scan_sbom_file(sbom_path: str | Path, scanner: ScannerAdapter) -> ScanReport:
    payload = _read_json_object(sbom_path)
    return scanner.scan_sbom(payload)


def scan_package_file(package_path: str | Path, scanner: ScannerAdapter) -> ScanReport:
    return scanner.scan_package(package_path)


def scan_lockfile(lockfile_path: str | Path, scanner: ScannerAdapter) -> ScanReport:
    return scanner.scan_dependencies(lockfile_path)


def enforce_scan_report(report: ScanReport) -> None:
    if report.policy_decision == "fail":
        raise PluginScanError(report.reason)


def scanner_missing_report(*, production_mode: bool, policy: ScanPolicy | None = None) -> ScanReport:
    effective_policy = policy or ScanPolicy(scanner_required=production_mode)
    decision = "fail" if production_mode and effective_policy.scanner_required else "warn"
    return ScanReport(
        scanner_name="scanner-configuration",
        scanner_version="1",
        generated_at=datetime.now(UTC).isoformat(),
        findings=[],
        severity_summary={},
        policy_decision=decision,
        reason="scanner is not configured",
    )


def _report(scanner_name: str, scanner_version: str, findings: list[Finding], policy: ScanPolicy) -> ScanReport:
    summary: dict[str, int] = {}
    for finding in findings:
        summary[finding.severity] = summary.get(finding.severity, 0) + 1
    decision = "pass"
    reasons: list[str] = []
    if any(item.severity == "critical" for item in findings) and policy.fail_on_critical:
        decision = "fail"
        reasons.append("critical finding present")
    if any(item.severity == "high" and item.type == "vulnerability" for item in findings):
        if policy.high_vulnerability == "fail":
            decision = "fail"
            reasons.append("high vulnerability present")
        elif decision == "pass":
            decision = "warn"
            reasons.append("high vulnerability present")
    if any(item.type == "license" and item.severity in {"critical", "high"} for item in findings):
        if policy.unknown_license == "fail" or any(item.id in policy.denied_licenses for item in findings):
            decision = "fail"
            reasons.append("license policy violation")
        elif decision == "pass":
            decision = "warn"
            reasons.append("license review required")
    elif any(item.type == "license" for item in findings) and decision == "pass":
        decision = "warn"
        reasons.append("license review required")
    if any(item.type == "policy" and item.id == "NATIVE_EXTENSION" for item in findings):
        if policy.native_extension == "fail":
            decision = "fail"
            reasons.append("native extension is not allowed")
        elif decision == "pass":
            decision = "warn"
            reasons.append("native extension requires review")
    return ScanReport(
        scanner_name=scanner_name,
        scanner_version=scanner_version,
        generated_at=datetime.now(UTC).isoformat(),
        findings=findings,
        severity_summary=summary,
        policy_decision=decision,
        reason="; ".join(dict.fromkeys(reasons)) or "policy passed",
    )


def _load_fixture(fixture: str | Path | dict[str, Any] | None) -> dict[str, Any]:
    if fixture is None:
        return {}
    if isinstance(fixture, dict):
        return fixture
    return _read_json_object(fixture)


def _read_json_object(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PluginScanError(f"JSON file must contain an object: {path}")
    return payload


def _vulnerability_fixture_findings(fixture: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    raw = _fixture_findings(fixture, "vulnerabilities")
    return {name: value for name, value in raw.items() if isinstance(value, list)}


def _license_fixture_findings(fixture: dict[str, Any]) -> dict[str, str]:
    raw = _fixture_findings(fixture, "licenses")
    return {name: value for name, value in raw.items() if isinstance(value, str)}


def _fixture_findings(fixture: dict[str, Any], key: str) -> dict[str, list[dict[str, Any]] | str]:
    raw = fixture.get(key, {})
    if not isinstance(raw, dict):
        return {}
    return raw


def _components_from_sbom(sbom: dict[str, Any]) -> list[dict[str, str]]:
    components: list[dict[str, str]] = []
    for raw in sbom.get("components", []):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", "")).strip()
        version = str(raw.get("version", "")).strip()
        if not name:
            continue
        license_id = "UNKNOWN"
        licenses = raw.get("licenses")
        if isinstance(licenses, list) and licenses:
            first = licenses[0]
            if isinstance(first, dict):
                license_payload = first.get("license")
                if isinstance(license_payload, dict):
                    license_id = str(license_payload.get("id") or license_payload.get("name") or "UNKNOWN")
        components.append({"name": name, "version": version, "license": license_id})
    return components


def _components_from_lockfile(lockfile: str | Path) -> list[dict[str, str]]:
    components: list[dict[str, str]] = []
    for line in Path(lockfile).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "==" not in stripped:
            continue
        requirement = stripped.split("--hash=", 1)[0].strip()
        name, version = requirement.split("==", 1)
        components.append({"name": name.strip(), "version": version.strip(), "license": "UNKNOWN"})
    return components


def _component_key(name: str, version: str) -> str:
    return f"{name.lower().replace('_', '-')}=={version}"


def _native_findings(path: str | Path, policy: ScanPolicy) -> list[Finding]:
    target = Path(path)
    native_extensions: list[str] = []
    if target.is_file() and target.suffix == ".zip":
        with zipfile.ZipFile(target, "r") as archive:
            native_extensions = [name for name in archive.namelist() if name.endswith((".so", ".pyd", ".dll", ".dylib"))]
    elif target.is_file() and target.suffix == ".whl":
        native_extensions = [] if target.name.endswith("py3-none-any.whl") else [target.name]
    elif target.is_dir():
        native_extensions = [
            str(item.relative_to(target))
            for item in target.rglob("*")
            if item.suffix in {".so", ".pyd", ".dll", ".dylib"}
        ]
    if not native_extensions:
        return []
    severity = "high" if policy.native_extension == "require_review" else "critical"
    return [
        Finding(
            type="policy",
            package=target.name,
            version="unknown",
            id="NATIVE_EXTENSION",
            severity=severity,
            description="native extension artifact detected",
            recommendation="reject or require high-trust approval",
            source="package-inspection",
        )
    ]
