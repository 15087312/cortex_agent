from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .dependency import DEPENDENCY_LOCK_FILE
from .loader import build_file_integrity_manifest
from .models import PluginMetadata


class PluginSbomError(ValueError):
    """Raised when an SBOM cannot be generated from plugin metadata."""


def generate_sbom(plugin_dir: str | Path, metadata: PluginMetadata | None = None) -> dict[str, Any]:
    """Generate a minimal CycloneDX JSON SBOM for a plugin directory."""

    plugin_path = Path(plugin_dir).resolve()
    metadata = metadata or _read_metadata(plugin_path)
    file_integrity = build_file_integrity_manifest(plugin_path)
    components = [
        {
            "type": "file",
            "name": path,
            "hashes": [{"alg": "SHA-256", "content": item["sha256"]}],
            "properties": [{"name": "humanoid:size", "value": str(item["size"])}],
        }
        for path, item in sorted(file_integrity["files"].items())
    ]
    components.extend(_dependency_components(plugin_path))
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(),
            "component": {
                "type": "application",
                "name": metadata.name,
                "version": metadata.version,
                "purl": f"pkg:generic/{metadata.name}@{metadata.version}",
            },
        },
        "components": components,
    }


def write_sbom(plugin_dir: str | Path, output: str | Path | None = None) -> Path:
    plugin_path = Path(plugin_dir).resolve()
    output_path = Path(output) if output is not None else plugin_path / "sbom.cdx.json"
    if not output_path.is_absolute():
        output_path = plugin_path / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(generate_sbom(plugin_path), indent=2, sort_keys=True), encoding="utf-8")
    return output_path


def _read_metadata(plugin_path: Path) -> PluginMetadata:
    import yaml  # type: ignore[import-untyped]

    metadata_path = plugin_path / "plugin.yaml"
    if not metadata_path.exists():
        raise PluginSbomError(f"missing plugin.yaml: {metadata_path}")
    raw = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise PluginSbomError("plugin.yaml must contain a mapping")
    return PluginMetadata(**raw)


def _dependency_components(plugin_path: Path) -> list[dict[str, Any]]:
    lockfile = plugin_path / DEPENDENCY_LOCK_FILE
    if not lockfile.exists():
        return []
    components: list[dict[str, Any]] = []
    for line in lockfile.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "--hash=sha256:" not in stripped:
            continue
        requirement, hash_part = stripped.split("--hash=sha256:", 1)
        if "==" not in requirement:
            continue
        name, version = [item.strip() for item in requirement.split("==", 1)]
        digest = hash_part.strip().split()[0]
        components.append(
            {
                "type": "library",
                "name": name,
                "version": version,
                "purl": f"pkg:pypi/{name}@{version}",
                "hashes": [{"alg": "SHA-256", "content": digest}],
            }
        )
    return components
