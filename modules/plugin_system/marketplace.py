from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen

import yaml  # type: ignore[import-untyped]

from .loader import MANIFEST_FILE, MAX_PLUGIN_ARCHIVE_BYTES, PluginLoader, PluginPackageError
from .models import PLUGIN_NAME_PATTERN, SEMVER_PATTERN, PluginMetadata, normalize_archive_path
from .signing import (
    SIGNATURE_ALGORITHM,
    PluginSignatureError,
    normalize_publisher,
    sha256_file,
    verify_signature,
    verify_signature_data,
)


REGISTRY_INDEX_VERSION = 1
MAX_REGISTRY_INDEX_BYTES = 2 * 1024 * 1024
MAX_SIGNATURE_BYTES = 128 * 1024
REGISTRY_CACHE_DIR = "_registry_cache"


class PluginRegistryError(PluginPackageError):
    """Raised when a plugin registry index or artifact cannot be trusted."""


@dataclass(frozen=True)
class RegistryEntry:
    name: str
    version: str
    description: str
    package: str
    sha256: str
    signature: str | None = None
    publisher: str | None = None


@dataclass(frozen=True)
class RegistryInstallResult:
    metadata: PluginMetadata
    entry: RegistryEntry
    package_path: Path
    signature_path: Path | None


@dataclass(frozen=True)
class RegistryIndex:
    source: str
    entries: tuple[RegistryEntry, ...]
    signature: dict[str, Any] | None = None
    revoked_keys: frozenset[str] = frozenset()
    revoked_plugin_versions: frozenset[tuple[str, str]] = frozenset()

    def find(self, name: str, version: str | None = None) -> RegistryEntry:
        matches = [entry for entry in self.entries if entry.name == name]
        if version is not None:
            matches = [entry for entry in matches if entry.version == version]
        if not matches:
            label = f"{name} v{version}" if version else name
            raise PluginRegistryError(f"plugin not found in registry: {label}")
        return sorted(matches, key=lambda item: _semver_sort_key(item.version), reverse=True)[0]


@dataclass(frozen=True)
class _RegistrySource:
    original: str
    remote_base: str | None = None
    local_base: Path | None = None


@dataclass(frozen=True)
class _ResolvedReference:
    value: str | Path
    remote: bool


class PluginRegistryClient:
    """Load a registry index, verify artifacts, and install registry plugins."""

    def __init__(
        self,
        index: str | Path,
        *,
        index_signature: str | Path | None = None,
        cache_dir: str | Path | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.index = str(index)
        self.index_signature = str(index_signature) if index_signature is not None else None
        self.cache_dir = Path(cache_dir).resolve() if cache_dir is not None else None
        self.timeout_seconds = timeout_seconds

    def load_index(
        self,
        *,
        public_key: str | Path | None = None,
        trust_store: str | Path | None = None,
        require_signature: bool = False,
    ) -> RegistryIndex:
        data, payload, source = _read_index_payload(self.index, self.timeout_seconds)
        signature_payload = self._verify_index_signature(
            data,
            source,
            public_key=public_key,
            trust_store=trust_store,
            require_signature=require_signature,
        )
        entries = _parse_index_payload(payload, self.index)
        return RegistryIndex(
            source=source.original,
            entries=tuple(entries),
            signature=signature_payload,
            revoked_keys=frozenset(_parse_revoked_keys(payload)),
            revoked_plugin_versions=frozenset(_parse_revoked_plugin_versions(payload)),
        )

    def list_plugins(
        self,
        *,
        public_key: str | Path | None = None,
        trust_store: str | Path | None = None,
        require_signature: bool = False,
    ) -> list[RegistryEntry]:
        return list(
            self.load_index(
                public_key=public_key,
                trust_store=trust_store,
                require_signature=require_signature,
            ).entries
        )

    def install(
        self,
        name: str,
        *,
        plugins_dir: str | Path = "data/plugins",
        version: str | None = None,
        public_key: str | Path | None = None,
        trust_store: str | Path | None = None,
        require_signature: bool = True,
        index_public_key: str | Path | None = None,
        index_trust_store: str | Path | None = None,
        require_index_signature: bool = False,
        allow_downgrade: bool = False,
        allow_same_version_reinstall: bool = False,
        install_dependencies: bool = False,
        production_mode: bool = False,
        scan_report: dict[str, Any] | None = None,
    ) -> RegistryInstallResult:
        data, payload, source = _read_index_payload(self.index, self.timeout_seconds)
        index_signature_payload = self._verify_index_signature(
            data,
            source,
            public_key=index_public_key or public_key,
            trust_store=index_trust_store or trust_store,
            require_signature=require_index_signature or production_mode,
        )
        index = RegistryIndex(
            source=source.original,
            entries=tuple(_parse_index_payload(payload, self.index)),
            signature=index_signature_payload,
            revoked_keys=frozenset(_parse_revoked_keys(payload)),
            revoked_plugin_versions=frozenset(_parse_revoked_plugin_versions(payload)),
        )
        entry = index.find(name, version)
        if (entry.name, entry.version) in index.revoked_plugin_versions:
            raise PluginRegistryError(f"registry plugin version is revoked: {entry.name} v{entry.version}")

        cache_root = self._cache_root(plugins_dir)
        package_ref = _resolve_artifact_reference(entry.package, source, label="package", expected_suffix=".zip")
        package_path = self._materialize_artifact(
            package_ref,
            cache_root / f"{entry.name}_v{entry.version}_{entry.sha256[:12]}.zip",
            max_bytes=MAX_PLUGIN_ARCHIVE_BYTES,
        )
        actual_hash = sha256_file(package_path)
        if actual_hash != entry.sha256:
            raise PluginRegistryError(
                f"registry package sha256 mismatch for {entry.name} v{entry.version}: "
                f"expected {entry.sha256}, got {actual_hash}"
            )

        package_metadata = _read_package_metadata(package_path)
        if package_metadata.name != entry.name or package_metadata.version != entry.version:
            raise PluginRegistryError(
                "registry entry does not match package metadata: "
                f"index={entry.name} v{entry.version}, "
                f"package={package_metadata.name} v{package_metadata.version}"
            )

        signature_path: Path | None = None
        signature_payload: dict[str, Any] | None = None
        effective_require_signature = require_signature or production_mode
        if effective_require_signature:
            if not entry.signature:
                raise PluginRegistryError(f"registry entry is missing signature: {entry.name} v{entry.version}")
            signature_ref = _resolve_artifact_reference(
                entry.signature,
                source,
                label="signature",
                expected_suffix=".sig",
            )
            signature_path = self._materialize_artifact(
                signature_ref,
                cache_root / f"{entry.name}_v{entry.version}_{entry.sha256[:12]}.zip.sig",
                max_bytes=MAX_SIGNATURE_BYTES,
            )
            try:
                signature_payload = verify_signature(
                    package_path,
                    signature_path,
                    public_key=public_key,
                    trust_store=trust_store,
                )
            except PluginSignatureError as exc:
                raise PluginRegistryError(str(exc)) from exc
            if entry.publisher and signature_payload.get("publisher") != entry.publisher:
                raise PluginRegistryError(
                    "registry publisher does not match package signature: "
                    f"index={entry.publisher}, signature={signature_payload.get('publisher')}"
                )
            if production_mode and signature_payload.get("algorithm") != SIGNATURE_ALGORITHM:
                raise PluginRegistryError(f"production registry install requires {SIGNATURE_ALGORITHM} package signatures")
            if str(signature_payload.get("key_id", "")) in index.revoked_keys:
                raise PluginRegistryError(
                    f"registry publisher key is revoked: {signature_payload.get('key_id')}"
                )

        loader = PluginLoader(plugins_dir, require_signatures=effective_require_signature, production_mode=production_mode)
        _assert_registry_install_not_rollback(
            loader,
            entry,
            actual_hash,
            allow_downgrade=allow_downgrade,
            allow_same_version_reinstall=allow_same_version_reinstall,
        )
        metadata = loader.install(
            package_path,
            signature=signature_payload,
            install_dependencies=install_dependencies,
            scan_report=scan_report,
        )
        _record_registry_source(
            plugins_dir,
            metadata.name,
            {
                "index": index.source,
                "index_signed": index_signature_payload is not None,
                "index_signature": index_signature_payload,
                "publisher": entry.publisher,
                "package": entry.package,
                "package_sha256": actual_hash,
                "signature": entry.signature,
            },
        )
        return RegistryInstallResult(
            metadata=metadata,
            entry=entry,
            package_path=package_path,
            signature_path=signature_path,
        )

    def _cache_root(self, plugins_dir: str | Path) -> Path:
        cache_root = self.cache_dir or Path(plugins_dir).resolve() / REGISTRY_CACHE_DIR
        cache_root.mkdir(parents=True, exist_ok=True)
        return cache_root.resolve()

    def _materialize_artifact(self, ref: _ResolvedReference, destination: Path, *, max_bytes: int) -> Path:
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if ref.remote:
            data = _read_remote_bytes(str(ref.value), self.timeout_seconds, max_bytes)
            destination.write_bytes(data)
            return destination

        source = Path(ref.value).resolve()
        if not source.exists() or not source.is_file():
            raise PluginRegistryError(f"registry artifact not found: {source}")
        size = source.stat().st_size
        if size > max_bytes:
            raise PluginRegistryError(f"registry artifact exceeds size limit: {source}")
        shutil.copyfile(source, destination)
        return destination

    def _verify_index_signature(
        self,
        data: bytes,
        source: _RegistrySource,
        *,
        public_key: str | Path | None,
        trust_store: str | Path | None,
        require_signature: bool,
    ) -> dict[str, Any] | None:
        signature_ref = self._index_signature_ref(source, include_auto=require_signature)
        if signature_ref is None:
            if require_signature:
                raise PluginRegistryError("registry index signature is required")
            return None
        signature_bytes = self._read_signature_artifact(signature_ref)
        try:
            payload = verify_signature_data(
                data,
                signature_bytes,
                public_key=public_key,
                trust_store=trust_store,
            )
        except PluginSignatureError as exc:
            raise PluginRegistryError(str(exc)) from exc
        if require_signature and payload.get("algorithm") != SIGNATURE_ALGORITHM:
            raise PluginRegistryError(f"production registry index requires {SIGNATURE_ALGORITHM} signatures")
        return payload

    def _index_signature_ref(self, source: _RegistrySource, *, include_auto: bool) -> _ResolvedReference | None:
        if self.index_signature is not None:
            if source.local_base is not None:
                parsed = urlparse(self.index_signature)
                if parsed.scheme == "file":
                    return _ResolvedReference(value=Path(unquote(parsed.path)).resolve(), remote=False)
                signature_path = Path(self.index_signature)
                if signature_path.is_absolute():
                    return _ResolvedReference(value=signature_path.resolve(), remote=False)
            return _resolve_artifact_reference(
                self.index_signature,
                source,
                label="index signature",
                expected_suffix=".sig",
            )
        if include_auto and source.local_base is not None:
            index_path = Path(source.original)
            candidate = Path(str(index_path) + ".sig")
            if candidate.exists():
                base = source.local_base.resolve()
                target = candidate.resolve()
                if target != base and base not in target.parents:
                    raise PluginRegistryError("local registry index signature escapes registry directory")
                return _ResolvedReference(value=target, remote=False)
        return None

    def _read_signature_artifact(self, ref: _ResolvedReference) -> bytes:
        if ref.remote:
            return _read_remote_bytes(str(ref.value), self.timeout_seconds, MAX_SIGNATURE_BYTES)
        path = Path(ref.value).resolve()
        if not path.exists() or not path.is_file():
            raise PluginRegistryError(f"registry signature artifact not found: {path}")
        if path.stat().st_size > MAX_SIGNATURE_BYTES:
            raise PluginRegistryError(f"registry signature artifact exceeds size limit: {path}")
        return path.read_bytes()


def load_registry_index(
    index: str | Path,
    *,
    index_signature: str | Path | None = None,
    public_key: str | Path | None = None,
    trust_store: str | Path | None = None,
    require_signature: bool = False,
) -> RegistryIndex:
    return PluginRegistryClient(index, index_signature=index_signature).load_index(
        public_key=public_key,
        trust_store=trust_store,
        require_signature=require_signature,
    )


def _read_index_payload(index: str | Path, timeout_seconds: float) -> tuple[bytes, dict[str, Any], _RegistrySource]:
    index_text = str(index)
    path_candidate = Path(index_text)
    if path_candidate.exists() or not _looks_like_remote_url(index_text):
        path = path_candidate.resolve()
        if path.is_dir():
            path = path / "index.json"
        if not path.exists() or not path.is_file():
            raise PluginRegistryError(f"registry index not found: {path}")
        if path.stat().st_size > MAX_REGISTRY_INDEX_BYTES:
            raise PluginRegistryError("registry index exceeds size limit")
        source = _RegistrySource(original=str(path), local_base=path.parent)
        data = path.read_bytes()
        return data, _decode_index(data), source

    parsed = urlparse(index_text)
    if parsed.scheme == "file":
        path = Path(unquote(parsed.path)).resolve()
        if path.is_dir():
            path = path / "index.json"
        if not path.exists() or not path.is_file():
            raise PluginRegistryError(f"registry index not found: {path}")
        if path.stat().st_size > MAX_REGISTRY_INDEX_BYTES:
            raise PluginRegistryError("registry index exceeds size limit")
        source = _RegistrySource(original=str(path), local_base=path.parent)
        data = path.read_bytes()
        return data, _decode_index(data), source

    if parsed.scheme not in {"http", "https"}:
        raise PluginRegistryError(f"unsupported registry index scheme: {parsed.scheme}")
    if parsed.username or parsed.password or parsed.fragment:
        raise PluginRegistryError("registry index URL must not contain credentials or fragments")
    data = _read_remote_bytes(index_text, timeout_seconds, MAX_REGISTRY_INDEX_BYTES)
    source = _RegistrySource(original=index_text, remote_base=index_text)
    return data, _decode_index(data), source


def _decode_index(data: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PluginRegistryError(f"invalid registry index JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PluginRegistryError("registry index must be a JSON object")
    return payload


def _parse_index_payload(payload: dict[str, Any], source: str | Path) -> list[RegistryEntry]:
    if payload.get("version") != REGISTRY_INDEX_VERSION:
        raise PluginRegistryError(f"unsupported registry index version: {payload.get('version')}")
    raw_entries = payload.get("plugins")
    if not isinstance(raw_entries, list):
        raise PluginRegistryError("registry index plugins must be a list")
    if len(raw_entries) > 1000:
        raise PluginRegistryError("registry index contains too many plugins")

    entries: list[RegistryEntry] = []
    seen: set[tuple[str, str]] = set()
    for position, raw_entry in enumerate(raw_entries, start=1):
        if not isinstance(raw_entry, dict):
            raise PluginRegistryError(f"registry entry {position} must be an object")
        entry = _parse_entry(raw_entry, position)
        key = (entry.name, entry.version)
        if key in seen:
            raise PluginRegistryError(f"duplicate registry entry: {entry.name} v{entry.version}")
        seen.add(key)
        entries.append(entry)
    return entries


def _parse_revoked_keys(payload: dict[str, Any]) -> set[str]:
    raw = payload.get("revoked_keys", [])
    if raw is None:
        return set()
    if not isinstance(raw, list):
        raise PluginRegistryError("registry revoked_keys must be a list")
    revoked: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise PluginRegistryError("registry revoked key entries must be strings")
        key_id = item.strip()
        if len(key_id) > 128 or any(ord(char) < 32 for char in key_id):
            raise PluginRegistryError("registry revoked key entry is invalid")
        revoked.add(key_id)
    return revoked


def _parse_revoked_plugin_versions(payload: dict[str, Any]) -> set[tuple[str, str]]:
    raw = payload.get("revoked_plugin_versions", [])
    if raw is None:
        return set()
    if not isinstance(raw, list):
        raise PluginRegistryError("registry revoked_plugin_versions must be a list")
    revoked: set[tuple[str, str]] = set()
    for position, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise PluginRegistryError(f"registry revoked plugin entry {position} must be an object")
        name = _required_string(item, "name", position)
        version = _required_string(item, "version", position)
        if not PLUGIN_NAME_PATTERN.match(name):
            raise PluginRegistryError(f"invalid revoked registry plugin name: {name}")
        if not SEMVER_PATTERN.match(version):
            raise PluginRegistryError(f"invalid revoked registry plugin version: {version}")
        revoked.add((name, version))
    return revoked


def _parse_entry(raw: dict[str, Any], position: int) -> RegistryEntry:
    name = _required_string(raw, "name", position)
    if not PLUGIN_NAME_PATTERN.match(name):
        raise PluginRegistryError(f"invalid registry plugin name: {name}")
    version = _required_string(raw, "version", position)
    if not SEMVER_PATTERN.match(version):
        raise PluginRegistryError(f"invalid registry plugin version: {version}")
    description = _required_string(raw, "description", position, max_length=512)
    package = _required_string(raw, "package", position, max_length=2048)
    _validate_artifact_reference_shape(package, "package", ".zip")
    digest = _required_string(raw, "sha256", position)
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise PluginRegistryError(f"invalid registry sha256 for {name} v{version}")
    signature = raw.get("signature")
    if signature is not None:
        if not isinstance(signature, str):
            raise PluginRegistryError(f"registry entry {position} signature must be a string")
        signature = signature.strip()
        _validate_artifact_reference_shape(signature, "signature", ".sig")
    publisher = raw.get("publisher")
    if publisher is not None:
        if not isinstance(publisher, str):
            raise PluginRegistryError(f"registry entry {position} publisher must be a string")
        publisher = normalize_publisher(publisher)
    return RegistryEntry(
        name=name,
        version=version,
        description=description,
        package=package,
        sha256=digest,
        signature=signature,
        publisher=publisher,
    )


def _required_string(raw: dict[str, Any], key: str, position: int, *, max_length: int = 256) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise PluginRegistryError(f"registry entry {position} {key} must be a string")
    value = value.strip()
    if not value:
        raise PluginRegistryError(f"registry entry {position} {key} is required")
    if len(value) > max_length:
        raise PluginRegistryError(f"registry entry {position} {key} is too long")
    if any(ord(char) < 32 for char in value):
        raise PluginRegistryError(f"registry entry {position} {key} contains control characters")
    return value


def _validate_artifact_reference_shape(reference: str, label: str, expected_suffix: str) -> None:
    parsed = urlparse(reference)
    if parsed.username or parsed.password or parsed.fragment:
        raise PluginRegistryError(f"registry {label} reference must not contain credentials or fragments")
    if any(ord(char) < 32 for char in reference):
        raise PluginRegistryError(f"registry {label} reference contains control characters")
    if "?" in reference:
        raise PluginRegistryError(f"registry {label} reference must not contain a query string")
    if not parsed.path.lower().endswith(expected_suffix):
        raise PluginRegistryError(f"registry {label} reference must end with {expected_suffix}")


def _resolve_artifact_reference(
    reference: str,
    source: _RegistrySource,
    *,
    label: str,
    expected_suffix: str,
) -> _ResolvedReference:
    _validate_artifact_reference_shape(reference, label, expected_suffix)
    parsed_ref = urlparse(reference)
    if source.remote_base:
        resolved = urljoin(source.remote_base, reference)
        parsed_base = urlparse(source.remote_base)
        parsed = urlparse(resolved)
        if parsed.scheme not in {"http", "https"}:
            raise PluginRegistryError(f"unsupported registry {label} scheme: {parsed.scheme}")
        if parsed.scheme != parsed_base.scheme or parsed.netloc != parsed_base.netloc:
            raise PluginRegistryError(f"registry {label} reference escapes registry origin")
        if parsed.username or parsed.password or parsed.fragment:
            raise PluginRegistryError(f"registry {label} reference must not contain credentials or fragments")
        return _ResolvedReference(value=resolved, remote=True)

    if source.local_base is None:
        raise PluginRegistryError("registry source has no artifact base")
    if parsed_ref.scheme:
        raise PluginRegistryError(f"local registry {label} reference must be relative")
    ref_path = Path(reference)
    if ref_path.is_absolute():
        raise PluginRegistryError(f"local registry {label} reference must be relative")
    target = (source.local_base / ref_path).resolve()
    base = source.local_base.resolve()
    if target != base and base not in target.parents:
        raise PluginRegistryError(f"local registry {label} reference escapes registry directory")
    return _ResolvedReference(value=target, remote=False)


def _read_remote_bytes(url: str, timeout_seconds: float, max_bytes: int) -> bytes:
    request = Request(url, headers={"User-Agent": "humanoid-agi-plugin-registry/1"})
    with urlopen(request, timeout=timeout_seconds) as response:
        return _read_limited(response, max_bytes)


def _read_limited(handle: Any, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining > 0:
        chunk = handle.read(min(1024 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) > max_bytes:
        raise PluginRegistryError("registry response exceeds size limit")
    return data


def _read_package_metadata(package_path: Path) -> PluginMetadata:
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            metadata_infos = [
                info
                for info in archive.infolist()
                if normalize_archive_path(info.filename) == "plugin.yaml"
            ]
            if len(metadata_infos) != 1:
                raise PluginRegistryError("plugin package must contain exactly one plugin.yaml")
            info = metadata_infos[0]
            if info.file_size > 64 * 1024:
                raise PluginRegistryError("plugin.yaml exceeds registry metadata size limit")
            raw = yaml.safe_load(archive.read(info).decode("utf-8"))
    except zipfile.BadZipFile as exc:
        raise PluginRegistryError("invalid plugin zip package") from exc
    except UnicodeDecodeError as exc:
        raise PluginRegistryError("plugin.yaml must be UTF-8") from exc
    if not isinstance(raw, dict):
        raise PluginRegistryError("plugin.yaml must contain a mapping")
    try:
        return PluginMetadata(**raw)
    except Exception as exc:
        raise PluginRegistryError(f"invalid plugin metadata in registry package: {exc}") from exc


def _assert_registry_install_not_rollback(
    loader: PluginLoader,
    entry: RegistryEntry,
    package_hash: str,
    *,
    allow_downgrade: bool,
    allow_same_version_reinstall: bool,
) -> None:
    installed = loader.get_installed(entry.name)
    if installed is None:
        return
    current_key = _semver_sort_key(installed.metadata.version)
    requested_key = _semver_sort_key(entry.version)
    if requested_key < current_key and not allow_downgrade:
        raise PluginRegistryError(
            f"registry install would downgrade {entry.name} "
            f"from v{installed.metadata.version} to v{entry.version}"
        )
    if requested_key == current_key and installed.package_hash != package_hash and not allow_same_version_reinstall:
        raise PluginRegistryError(
            f"registry install would replace same version {entry.name} v{entry.version} "
            "with different package content"
        )


def _record_registry_source(plugins_dir: str | Path, name: str, registry_source: dict[str, Any]) -> None:
    manifest_path = Path(plugins_dir).resolve() / name / MANIFEST_FILE
    if not manifest_path.exists():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PluginRegistryError(f"invalid installed plugin manifest: {manifest_path}") from exc
    if not isinstance(manifest, dict):
        raise PluginRegistryError(f"installed plugin manifest must be an object: {manifest_path}")
    manifest["registry"] = registry_source
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def _looks_like_remote_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https", "file"}


def _semver_sort_key(version: str) -> tuple[int, int, int, int, str]:
    core, separator, suffix = version.partition("-")
    major, minor, patch = [int(part) for part in core.split(".")]
    return major, minor, patch, 1 if not separator else 0, suffix
