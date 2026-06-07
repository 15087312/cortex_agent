from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_SCHEMA_FILE = "config_schema.json"
CONFIG_FILE = "config.json"
CONFIG_EFFECTIVE_FILE = ".plugin-config.json"


class PluginConfigError(ValueError):
    """Raised when plugin configuration or schema is invalid."""


@dataclass
class PluginConfig:
    schema: dict[str, Any]
    values: dict[str, Any]


class PluginConfigManager:
    """Validate and read per-plugin configuration using a small JSON Schema subset."""

    def __init__(self, plugins_dir: str | Path = "data/plugins"):
        self.plugins_dir = Path(plugins_dir).resolve()

    def prepare(self, plugin_dir: str | Path) -> PluginConfig | None:
        plugin_path = Path(plugin_dir).resolve()
        schema_path = plugin_path / CONFIG_SCHEMA_FILE
        config_path = plugin_path / CONFIG_FILE
        if not schema_path.exists():
            if config_path.exists():
                raise PluginConfigError("config.json requires config_schema.json")
            return None
        schema = self._read_json_object(schema_path, "config_schema.json")
        self._validate_schema_shape(schema)
        raw_config = self._read_json_object(config_path, "config.json") if config_path.exists() else {}
        values = self._apply_defaults_and_validate(schema, raw_config)
        effective = PluginConfig(schema=schema, values=values)
        self.write_effective_config(plugin_path, effective)
        return effective

    def read_value(self, plugin_name: str, key: str, default: Any = None) -> Any:
        if not key or "." in key or "/" in key or "\\" in key:
            raise PluginConfigError(f"invalid config key: {key}")
        config = self.read_effective_config(self.plugins_dir / plugin_name)
        if not config:
            return default
        return config.values.get(key, default)

    def has_value(self, plugin_name: str, key: str) -> bool:
        if not key or "." in key or "/" in key or "\\" in key:
            raise PluginConfigError(f"invalid config key: {key}")
        config = self.read_effective_config(self.plugins_dir / plugin_name)
        return bool(config and key in config.values)

    def read_effective_config(self, plugin_dir: str | Path) -> PluginConfig | None:
        config_path = Path(plugin_dir) / CONFIG_EFFECTIVE_FILE
        if not config_path.exists():
            return None
        payload = self._read_json_object(config_path, CONFIG_EFFECTIVE_FILE)
        schema = payload.get("schema")
        values = payload.get("values")
        if not isinstance(schema, dict) or not isinstance(values, dict):
            raise PluginConfigError(f"{CONFIG_EFFECTIVE_FILE} is invalid")
        return PluginConfig(schema=schema, values=values)

    def write_effective_config(self, plugin_dir: str | Path, config: PluginConfig) -> None:
        payload = {
            "schema": config.schema,
            "values": config.values,
        }
        (Path(plugin_dir) / CONFIG_EFFECTIVE_FILE).write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _read_json_object(self, path: Path, label: str) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PluginConfigError(f"invalid {label}: {exc}") from exc
        if not isinstance(payload, dict):
            raise PluginConfigError(f"{label} must contain a JSON object")
        return payload

    def _validate_schema_shape(self, schema: dict[str, Any]) -> None:
        if schema.get("type") != "object":
            raise PluginConfigError("config_schema.json root type must be object")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise PluginConfigError("config_schema.json properties must be an object")
        required = schema.get("required", [])
        if required is not None and not isinstance(required, list):
            raise PluginConfigError("config_schema.json required must be a list")
        for name, spec in properties.items():
            if not isinstance(name, str) or not name:
                raise PluginConfigError("config property names must be non-empty strings")
            if not isinstance(spec, dict):
                raise PluginConfigError(f"config property {name} must be an object")
            expected_type = spec.get("type")
            if expected_type not in {"string", "integer", "number", "boolean", "array", "object"}:
                raise PluginConfigError(f"config property {name} has unsupported type: {expected_type}")

    def _apply_defaults_and_validate(self, schema: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        unknown = set(values) - set(properties)
        if unknown:
            raise PluginConfigError(f"unknown config keys: {sorted(unknown)}")
        effective: dict[str, Any] = {}
        for name, spec in properties.items():
            if name in values:
                value = values[name]
            elif "default" in spec:
                value = spec["default"]
            elif name in required:
                raise PluginConfigError(f"missing required config key: {name}")
            else:
                continue
            self._validate_value(name, value, str(spec["type"]))
            effective[name] = value
        return effective

    def _validate_value(self, name: str, value: Any, expected_type: str) -> None:
        if expected_type == "string" and not isinstance(value, str):
            raise PluginConfigError(f"config key {name} must be string")
        if expected_type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise PluginConfigError(f"config key {name} must be integer")
        if expected_type == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise PluginConfigError(f"config key {name} must be number")
        if expected_type == "boolean" and not isinstance(value, bool):
            raise PluginConfigError(f"config key {name} must be boolean")
        if expected_type == "array" and not isinstance(value, list):
            raise PluginConfigError(f"config key {name} must be array")
        if expected_type == "object" and not isinstance(value, dict):
            raise PluginConfigError(f"config key {name} must be object")
