from __future__ import annotations

import hashlib
import json
import shutil
import stat
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from .audit import AuditLogger, NullAuditLogger, new_request_id
from .config import PluginConfigManager
from .dependency import DependencyManager
from .dependency import validate_dependency_policy
from .models import (
    InstalledPlugin,
    PluginMetadata,
    PluginStatus,
    PluginValidationError,
    RunMode,
    TrustLevel,
    normalize_archive_path,
    permission_risks,
    validate_permission_decls,
)
from .policy import PolicyEngine
from .signing import LEGACY_SIGNATURE_ALGORITHM, SIGNATURE_ALGORITHM, SUPPORTED_SIGNATURE_ALGORITHMS


MAX_PLUGIN_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_UNPACKED_BYTES = 256 * 1024 * 1024
MAX_PLUGIN_FILE_BYTES = 32 * 1024 * 1024
MAX_PLUGIN_FILES = 2048
MAX_COMPRESSION_RATIO = 100
MANIFEST_FILE = ".plugin-install.json"
PACKAGE_LOCK_FILE = "manifest.lock"
PACKAGE_LOCK_VERSION = 1
INTEGRITY_MANIFEST_VERSION = 1
INTEGRITY_EXCLUDED_DIRS = {".venv", "__pycache__", "data"}
INTEGRITY_EXCLUDED_FILES = {
    MANIFEST_FILE,
    ".plugin-config.json",
    ".plugin-deps.json",
}
GOVERNANCE_FILE = ".plugin-governance.json"
GOVERNANCE_VERSION = 1
ALLOWED_STATUS_TRANSITIONS: dict[PluginStatus, set[PluginStatus]] = {
    PluginStatus.DISCOVERED: {PluginStatus.VERIFIED, PluginStatus.UNINSTALLED},
    PluginStatus.VERIFIED: {PluginStatus.INSTALLED, PluginStatus.REVOKED, PluginStatus.UNINSTALLED},
    PluginStatus.INSTALLED: {
        PluginStatus.CONFIGURED,
        PluginStatus.PENDING_APPROVAL,
        PluginStatus.PERMISSION_PENDING,
        PluginStatus.ENABLED,
        PluginStatus.DISABLED,
        PluginStatus.QUARANTINED,
        PluginStatus.REVOKED,
        PluginStatus.UNINSTALLED,
    },
    PluginStatus.CONFIGURED: {
        PluginStatus.PENDING_APPROVAL,
        PluginStatus.PERMISSION_PENDING,
        PluginStatus.ENABLED,
        PluginStatus.DISABLED,
        PluginStatus.QUARANTINED,
        PluginStatus.REVOKED,
        PluginStatus.UNINSTALLED,
    },
    PluginStatus.PENDING_APPROVAL: {
        PluginStatus.PERMISSION_PENDING,
        PluginStatus.ENABLED,
        PluginStatus.DISABLED,
        PluginStatus.QUARANTINED,
        PluginStatus.REVOKED,
        PluginStatus.UNINSTALLED,
    },
    PluginStatus.PERMISSION_PENDING: {
        PluginStatus.PENDING_APPROVAL,
        PluginStatus.ENABLED,
        PluginStatus.DISABLED,
        PluginStatus.QUARANTINED,
        PluginStatus.REVOKED,
        PluginStatus.UNINSTALLED,
    },
    PluginStatus.ENABLED: {
        PluginStatus.RUNNING,
        PluginStatus.SUSPENDED,
        PluginStatus.DISABLED,
        PluginStatus.QUARANTINED,
        PluginStatus.REVOKED,
        PluginStatus.UNINSTALLED,
    },
    PluginStatus.RUNNING: {
        PluginStatus.ENABLED,
        PluginStatus.SUSPENDED,
        PluginStatus.DISABLED,
        PluginStatus.QUARANTINED,
        PluginStatus.REVOKED,
        PluginStatus.UNINSTALLED,
    },
    PluginStatus.SUSPENDED: {
        PluginStatus.ENABLED,
        PluginStatus.DISABLED,
        PluginStatus.QUARANTINED,
        PluginStatus.REVOKED,
        PluginStatus.UNINSTALLED,
    },
    PluginStatus.DISABLED: {
        PluginStatus.ENABLED,
        PluginStatus.QUARANTINED,
        PluginStatus.REVOKED,
        PluginStatus.UNINSTALLED,
    },
    PluginStatus.QUARANTINED: {PluginStatus.REVOKED, PluginStatus.UNINSTALLED},
    PluginStatus.REVOKED: {PluginStatus.UNINSTALLED},
    PluginStatus.UNINSTALLED: set(),
}


class PluginPackageError(PluginValidationError):
    """Raised when a plugin package cannot be safely loaded or installed."""


class PluginLoader:
    """Discover, validate, and install Humanoid AGI plugin packages."""

    def __init__(
        self,
        plugins_dir: str | Path = "data/plugins",
        require_signatures: bool = False,
        production_mode: bool = False,
        policy_engine: PolicyEngine | None = None,
    ):
        self.plugins_dir = Path(plugins_dir).resolve()
        self.production_mode = production_mode
        self.require_signatures = require_signatures or production_mode
        self.policy_engine = policy_engine or (PolicyEngine() if production_mode else None)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.dependency_manager = DependencyManager(self.plugins_dir)
        self.config_manager = PluginConfigManager(self.plugins_dir)
        self.loaded_plugins: dict[str, PluginMetadata] = {}
        self.installed_plugins: dict[str, InstalledPlugin] = {}
        self.discover_installed()

    def discover_installed(self) -> dict[str, PluginMetadata]:
        self.loaded_plugins.clear()
        self.installed_plugins.clear()
        if not self.plugins_dir.exists():
            return self.loaded_plugins
        for child in sorted(self.plugins_dir.iterdir()):
            if not child.is_dir():
                continue
            metadata_path = child / "plugin.yaml"
            if not metadata_path.exists():
                continue
            try:
                metadata = self.read_metadata(metadata_path)
                manifest = self._read_manifest(child)
            except Exception:
                continue
            status = PluginStatus(manifest.get("status") or self._default_status(metadata))
            granted_permissions = manifest.get("granted_permissions") or self._default_granted_permissions(metadata)
            permission_review = manifest.get("permission_review") or self._legacy_permission_review(
                metadata,
                status,
                granted_permissions,
            )
            self.loaded_plugins[metadata.name] = metadata
            self.installed_plugins[metadata.name] = InstalledPlugin(
                metadata=metadata,
                path=str(child),
                package_hash=manifest.get("package_hash"),
                installed_at=manifest.get("installed_at"),
                status=status,
                granted_permissions=granted_permissions,
                permission_review=permission_review,
            )
        return self.loaded_plugins

    def load_from_directory(self, plugin_dir: str | Path) -> PluginMetadata:
        plugin_path = Path(plugin_dir).resolve()
        if not plugin_path.is_dir():
            raise PluginPackageError(f"plugin directory does not exist: {plugin_dir}")
        metadata = self.read_metadata(plugin_path / "plugin.yaml")
        if self.production_mode and metadata.runtime.trust == TrustLevel.THIRD_PARTY:
            raise PluginPackageError(
                f"production mode requires a signed package install for third-party plugin: {metadata.name}"
            )
        self._validate_plugin_layout(plugin_path, metadata)
        self.loaded_plugins[metadata.name] = metadata
        self.installed_plugins[metadata.name] = InstalledPlugin(
            metadata=metadata,
            path=str(plugin_path),
                status=self._default_status(metadata),
                granted_permissions=self._default_granted_permissions(metadata),
                permission_review=self._new_install_permission_review(metadata),
            )
        return metadata

    def load_from_zip(self, zip_path: str | Path) -> PluginMetadata | None:
        try:
            return self.install(zip_path)
        except PluginPackageError as exc:
            print(f"Plugin install failed: {exc}")
            return None

    def install(
        self,
        package_path: str | Path,
        replace: bool = True,
        signature: dict[str, Any] | None = None,
        install_dependencies: bool = False,
        scan_report: dict[str, Any] | None = None,
    ) -> PluginMetadata:
        package = Path(package_path).resolve()
        if not package.exists() or not package.is_file():
            raise PluginPackageError(f"package not found: {package}")
        if package.stat().st_size > MAX_PLUGIN_ARCHIVE_BYTES:
            raise PluginPackageError("plugin archive exceeds size limit")

        package_hash = sha256_file(package)
        staging_root = self.plugins_dir / "_staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        temp_dir = staging_root / package_hash[:16]
        backup_dir = staging_root / f"{package_hash[:16]}.backup"
        self._assert_within_plugins_dir(temp_dir)
        self._assert_within_plugins_dir(backup_dir)
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        temp_dir.mkdir(parents=True)
        try:
            metadata = self._unpack_and_validate(package, temp_dir)
            self._reject_revoked_plugin_version(metadata)
            self._validate_signature_policy(metadata, signature, package_hash)
            self._validate_package_lock_policy(temp_dir, metadata)
            validate_dependency_policy(temp_dir, metadata, production_mode=self.production_mode)
            if self.policy_engine is not None:
                decisions = self.policy_engine.evaluate_install(
                    temp_dir,
                    production_mode=self.production_mode,
                    signature=signature,
                    scan_report=scan_report,
                )
                self.policy_engine.enforce(decisions)
            target_dir = self.plugins_dir / metadata.name
            self._assert_within_plugins_dir(target_dir)

            installed_at = datetime.now(UTC).isoformat()
            initial_status = self._default_status(metadata)
            granted_permissions = self._default_granted_permissions(metadata)
            permission_review = self._new_install_permission_review(metadata)
            previous_installed = self.installed_plugins.get(metadata.name)
            if previous_installed:
                if not replace:
                    raise PluginPackageError(f"plugin already installed: {metadata.name}")
                initial_status = previous_installed.status
                granted_permissions = self._preserve_granted_permissions(previous_installed, metadata)
                permission_review = self._permission_review_for_upgrade(previous_installed, metadata)
                if permission_review["required"] and previous_installed.status != PluginStatus.REVOKED:
                    initial_status = PluginStatus.PENDING_APPROVAL
            self._replace_plugin_tree(temp_dir, target_dir, backup_dir)
            dependency_environment = self.dependency_manager.prepare(
                target_dir,
                metadata,
                install=install_dependencies,
            )
            plugin_config = self.config_manager.prepare(target_dir)
            file_integrity = build_file_integrity_manifest(target_dir)
            self._write_manifest(
                target_dir,
                {
                    "name": metadata.name,
                    "version": metadata.version,
                    "package_hash": package_hash,
                    "installed_at": installed_at,
                    "source": str(package),
                    "status": initial_status.value,
                    "granted_permissions": granted_permissions,
                    "permission_review": permission_review,
                    "signature": signature,
                    "file_integrity": file_integrity,
                    "dependency_environment": (
                        dependency_environment.__dict__ if dependency_environment is not None else None
                    ),
                    "config": (
                        {"keys": sorted(plugin_config.values.keys())} if plugin_config is not None else None
                    ),
                },
            )
            self.loaded_plugins[metadata.name] = metadata
            self.installed_plugins[metadata.name] = InstalledPlugin(
                metadata=metadata,
                path=str(target_dir),
                package_hash=package_hash,
                installed_at=installed_at,
                status=initial_status,
                granted_permissions=granted_permissions,
                permission_review=permission_review,
            )
            return metadata
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            if backup_dir.exists():
                shutil.rmtree(backup_dir, ignore_errors=True)

    def get_plugin(self, name: str) -> PluginMetadata | None:
        return self.loaded_plugins.get(name)

    def get_installed(self, name: str) -> InstalledPlugin | None:
        return self.installed_plugins.get(name)

    def get_all_plugins(self) -> dict[str, PluginMetadata]:
        return dict(self.loaded_plugins)

    def verify_integrity(self, name: str) -> dict[str, Any]:
        installed = self._installed_or_raise(name)
        plugin_dir = Path(installed.path)
        manifest = self._read_manifest(plugin_dir)
        expected_manifest = manifest.get("file_integrity")
        if not isinstance(expected_manifest, dict):
            return {
                "status": "skipped",
                "reason": "missing_file_integrity_manifest",
            }
        if expected_manifest.get("version") != INTEGRITY_MANIFEST_VERSION:
            raise PluginPackageError("unsupported plugin file integrity manifest version")
        if expected_manifest.get("algorithm") != "sha256":
            raise PluginPackageError("unsupported plugin file integrity algorithm")
        expected_files = expected_manifest.get("files")
        if not isinstance(expected_files, dict):
            raise PluginPackageError("plugin file integrity manifest is invalid")

        actual_manifest = build_file_integrity_manifest(plugin_dir)
        actual_files = actual_manifest["files"]
        missing = sorted(set(expected_files) - set(actual_files))
        extra = sorted(set(actual_files) - set(expected_files))
        changed = sorted(
            path
            for path in set(expected_files) & set(actual_files)
            if expected_files[path] != actual_files[path]
        )
        if missing or extra or changed:
            details = []
            if changed:
                details.append(f"changed={changed[:5]}")
            if missing:
                details.append(f"missing={missing[:5]}")
            if extra:
                details.append(f"extra={extra[:5]}")
            raise PluginPackageError(f"plugin integrity check failed for {name}: {'; '.join(details)}")
        return {
            "status": "success",
            "checked_files": len(expected_files),
        }

    def verify_package_lock(self, name: str) -> dict[str, Any]:
        installed = self._installed_or_raise(name)
        lock = read_package_lock(Path(installed.path))
        verify_package_lock(Path(installed.path), lock)
        return {
            "status": "success",
            "checked_files": len(lock["files"]),
        }

    def verify_production_install_policy(self, name: str) -> dict[str, Any]:
        installed = self._installed_or_raise(name)
        metadata = installed.metadata
        if metadata.runtime.trust in {TrustLevel.OFFICIAL, TrustLevel.TRUSTED}:
            return {"status": "skipped", "reason": "trusted_plugin"}
        if metadata.effective_run_mode != RunMode.SUB_PROCESS:
            raise PluginPackageError(
                f"production mode requires third-party plugin {metadata.name} to run in sub_process"
            )

        manifest = self._read_manifest(Path(installed.path))
        self._validate_package_lock_policy(Path(installed.path), metadata)
        validate_dependency_policy(Path(installed.path), metadata, production_mode=True)
        signature = manifest.get("signature")
        if not isinstance(signature, dict):
            raise PluginPackageError(
                f"production mode requires {SIGNATURE_ALGORITHM} signature record for third-party plugin: {name}"
            )
        if signature.get("algorithm") != SIGNATURE_ALGORITHM:
            raise PluginPackageError(
                f"production mode requires {SIGNATURE_ALGORITHM} signatures for third-party plugins"
            )
        if not installed.package_hash or signature.get("hash") != installed.package_hash:
            raise PluginPackageError("production plugin signature hash does not match installed package")
        if not signature.get("signature"):
            raise PluginPackageError("production plugin signature payload is missing signature")
        return {"status": "success", "algorithm": SIGNATURE_ALGORITHM}

    def grant_permissions(
        self,
        name: str,
        permissions: list[dict[str, Any]] | None = None,
        reviewer: str | None = None,
        review_reason: str | None = None,
    ) -> InstalledPlugin:
        installed = self._installed_or_raise(name)
        granted = permissions if permissions is not None else installed.metadata.permissions
        granted = validate_permission_decls(granted, default_compute=False)
        requested = installed.metadata.requested_permissions
        unexpected = {next(iter(item.keys())) for item in granted} - requested
        if unexpected:
            raise PluginPackageError(f"cannot grant permissions not requested by plugin: {sorted(unexpected)}")
        if installed.status in {PluginStatus.REVOKED, PluginStatus.QUARANTINED, PluginStatus.UNINSTALLED}:
            raise PluginPackageError(f"cannot grant permissions to {installed.status.value} plugin: {name}")
        installed.granted_permissions = granted
        installed.permission_review = self._mark_permission_reviewed(installed, reviewer, review_reason)
        installed.status = PluginStatus.ENABLED
        self._persist_installed(installed)
        return installed

    def grant_permission_names(
        self,
        name: str,
        permission_names: list[str],
        reviewer: str | None = None,
        review_reason: str | None = None,
    ) -> InstalledPlugin:
        installed = self._installed_or_raise(name)
        granted = self._permission_decls_for_names(installed.metadata.permissions, permission_names)
        return self.grant_permissions(name, granted, reviewer=reviewer, review_reason=review_reason)

    def set_status(self, name: str, status: PluginStatus | str) -> InstalledPlugin:
        return self.transition_status(name, status)

    def transition_status(
        self,
        name: str,
        status: PluginStatus | str,
        *,
        actor: str | None = None,
        reason: str | None = None,
        audit_logger: AuditLogger | NullAuditLogger | None = None,
        request_id: str | None = None,
    ) -> InstalledPlugin:
        installed = self._installed_or_raise(name)
        target = PluginStatus(status)
        if target != installed.status and target not in ALLOWED_STATUS_TRANSITIONS.get(installed.status, set()):
            message = f"illegal plugin status transition: {installed.status.value} -> {target.value}"
            if audit_logger is not None:
                audit_logger.record(
                    "plugin.status_transition",
                    "error",
                    request_id=request_id or new_request_id(),
                    plugin=name,
                    action="status_transition",
                    details={
                        "from_status": installed.status.value,
                        "to_status": target.value,
                        "reason": message,
                        "actor": actor or "system",
                        "version": installed.metadata.version,
                    },
                )
            raise PluginPackageError(message)
        previous = installed.status
        installed.status = target
        self._persist_installed(installed)
        if audit_logger is not None:
            audit_logger.record(
                "plugin.status_transition",
                "success",
                request_id=request_id or new_request_id(),
                plugin=name,
                action="status_transition",
                details={
                    "from_status": previous.value,
                    "to_status": target.value,
                    "reason": reason or "status_change",
                    "actor": actor or "system",
                    "version": installed.metadata.version,
                },
            )
        return installed

    def enable_plugin(self, name: str) -> InstalledPlugin:
        installed = self._installed_or_raise(name)
        if not installed.granted_permissions:
            raise PluginPackageError(f"plugin has no granted permissions: {name}")
        if installed.permission_review.get("required"):
            raise PluginPackageError(f"plugin requires permission review before enabling: {name}")
        if installed.status in {PluginStatus.REVOKED, PluginStatus.QUARANTINED, PluginStatus.UNINSTALLED}:
            raise PluginPackageError(f"cannot enable {installed.status.value} plugin: {name}")
        return self.transition_status(name, PluginStatus.ENABLED)

    def disable_plugin(self, name: str) -> InstalledPlugin:
        return self.transition_status(name, PluginStatus.DISABLED)

    def quarantine_plugin(self, name: str) -> InstalledPlugin:
        return self.transition_status(name, PluginStatus.QUARANTINED)

    def revoke_plugin(self, name: str) -> InstalledPlugin:
        installed = self._installed_or_raise(name)
        installed.granted_permissions = []
        if installed.status != PluginStatus.REVOKED:
            installed = self.transition_status(name, PluginStatus.REVOKED)
            installed.granted_permissions = []
            self._persist_installed(installed)
        else:
            self._persist_installed(installed)
        return installed

    def revoke_plugin_version(
        self,
        name: str,
        version: str,
        *,
        actor: str | None = None,
        reason: str | None = None,
    ) -> None:
        governance = self._read_governance()
        revoked_versions = governance.setdefault("revoked_plugin_versions", [])
        entry = {"name": name, "version": version, "actor": actor or "admin", "reason": reason or "revoked"}
        if not any(item.get("name") == name and item.get("version") == version for item in revoked_versions):
            revoked_versions.append(entry)
        self._write_governance(governance)
        installed = self.installed_plugins.get(name)
        if installed is not None and installed.metadata.version == version:
            self.revoke_plugin(name)

    def is_plugin_version_revoked(self, name: str, version: str) -> bool:
        governance = self._read_governance()
        revoked_versions = governance.get("revoked_plugin_versions", [])
        if not isinstance(revoked_versions, list):
            return False
        return any(
            isinstance(item, dict) and item.get("name") == name and item.get("version") == version
            for item in revoked_versions
        )

    def read_metadata(self, metadata_path: str | Path) -> PluginMetadata:
        path = Path(metadata_path)
        if not path.exists():
            raise PluginPackageError(f"missing plugin.yaml: {path}")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise PluginPackageError(f"invalid YAML metadata: {exc}") from exc
        if not isinstance(raw, dict):
            raise PluginPackageError("plugin.yaml must contain a mapping")
        try:
            return PluginMetadata(**raw)
        except ValidationError as exc:
            raise PluginPackageError(exc.json(indent=2)) from exc

    def _unpack_and_validate(self, package: Path, target: Path) -> PluginMetadata:
        total_unpacked = 0
        try:
            with zipfile.ZipFile(package, "r") as archive:
                seen = set()
                file_count = 0
                for info in archive.infolist():
                    try:
                        archive_path = normalize_archive_path(info.filename)
                    except PluginValidationError as exc:
                        raise PluginPackageError(str(exc)) from exc
                    if not archive_path or archive_path.endswith("/"):
                        continue
                    file_count += 1
                    if file_count > MAX_PLUGIN_FILES:
                        raise PluginPackageError("plugin archive contains too many files")
                    if archive_path in seen:
                        raise PluginPackageError(f"duplicate archive member: {archive_path}")
                    seen.add(archive_path)
                    if info.file_size < 0:
                        raise PluginPackageError(f"invalid archive member size: {archive_path}")
                    if info.file_size > MAX_PLUGIN_FILE_BYTES:
                        raise PluginPackageError(f"plugin archive member exceeds file size limit: {archive_path}")
                    if info.compress_size > 0 and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                        raise PluginPackageError(f"plugin archive member has suspicious compression ratio: {archive_path}")
                    self._reject_unsafe_zip_member(info, archive_path)
                    total_unpacked += info.file_size
                    if total_unpacked > MAX_UNPACKED_BYTES:
                        raise PluginPackageError("plugin archive expands beyond size limit")

                    destination = (target / archive_path).resolve()
                    if not str(destination).startswith(str(target.resolve())):
                        raise PluginPackageError(f"archive path escapes target: {archive_path}")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info, "r") as src, destination.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        except zipfile.BadZipFile as exc:
            raise PluginPackageError("invalid zip package") from exc

        metadata = self.read_metadata(target / "plugin.yaml")
        self._validate_plugin_layout(target, metadata)
        return metadata

    def _reject_unsafe_zip_member(self, info: zipfile.ZipInfo, archive_path: str) -> None:
        mode = (info.external_attr >> 16) & 0o170000
        if mode in {stat.S_IFLNK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO, stat.S_IFSOCK}:
            raise PluginPackageError(f"archive member type is not allowed: {archive_path}")
        if info.create_system == 3 and mode and mode != stat.S_IFREG:
            raise PluginPackageError(f"archive member is not a regular file: {archive_path}")

    def _validate_plugin_layout(self, plugin_dir: Path, metadata: PluginMetadata) -> None:
        src_dir = plugin_dir / "src"
        if not src_dir.is_dir():
            raise PluginPackageError("plugin package must contain src/")
        for tool_name, entry in metadata.tool_entries().items():
            self._validate_entry_exists(plugin_dir, f"tool {tool_name!r}", entry)
        for middleware_name, entry in metadata.middleware_entries().items():
            self._validate_entry_exists(plugin_dir, f"middleware {middleware_name!r}", entry)
        for provider_name, entry in metadata.memory_provider_entries().items():
            self._validate_entry_exists(plugin_dir, f"memory provider {provider_name!r}", entry)
        for event, entries in metadata.event_listener_entries().items():
            for entry in entries:
                self._validate_entry_exists(plugin_dir, f"event listener {event!r}", entry)

    def _validate_entry_exists(self, plugin_dir: Path, label: str, entry: str) -> None:
        module_name, function_name = entry.split(":", 1)
        module_path = plugin_dir / Path(*module_name.split(".")).with_suffix(".py")
        package_init = plugin_dir / Path(*module_name.split(".")) / "__init__.py"
        if not module_path.exists() and not package_init.exists():
            raise PluginPackageError(f"entry module for {label} not found: {entry}")
        if not function_name:
            raise PluginPackageError(f"entry function missing for {label}")

    def _validate_signature_policy(
        self,
        metadata: PluginMetadata,
        signature: dict[str, Any] | None,
        package_hash: str,
    ) -> None:
        if metadata.runtime.trust in {TrustLevel.OFFICIAL, TrustLevel.TRUSTED}:
            return
        if self.production_mode and metadata.effective_run_mode != RunMode.SUB_PROCESS:
            raise PluginPackageError(
                f"production mode requires third-party plugin {metadata.name} to run in sub_process"
            )
        if self.require_signatures and not signature:
            raise PluginPackageError(f"third-party plugin requires a verified signature: {metadata.name}")
        if not signature:
            return
        if signature.get("algorithm") not in SUPPORTED_SIGNATURE_ALGORITHMS:
            raise PluginPackageError(f"unsupported plugin signature algorithm: {signature.get('algorithm')}")
        if self.production_mode and signature.get("algorithm") == LEGACY_SIGNATURE_ALGORITHM:
            raise PluginPackageError(
                f"production mode requires {SIGNATURE_ALGORITHM} signatures for third-party plugins"
            )
        if signature.get("hash") != package_hash:
            raise PluginPackageError("plugin signature hash does not match package")
        if not signature.get("signature"):
            raise PluginPackageError("plugin signature payload is missing signature")

    def _reject_revoked_plugin_version(self, metadata: PluginMetadata) -> None:
        if self.is_plugin_version_revoked(metadata.name, metadata.version):
            raise PluginPackageError(f"plugin version is revoked: {metadata.name} v{metadata.version}")

    def _validate_package_lock_policy(self, plugin_dir: Path, metadata: PluginMetadata) -> None:
        lock_path = plugin_dir / PACKAGE_LOCK_FILE
        if not lock_path.exists():
            if self.production_mode and metadata.runtime.trust == TrustLevel.THIRD_PARTY:
                raise PluginPackageError(f"production mode requires {PACKAGE_LOCK_FILE}")
            return
        lock = read_package_lock(plugin_dir)
        verify_package_lock(plugin_dir, lock)

    def _assert_within_plugins_dir(self, path: Path) -> None:
        plugins_root = self.plugins_dir.resolve()
        target = path.resolve()
        if plugins_root != target and plugins_root not in target.parents:
            raise PluginPackageError(f"target escapes plugins directory: {target}")

    def _read_manifest(self, plugin_dir: Path) -> dict[str, Any]:
        manifest_path = plugin_dir / MANIFEST_FILE
        if not manifest_path.exists():
            return {}
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _governance_path(self) -> Path:
        return self.plugins_dir / GOVERNANCE_FILE

    def _read_governance(self) -> dict[str, Any]:
        path = self._governance_path()
        if not path.exists():
            return {"version": GOVERNANCE_VERSION, "revoked_plugin_versions": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PluginPackageError(f"invalid plugin governance file: {exc}") from exc
        if not isinstance(payload, dict):
            raise PluginPackageError("plugin governance file must be a JSON object")
        if payload.get("version") != GOVERNANCE_VERSION:
            raise PluginPackageError(f"unsupported plugin governance version: {payload.get('version')}")
        if not isinstance(payload.get("revoked_plugin_versions", []), list):
            raise PluginPackageError("plugin governance revoked_plugin_versions must be a list")
        return payload

    def _write_governance(self, payload: dict[str, Any]) -> None:
        payload.setdefault("version", GOVERNANCE_VERSION)
        payload.setdefault("revoked_plugin_versions", [])
        self._governance_path().write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _write_manifest(self, plugin_dir: Path, payload: dict[str, Any]) -> None:
        (plugin_dir / MANIFEST_FILE).write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _installed_or_raise(self, name: str) -> InstalledPlugin:
        installed = self.installed_plugins.get(name)
        if not installed:
            self.discover_installed()
            installed = self.installed_plugins.get(name)
        if not installed:
            raise PluginPackageError(f"plugin is not installed: {name}")
        return installed

    def _persist_installed(self, installed: InstalledPlugin) -> None:
        plugin_dir = Path(installed.path)
        manifest = self._read_manifest(plugin_dir)
        manifest.update(
            {
                "name": installed.metadata.name,
                "version": installed.metadata.version,
                "package_hash": installed.package_hash,
                "installed_at": installed.installed_at,
                "status": installed.status.value,
                "granted_permissions": installed.granted_permissions,
                "permission_review": installed.permission_review,
            }
        )
        self._write_manifest(plugin_dir, manifest)
        self.installed_plugins[installed.metadata.name] = installed
        self.loaded_plugins[installed.metadata.name] = installed.metadata

    def _default_status(self, metadata: PluginMetadata) -> PluginStatus:
        if metadata.runtime.trust in {TrustLevel.OFFICIAL, TrustLevel.TRUSTED}:
            return PluginStatus.ENABLED
        return PluginStatus.PENDING_APPROVAL

    def _default_granted_permissions(self, metadata: PluginMetadata) -> list[dict[str, Any]]:
        if metadata.runtime.trust in {TrustLevel.OFFICIAL, TrustLevel.TRUSTED}:
            return metadata.permissions
        return []

    def _preserve_granted_permissions(
        self,
        previous: InstalledPlugin,
        metadata: PluginMetadata,
    ) -> list[dict[str, Any]]:
        requested = metadata.requested_permissions
        return [
            item
            for item in previous.granted_permissions
            if next(iter(item.keys())) in requested
        ]

    def _new_install_permission_review(self, metadata: PluginMetadata) -> dict[str, Any]:
        return {
            "required": metadata.runtime.trust == TrustLevel.THIRD_PARTY,
            "reason": "initial_third_party_install" if metadata.runtime.trust == TrustLevel.THIRD_PARTY else "trusted_install",
            "added_permissions": sorted(metadata.requested_permissions),
            "removed_permissions": [],
            "changed_permissions": [],
            "requested_permission_risks": permission_risks(metadata.permissions),
        }

    def _permission_review_for_upgrade(
        self,
        previous: InstalledPlugin,
        metadata: PluginMetadata,
    ) -> dict[str, Any]:
        previous_by_name = self._permission_map(previous.metadata.permissions)
        new_by_name = self._permission_map(metadata.permissions)
        added = sorted(set(new_by_name) - set(previous_by_name))
        removed = sorted(set(previous_by_name) - set(new_by_name))
        changed = sorted(
            name
            for name in set(previous_by_name) & set(new_by_name)
            if previous_by_name[name] != new_by_name[name]
        )
        requires_review = metadata.runtime.trust == TrustLevel.THIRD_PARTY and bool(added or changed)
        return {
            "required": requires_review,
            "reason": "permission_expansion" if requires_review else "no_permission_expansion",
            "added_permissions": added,
            "removed_permissions": removed,
            "changed_permissions": changed,
            "requested_permission_risks": permission_risks(metadata.permissions),
            "added_permission_risks": permission_risks(
                self._permission_decls_for_existing_names(metadata.permissions, added)
            ),
            "changed_permission_risks": permission_risks(
                self._permission_decls_for_existing_names(metadata.permissions, changed)
            ),
        }

    def _permission_map(self, permissions: list[dict[str, Any]]) -> dict[str, Any]:
        return {next(iter(item.keys())): next(iter(item.values())) for item in permissions}

    def _permission_decls_for_names(
        self,
        permissions: list[dict[str, Any]],
        permission_names: list[str],
    ) -> list[dict[str, Any]]:
        requested = self._permission_map(permissions)
        selected_names = list(dict.fromkeys(name.strip() for name in permission_names if name.strip()))
        if not selected_names:
            raise PluginPackageError("at least one permission must be selected")
        unexpected = sorted(set(selected_names) - set(requested))
        if unexpected:
            raise PluginPackageError(f"cannot grant permissions not requested by plugin: {unexpected}")
        return [{name: requested[name]} for name in selected_names]

    def _permission_decls_for_existing_names(
        self,
        permissions: list[dict[str, Any]],
        permission_names: list[str],
    ) -> list[dict[str, Any]]:
        requested = self._permission_map(permissions)
        return [{name: requested[name]} for name in permission_names if name in requested]

    def _mark_permission_reviewed(
        self,
        installed: InstalledPlugin,
        reviewer: str | None,
        review_reason: str | None,
    ) -> dict[str, Any]:
        review = dict(installed.permission_review or {})
        if not review:
            review = self._new_install_permission_review(installed.metadata)
        granted_names = installed.granted_permission_names
        denied_names = sorted(installed.metadata.requested_permissions - granted_names)
        reviewed_at = datetime.now(UTC).isoformat()
        history = list(review.get("history") or [])
        event = {
            "reviewed_at": reviewed_at,
            "reviewer": self._normalize_review_text(reviewer, default="system"),
            "reason": self._normalize_review_text(review_reason, default="not_provided"),
            "granted_permissions": sorted(granted_names),
            "denied_permissions": denied_names,
        }
        history.append(event)
        review.update(
            {
                "required": False,
                "reviewed": True,
                "reviewed_at": reviewed_at,
                "reviewer": event["reviewer"],
                "review_reason": event["reason"],
                "granted_permissions": event["granted_permissions"],
                "denied_permissions": denied_names,
                "granted_permission_risks": permission_risks(installed.granted_permissions),
                "denied_permission_risks": permission_risks(
                    self._permission_decls_for_existing_names(
                        installed.metadata.permissions,
                        denied_names,
                    )
                ),
                "history": history[-50:],
            }
        )
        return review

    def _normalize_review_text(self, value: str | None, default: str) -> str:
        if value is None:
            return default
        cleaned = " ".join(str(value).strip().split())
        if not cleaned:
            return default
        return cleaned[:256]

    def _legacy_permission_review(
        self,
        metadata: PluginMetadata,
        status: PluginStatus,
        granted_permissions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        review = self._new_install_permission_review(metadata)
        granted_names = sorted(next(iter(item.keys())) for item in granted_permissions)
        if status == PluginStatus.ENABLED or granted_permissions:
            review.update(
                {
                    "required": False,
                    "reviewed": True,
                    "reason": "legacy_install_assumed_reviewed",
                    "granted_permissions": granted_names,
                }
            )
        else:
            review.update(
                {
                    "reason": "legacy_install_pending_review",
                    "granted_permissions": granted_names,
                }
            )
        return review

    def _replace_plugin_tree(self, source: Path, target: Path, backup: Path) -> None:
        replaced_existing = False
        try:
            if target.exists():
                target.rename(backup)
                replaced_existing = True
            shutil.copytree(source, target)
        except Exception:
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            if replaced_existing and backup.exists():
                backup.rename(target)
            raise


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_file_integrity_manifest(plugin_dir: str | Path) -> dict[str, Any]:
    plugin_path = Path(plugin_dir).resolve()
    files: dict[str, dict[str, Any]] = {}
    for file_path in sorted(plugin_path.rglob("*")):
        if not file_path.is_file():
            continue
        relative_path = file_path.relative_to(plugin_path).as_posix()
        if integrity_path_excluded(relative_path):
            continue
        files[relative_path] = {
            "sha256": sha256_file(file_path),
            "size": file_path.stat().st_size,
        }
    return {
        "version": INTEGRITY_MANIFEST_VERSION,
        "algorithm": "sha256",
        "files": files,
    }


def integrity_path_excluded(relative_path: str) -> bool:
    parts = Path(relative_path).parts
    if not parts:
        return True
    if parts[0] in INTEGRITY_EXCLUDED_DIRS:
        return True
    if parts[-1] in INTEGRITY_EXCLUDED_FILES:
        return True
    if parts[-1].endswith((".pyc", ".pyo")):
        return True
    return False


def build_package_lock(plugin_dir: str | Path) -> dict[str, Any]:
    plugin_path = Path(plugin_dir).resolve()
    files: dict[str, dict[str, Any]] = {}
    for file_path in sorted(plugin_path.rglob("*")):
        if not file_path.is_file():
            continue
        relative_path = file_path.relative_to(plugin_path).as_posix()
        if relative_path == PACKAGE_LOCK_FILE:
            continue
        if integrity_path_excluded(relative_path):
            continue
        files[relative_path] = {
            "sha256": sha256_file(file_path),
            "size": file_path.stat().st_size,
        }
    return {
        "version": PACKAGE_LOCK_VERSION,
        "algorithm": "sha256",
        "files": files,
    }


def write_package_lock(plugin_dir: str | Path, output: str | Path | None = None) -> Path:
    plugin_path = Path(plugin_dir).resolve()
    lock_path = Path(output) if output is not None else plugin_path / PACKAGE_LOCK_FILE
    if not lock_path.is_absolute():
        lock_path = plugin_path / lock_path
    lock_path = lock_path.resolve()
    try:
        lock_path.relative_to(plugin_path)
    except ValueError as exc:
        raise PluginPackageError("manifest.lock output must stay inside the plugin directory") from exc
    lock = build_package_lock(plugin_path)
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True), encoding="utf-8")
    return lock_path


def read_package_lock(plugin_dir: str | Path) -> dict[str, Any]:
    lock_path = Path(plugin_dir) / PACKAGE_LOCK_FILE
    if not lock_path.exists():
        raise PluginPackageError(f"missing {PACKAGE_LOCK_FILE}")
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PluginPackageError(f"invalid {PACKAGE_LOCK_FILE}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PluginPackageError(f"{PACKAGE_LOCK_FILE} must be a JSON object")
    return payload


def verify_package_lock(plugin_dir: str | Path, lock: dict[str, Any] | None = None) -> None:
    plugin_path = Path(plugin_dir).resolve()
    payload = lock or read_package_lock(plugin_path)
    if payload.get("version") != PACKAGE_LOCK_VERSION:
        raise PluginPackageError(f"unsupported {PACKAGE_LOCK_FILE} version")
    if payload.get("algorithm") != "sha256":
        raise PluginPackageError(f"unsupported {PACKAGE_LOCK_FILE} algorithm")
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        raise PluginPackageError(f"{PACKAGE_LOCK_FILE} files must be a non-empty object")
    actual = build_package_lock(plugin_path)["files"]
    missing = sorted(set(files) - set(actual))
    changed: list[str] = []
    invalid: list[str] = []
    for relative_path, expected in files.items():
        try:
            normalized = normalize_archive_path(str(relative_path))
        except PluginValidationError:
            invalid.append(str(relative_path))
            continue
        if normalized != relative_path or relative_path == PACKAGE_LOCK_FILE:
            invalid.append(str(relative_path))
            continue
        if not isinstance(expected, dict):
            invalid.append(str(relative_path))
            continue
        expected_hash = expected.get("sha256")
        expected_size = expected.get("size")
        actual_entry = actual.get(relative_path)
        if actual_entry is None:
            continue
        if expected_hash != actual_entry.get("sha256") or expected_size != actual_entry.get("size"):
            changed.append(str(relative_path))
    extra = sorted(set(actual) - set(files))
    if invalid or missing or extra or changed:
        details = []
        if invalid:
            details.append(f"invalid={invalid[:5]}")
        if changed:
            details.append(f"changed={changed[:5]}")
        if missing:
            details.append(f"missing={missing[:5]}")
        if extra:
            details.append(f"extra={extra[:5]}")
        raise PluginPackageError(f"{PACKAGE_LOCK_FILE} verification failed: {'; '.join(details)}")
