from __future__ import annotations

import json
import os
import hashlib
import re
import shutil
import subprocess
import sys
import venv
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, Any

from .models import PluginMetadata, TrustLevel


DEPENDENCY_MANIFEST = ".plugin-deps.json"
DEPENDENCY_LOCK_FILE = "requirements.lock"
DEPENDENCY_WHEELHOUSE_DIR = "wheels"
EXACT_REQUIREMENT_PATTERN = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)\s*==\s*(?P<version>[A-Za-z0-9!+_.-]+)\s*$"
)
PURE_PYTHON_WHEEL_TAGS = {"py3-none-any", "py2.py3-none-any"}


class PluginDependencyError(RuntimeError):
    """Raised when a plugin dependency environment cannot be prepared."""


@dataclass
class DependencyEnvironment:
    backend: str
    path: str | None
    python: str
    installed: bool
    packages: list[str]
    created_at: str
    skipped_reason: str | None = None
    lockfile: str | None = None
    hash_required: bool = False
    native_extensions_allowed: bool = False
    scan_reports: list[dict[str, Any]] = field(default_factory=list)


class DependencySecurityScanner(Protocol):
    """Adapter interface for offline or external dependency security scanners."""

    def scan(
        self,
        *,
        plugin_dir: Path,
        metadata: PluginMetadata,
        locked_requirements: list[str],
        wheelhouse: Path | None,
    ) -> dict[str, Any]:
        ...


@dataclass
class DependencyScanPolicy:
    vulnerability_scanner: DependencySecurityScanner | None = None
    license_scanner: DependencySecurityScanner | None = None
    allow_native_extensions: bool = False


def lock_requirements(
    plugin_dir: str | Path,
    metadata: PluginMetadata,
    wheelhouse: str | Path,
    output: str | Path | None = None,
    vendor: bool = False,
) -> Path:
    """Generate a hash-pinned requirements.lock from local wheel artifacts."""

    packages = list(metadata.requires.packages)
    if not packages:
        raise PluginDependencyError("plugin does not declare Python packages")

    plugin_path = Path(plugin_dir).resolve()
    wheelhouse_path = Path(wheelhouse).resolve()
    if not wheelhouse_path.is_dir():
        raise PluginDependencyError(f"wheelhouse directory does not exist: {wheelhouse_path}")

    output_path = Path(output) if output is not None else plugin_path / DEPENDENCY_LOCK_FILE
    if not output_path.is_absolute():
        output_path = plugin_path / output_path
    output_path = output_path.resolve()
    if not _is_relative_to(output_path, plugin_path):
        raise PluginDependencyError("lockfile output must stay inside the plugin directory")

    locked_lines: list[str] = []
    artifacts: list[Path] = []
    seen_names: set[str] = set()
    for requirement in packages:
        name, version = _parse_exact_requirement(requirement)
        normalized_name = _normalize_package_name(name)
        if normalized_name in seen_names:
            raise PluginDependencyError(f"duplicate package requirement: {name}")
        seen_names.add(normalized_name)
        artifact = _find_wheel_artifact(wheelhouse_path, normalized_name, version)
        artifacts.append(artifact)
        digest = _sha256_path(artifact)
        locked_lines.append(f"{normalized_name}=={version} --hash=sha256:{digest}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(sorted(locked_lines)) + "\n", encoding="utf-8")
    if vendor:
        _vendor_wheels(plugin_path, artifacts)
    return output_path


class DependencyManager:
    """Prepare an isolated Python environment for plugins that declare dependencies."""

    def __init__(
        self,
        plugins_dir: str | Path = "data/plugins",
        scan_policy: DependencyScanPolicy | None = None,
    ):
        self.plugins_dir = Path(plugins_dir).resolve()
        self.scan_policy = scan_policy or DependencyScanPolicy()

    def prepare(
        self,
        plugin_dir: str | Path,
        metadata: PluginMetadata,
        *,
        install: bool = False,
    ) -> DependencyEnvironment | None:
        packages = list(metadata.requires.packages)
        if not packages:
            return None
        validate_dependency_policy(
            plugin_dir,
            metadata,
            production_mode=False,
            scan_policy=self.scan_policy,
        )
        if metadata.runtime.trust in {TrustLevel.OFFICIAL, TrustLevel.TRUSTED}:
            environment = self._system_environment(packages, "trusted plugin uses host interpreter")
            self._write_manifest(plugin_dir, environment)
            return environment

        plugin_path = Path(plugin_dir).resolve()
        lockfile = plugin_path / DEPENDENCY_LOCK_FILE
        wheelhouse = plugin_path / DEPENDENCY_WHEELHOUSE_DIR
        env_dir = plugin_path / ".venv"
        python_path = self._venv_python(env_dir)
        locked_requirements = self._read_locked_requirements(lockfile) if lockfile.exists() else []
        scan_reports = run_dependency_scans(
            plugin_path,
            metadata,
            locked_requirements,
            wheelhouse if wheelhouse.exists() else None,
            self.scan_policy,
        )
        environment = DependencyEnvironment(
            backend="venv",
            path=str(env_dir),
            python=str(python_path),
            installed=False,
            packages=locked_requirements or packages,
            created_at=datetime.now(UTC).isoformat(),
            lockfile=DEPENDENCY_LOCK_FILE if lockfile.exists() else None,
            hash_required=bool(lockfile.exists()),
            native_extensions_allowed=self.scan_policy.allow_native_extensions,
            scan_reports=scan_reports,
        )
        if install:
            if not lockfile.exists():
                raise PluginDependencyError(
                    f"third-party plugin dependency installation requires {DEPENDENCY_LOCK_FILE}"
                )
            self._validate_locked_requirements(packages, locked_requirements)
            if not wheelhouse.is_dir():
                raise PluginDependencyError(
                    f"third-party plugin dependency installation requires vendored {DEPENDENCY_WHEELHOUSE_DIR}/"
                )
            self._validate_wheelhouse(wheelhouse, locked_requirements)
            self._create_venv(env_dir)
            self._install_locked_requirements(python_path, lockfile, wheelhouse)
            environment.installed = True
        else:
            environment.skipped_reason = "dependency installation not requested"
        self._write_manifest(plugin_path, environment)
        return environment

    def read_environment(self, plugin_dir: str | Path) -> DependencyEnvironment | None:
        manifest_path = Path(plugin_dir) / DEPENDENCY_MANIFEST
        if not manifest_path.exists():
            return None
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        return DependencyEnvironment(**payload)

    def write_environment(self, plugin_dir: str | Path, environment: DependencyEnvironment) -> None:
        self._write_manifest(plugin_dir, environment)

    def runtime_python(self, plugin_dir: str | Path, metadata: PluginMetadata) -> str:
        environment = self.read_environment(plugin_dir)
        if not environment:
            return sys.executable
        if environment.backend == "venv" and environment.installed:
            python_path = Path(environment.python)
            if python_path.exists():
                return str(python_path)
        return sys.executable

    def _system_environment(self, packages: list[str], reason: str) -> DependencyEnvironment:
        return DependencyEnvironment(
            backend="system",
            path=None,
            python=sys.executable,
            installed=False,
            packages=packages,
            created_at=datetime.now(UTC).isoformat(),
            skipped_reason=reason,
        )

    def _create_venv(self, env_dir: Path) -> None:
        builder = venv.EnvBuilder(with_pip=True, clear=True, symlinks=(os.name != "nt"))
        builder.create(env_dir)

    def _install_locked_requirements(self, python_path: Path, lockfile: Path, wheelhouse: Path) -> None:
        command = [
            str(python_path),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            "--require-hashes",
            "--only-binary",
            ":all:",
            "--no-build-isolation",
            "-r",
            str(lockfile),
        ]
        try:
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except subprocess.CalledProcessError as exc:
            raise PluginDependencyError(exc.stderr.strip() or exc.stdout.strip() or str(exc)) from exc

    def _read_locked_requirements(self, lockfile: Path) -> list[str]:
        requirements: list[str] = []
        for line_no, line in enumerate(lockfile.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith(("-", "--")):
                raise PluginDependencyError(f"{DEPENDENCY_LOCK_FILE}:{line_no} must not contain pip options")
            if "--hash=sha256:" not in stripped:
                raise PluginDependencyError(f"{DEPENDENCY_LOCK_FILE}:{line_no} is missing --hash=sha256")
            if any(token in stripped for token in [";", "&", "|", "`", "$", "\n", "\r"]):
                raise PluginDependencyError(f"{DEPENDENCY_LOCK_FILE}:{line_no} contains unsafe characters")
            _parse_exact_requirement(stripped.split("--hash=", 1)[0].strip())
            requirements.append(stripped)
        if not requirements:
            raise PluginDependencyError(f"{DEPENDENCY_LOCK_FILE} does not contain locked requirements")
        return requirements

    def _validate_locked_requirements(self, declared_packages: list[str], locked_requirements: list[str]) -> None:
        declared_versions = {
            _normalize_package_name(name): version
            for name, version in (_parse_exact_requirement(item) for item in declared_packages)
        }
        locked_versions = {
            _normalize_package_name(name): version
            for name, version in (
                _parse_exact_requirement(item.split("--hash=", 1)[0].strip()) for item in locked_requirements
            )
        }
        missing = sorted(set(declared_versions) - set(locked_versions))
        if missing:
            raise PluginDependencyError(f"{DEPENDENCY_LOCK_FILE} is missing declared packages: {missing}")
        mismatched = sorted(
            name
            for name, version in declared_versions.items()
            if locked_versions.get(name) != version
        )
        if mismatched:
            raise PluginDependencyError(
                f"{DEPENDENCY_LOCK_FILE} version mismatch for declared packages: {mismatched}"
            )

    def _validate_wheelhouse(self, wheelhouse: Path, locked_requirements: list[str]) -> None:
        expected: dict[str, tuple[str, set[str]]] = {}
        for requirement in locked_requirements:
            requirement_part, hash_part = requirement.split("--hash=sha256:", 1)
            name, version = _parse_exact_requirement(requirement_part.strip())
            digest = hash_part.strip().split()[0]
            expected[_normalize_package_name(name)] = (version, {digest})
        for name, (version, digests) in expected.items():
            artifact = _find_wheel_artifact(
                wheelhouse,
                name,
                version,
                allow_native_extensions=self.scan_policy.allow_native_extensions,
            )
            actual_digest = _sha256_path(artifact)
            if actual_digest not in digests:
                raise PluginDependencyError(
                    f"{DEPENDENCY_WHEELHOUSE_DIR}/ wheel hash does not match {DEPENDENCY_LOCK_FILE}: {artifact.name}"
                )

    def _requirement_name(self, requirement: str) -> str:
        name = requirement.strip()
        for separator in ["==", ">=", "<=", "~=", "!=", ">", "<"]:
            if separator in name:
                name = name.split(separator, 1)[0]
                break
        extras = name.find("[")
        if extras != -1:
            name = name[:extras]
        return name.strip().lower().replace("_", "-")

    def _venv_python(self, env_dir: Path) -> Path:
        if os.name == "nt":
            return env_dir / "Scripts" / "python.exe"
        return env_dir / "bin" / "python"

    def _write_manifest(self, plugin_dir: str | Path, environment: DependencyEnvironment) -> None:
        manifest_path = Path(plugin_dir) / DEPENDENCY_MANIFEST
        manifest_path.write_text(json.dumps(asdict(environment), indent=2, sort_keys=True), encoding="utf-8")


def _parse_exact_requirement(requirement: str) -> tuple[str, str]:
    match = EXACT_REQUIREMENT_PATTERN.match(requirement)
    if not match:
        raise PluginDependencyError(
            "lock generation requires exact package pins like 'package==1.2.3'"
        )
    return match.group("name"), match.group("version")


def _normalize_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _normalize_wheel_component(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _find_wheel_artifact(
    wheelhouse: Path,
    normalized_name: str,
    version: str,
    *,
    allow_native_extensions: bool = False,
) -> Path:
    matches: list[Path] = []
    for artifact in sorted(wheelhouse.glob("*.whl")):
        parsed = _parse_wheel_filename(artifact.name)
        if not parsed:
            continue
        if not allow_native_extensions and not _is_pure_python_wheel(artifact.name):
            raise PluginDependencyError(
                f"native extension wheel is not allowed by default: {artifact.name}"
            )
        artifact_name, artifact_version = parsed
        if artifact_name == normalized_name and artifact_version == _normalize_wheel_component(version):
            matches.append(artifact)
    if not matches:
        raise PluginDependencyError(
            f"wheelhouse is missing wheel for {normalized_name}=={version}"
        )
    if len(matches) > 1:
        raise PluginDependencyError(
            f"wheelhouse contains multiple wheels for {normalized_name}=={version}"
        )
    return matches[0]


def _parse_wheel_filename(filename: str) -> tuple[str, str] | None:
    if not filename.endswith(".whl"):
        return None
    parts = filename[:-4].split("-")
    if len(parts) < 5:
        return None
    return _normalize_wheel_component(parts[0]), _normalize_wheel_component(parts[1])


def _is_pure_python_wheel(filename: str) -> bool:
    if not filename.endswith(".whl"):
        return False
    parts = filename[:-4].split("-")
    if len(parts) < 5:
        return False
    tag = "-".join(parts[-3:])
    return tag in PURE_PYTHON_WHEEL_TAGS


def validate_dependency_policy(
    plugin_dir: str | Path,
    metadata: PluginMetadata,
    *,
    production_mode: bool,
    scan_policy: DependencyScanPolicy | None = None,
) -> None:
    packages = list(metadata.requires.packages)
    if not packages:
        return
    plugin_path = Path(plugin_dir).resolve()
    if (plugin_path / "setup.py").exists() or (plugin_path / "pyproject.toml").exists():
        raise PluginDependencyError("plugin dependency installation must not run package build hooks")
    lockfile = plugin_path / DEPENDENCY_LOCK_FILE
    if metadata.runtime.trust == TrustLevel.THIRD_PARTY and production_mode and not lockfile.exists():
        raise PluginDependencyError(f"production mode requires {DEPENDENCY_LOCK_FILE} for third-party dependencies")
    if not lockfile.exists():
        return
    manager = DependencyManager(plugin_path.parent, scan_policy=scan_policy)
    locked = manager._read_locked_requirements(lockfile)
    manager._validate_locked_requirements(packages, locked)
    wheelhouse = plugin_path / DEPENDENCY_WHEELHOUSE_DIR
    if wheelhouse.exists():
        manager._validate_wheelhouse(wheelhouse, locked)
    run_dependency_scans(
        plugin_path,
        metadata,
        locked,
        wheelhouse if wheelhouse.exists() else None,
        scan_policy or DependencyScanPolicy(),
    )


def run_dependency_scans(
    plugin_dir: Path,
    metadata: PluginMetadata,
    locked_requirements: list[str],
    wheelhouse: Path | None,
    scan_policy: DependencyScanPolicy,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for scanner_type, scanner in [
        ("vulnerability", scan_policy.vulnerability_scanner),
        ("license", scan_policy.license_scanner),
    ]:
        if scanner is None:
            continue
        report = scanner.scan(
            plugin_dir=plugin_dir,
            metadata=metadata,
            locked_requirements=locked_requirements,
            wheelhouse=wheelhouse,
        )
        if not isinstance(report, dict):
            raise PluginDependencyError(f"{scanner_type} scanner must return a report object")
        if report.get("status") == "failed":
            raise PluginDependencyError(f"{scanner_type} scanner failed policy: {report.get('reason', 'unspecified')}")
        reports.append({"type": scanner_type, **report})
    return reports


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _vendor_wheels(plugin_path: Path, artifacts: list[Path]) -> None:
    wheelhouse = plugin_path / DEPENDENCY_WHEELHOUSE_DIR
    wheelhouse.mkdir(parents=True, exist_ok=True)
    for artifact in artifacts:
        destination = wheelhouse / artifact.name
        if destination.resolve() == artifact.resolve():
            continue
        shutil.copy2(artifact, destination)


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False
