from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from .tool_contracts import utc_now


SUPPORTED_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}
SCHEMA_OBJECT_KEYS = {
    "type",
    "properties",
    "required",
    "items",
    "enum",
    "const",
    "additionalProperties",
    "maxLength",
    "minLength",
    "pattern",
    "format",
    "maximum",
    "minimum",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "multipleOf",
    "maxItems",
    "minItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
    "propertyNames",
    "dependentRequired",
    "dependencies",
    "description",
}
SUPPORTED_STRING_FORMATS = {"email", "uri", "date-time", "uuid"}


@dataclass(frozen=True)
class SchemaViolation:
    instance_path: str
    schema_path: str
    violation: str
    expected: str
    actual_type: str


class SchemaValidationError(ValueError):
    """Raised when a JSON value does not match the supported schema subset."""

    def __init__(self, violation: SchemaViolation):
        super().__init__(
            f"{violation.instance_path}: {violation.violation} "
            f"(expected {violation.expected}, got {violation.actual_type})"
        )
        self.violation = violation


class SchemaDefinitionError(ValueError):
    """Raised when a schema uses unsupported or invalid keywords."""


def validate_json_schema(schema: dict[str, Any], *, schema_path: str = "$") -> None:
    """Validate the supported JSON Schema subset without external dependencies."""

    if not isinstance(schema, dict):
        raise SchemaDefinitionError(f"{schema_path} must be an object")
    if not schema:
        raise SchemaDefinitionError(f"{schema_path} cannot be empty")
    unknown = sorted(set(schema) - SCHEMA_OBJECT_KEYS)
    if unknown:
        raise SchemaDefinitionError(f"{schema_path} contains unsupported keywords: {', '.join(unknown)}")
    schema_type = schema.get("type")
    if schema_type is not None and schema_type not in SUPPORTED_TYPES:
        raise SchemaDefinitionError(f"{schema_path}.type is unsupported: {schema_type}")
    if "enum" in schema and not isinstance(schema["enum"], list):
        raise SchemaDefinitionError(f"{schema_path}.enum must be an array")
    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise SchemaDefinitionError(f"{schema_path}.required must be an array of strings")
    if "properties" in schema:
        properties = schema["properties"]
        if not isinstance(properties, dict):
            raise SchemaDefinitionError(f"{schema_path}.properties must be an object")
        for name, child in properties.items():
            if not isinstance(name, str):
                raise SchemaDefinitionError(f"{schema_path}.properties keys must be strings")
            validate_json_schema(child, schema_path=f"{schema_path}.properties.{name}")
    if "items" in schema:
        validate_json_schema(schema["items"], schema_path=f"{schema_path}.items")
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        raise SchemaDefinitionError(f"{schema_path}.additionalProperties only supports true or false")
    for key in ("maxLength", "minLength", "maxItems", "minItems", "minProperties", "maxProperties"):
        if key in schema and (not isinstance(schema[key], int) or isinstance(schema[key], bool) or schema[key] < 0):
            raise SchemaDefinitionError(f"{schema_path}.{key} must be a non-negative integer")
    for key in ("maximum", "minimum", "exclusiveMaximum", "exclusiveMinimum", "multipleOf"):
        if key in schema and (not isinstance(schema[key], (int, float)) or isinstance(schema[key], bool)):
            raise SchemaDefinitionError(f"{schema_path}.{key} must be a number")
    if "multipleOf" in schema and schema["multipleOf"] <= 0:
        raise SchemaDefinitionError(f"{schema_path}.multipleOf must be greater than zero")
    if "uniqueItems" in schema and not isinstance(schema["uniqueItems"], bool):
        raise SchemaDefinitionError(f"{schema_path}.uniqueItems must be a boolean")
    if "pattern" in schema:
        _validate_regex(schema["pattern"], f"{schema_path}.pattern")
    if "format" in schema and schema["format"] not in SUPPORTED_STRING_FORMATS:
        raise SchemaDefinitionError(
            f"{schema_path}.format is unsupported: {schema['format']}"
        )
    if "propertyNames" in schema:
        property_names = schema["propertyNames"]
        if not isinstance(property_names, dict):
            raise SchemaDefinitionError(f"{schema_path}.propertyNames must be an object")
        if set(property_names) - {"pattern"}:
            raise SchemaDefinitionError(f"{schema_path}.propertyNames only supports pattern")
        if "pattern" in property_names:
            _validate_regex(property_names["pattern"], f"{schema_path}.propertyNames.pattern")
    for key in ("dependentRequired", "dependencies"):
        if key not in schema:
            continue
        raw = schema[key]
        if not isinstance(raw, dict):
            raise SchemaDefinitionError(f"{schema_path}.{key} must be an object")
        for name, dependencies in raw.items():
            if not isinstance(name, str):
                raise SchemaDefinitionError(f"{schema_path}.{key} keys must be strings")
            if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
                raise SchemaDefinitionError(f"{schema_path}.{key}.{name} must be an array of strings")


def validate_json_value(value: Any, schema: dict[str, Any]) -> None:
    validate_json_schema(schema)
    _validate(value, schema, instance_path="$", schema_path="$")


def _validate(value: Any, schema: dict[str, Any], *, instance_path: str, schema_path: str) -> None:
    if "const" in schema and value != schema["const"]:
        _raise(instance_path, f"{schema_path}.const", "const_mismatch", repr(schema["const"]), value)
    if "enum" in schema and value not in schema["enum"]:
        _raise(instance_path, f"{schema_path}.enum", "enum_mismatch", f"one of {schema['enum']!r}", value)

    schema_type = schema.get("type")
    if schema_type is not None and not _type_matches(value, schema_type):
        _raise(instance_path, f"{schema_path}.type", "type_mismatch", schema_type, value)

    if schema_type == "object" or ("properties" in schema and isinstance(value, dict)):
        _validate_object(value, schema, instance_path=instance_path, schema_path=schema_path)
    if schema_type == "array" or ("items" in schema and isinstance(value, list)):
        _validate_array(value, schema, instance_path=instance_path, schema_path=schema_path)
    if isinstance(value, str):
        _validate_string(value, schema, instance_path=instance_path, schema_path=schema_path)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        _validate_number(value, schema, instance_path=instance_path, schema_path=schema_path)


def _validate_object(value: Any, schema: dict[str, Any], *, instance_path: str, schema_path: str) -> None:
    if not isinstance(value, dict):
        _raise(instance_path, f"{schema_path}.type", "type_mismatch", "object", value)
    if "minProperties" in schema and len(value) < int(schema["minProperties"]):
        _raise(
            instance_path,
            f"{schema_path}.minProperties",
            "too_few_properties",
            f"property count >= {schema['minProperties']}",
            value,
        )
    if "maxProperties" in schema and len(value) > int(schema["maxProperties"]):
        _raise(
            instance_path,
            f"{schema_path}.maxProperties",
            "too_many_properties",
            f"property count <= {schema['maxProperties']}",
            value,
        )
    property_names = schema.get("propertyNames")
    if isinstance(property_names, dict) and "pattern" in property_names:
        pattern = re.compile(str(property_names["pattern"]))
        for name in value:
            if not pattern.search(str(name)):
                _raise(
                    _child_path(instance_path, str(name)),
                    f"{schema_path}.propertyNames.pattern",
                    "property_name_pattern_mismatch",
                    f"property name matching {property_names['pattern']!r}",
                    str(name),
                )
    required = schema.get("required") or []
    for name in required:
        if name not in value:
            raise SchemaValidationError(
                SchemaViolation(
                    instance_path=_child_path(instance_path, name),
                    schema_path=f"{schema_path}.required",
                    violation="required_property_missing",
                    expected=f"required property {name}",
                    actual_type="missing",
                )
            )
    _validate_dependent_required(value, schema, instance_path=instance_path, schema_path=schema_path)
    properties = schema.get("properties") or {}
    if schema.get("additionalProperties") is False:
        unexpected = sorted(set(value) - set(properties))
        if unexpected:
            name = unexpected[0]
            raise SchemaValidationError(
                SchemaViolation(
                    instance_path=_child_path(instance_path, name),
                    schema_path=f"{schema_path}.additionalProperties",
                    violation="additional_property_not_allowed",
                    expected="declared property",
                    actual_type=_json_type(value.get(name)),
                )
            )
    for name, child_schema in properties.items():
        if name in value:
            _validate(
                value[name],
                child_schema,
                instance_path=_child_path(instance_path, name),
                schema_path=f"{schema_path}.properties.{name}",
            )


def _validate_dependent_required(
    value: dict[str, Any],
    schema: dict[str, Any],
    *,
    instance_path: str,
    schema_path: str,
) -> None:
    for keyword in ("dependentRequired", "dependencies"):
        raw = schema.get(keyword)
        if not isinstance(raw, dict):
            continue
        for name, dependencies in raw.items():
            if name not in value:
                continue
            for dependency in dependencies:
                if dependency not in value:
                    raise SchemaValidationError(
                        SchemaViolation(
                            instance_path=_child_path(instance_path, dependency),
                            schema_path=f"{schema_path}.{keyword}.{name}",
                            violation="dependent_required_missing",
                            expected=f"{dependency} when {name} is present",
                            actual_type="missing",
                        )
                    )


def _validate_array(value: Any, schema: dict[str, Any], *, instance_path: str, schema_path: str) -> None:
    if not isinstance(value, list):
        _raise(instance_path, f"{schema_path}.type", "type_mismatch", "array", value)
    if "minItems" in schema and len(value) < int(schema["minItems"]):
        _raise(instance_path, f"{schema_path}.minItems", "too_few_items", f"items >= {schema['minItems']}", value)
    if "maxItems" in schema and len(value) > int(schema["maxItems"]):
        _raise(instance_path, f"{schema_path}.maxItems", "too_many_items", f"items <= {schema['maxItems']}", value)
    if schema.get("uniqueItems") is True:
        seen: set[str] = set()
        for index, item in enumerate(value):
            marker = _stable_json_marker(item)
            if marker in seen:
                _raise(
                    f"{instance_path}[{index}]",
                    f"{schema_path}.uniqueItems",
                    "items_not_unique",
                    "unique array items",
                    item,
                )
            seen.add(marker)
    item_schema = schema.get("items")
    if item_schema is None:
        return
    for index, item in enumerate(value):
        _validate(
            item,
            item_schema,
            instance_path=f"{instance_path}[{index}]",
            schema_path=f"{schema_path}.items",
        )


def _validate_string(value: str, schema: dict[str, Any], *, instance_path: str, schema_path: str) -> None:
    if "minLength" in schema and len(value) < int(schema["minLength"]):
        _raise(instance_path, f"{schema_path}.minLength", "string_too_short", f"length >= {schema['minLength']}", value)
    if "maxLength" in schema and len(value) > int(schema["maxLength"]):
        _raise(instance_path, f"{schema_path}.maxLength", "string_too_long", f"length <= {schema['maxLength']}", value)
    if "pattern" in schema and not re.search(str(schema["pattern"]), value):
        _raise(instance_path, f"{schema_path}.pattern", "pattern_mismatch", f"match {schema['pattern']!r}", value)
    if "format" in schema and not _format_matches(value, str(schema["format"])):
        _raise(instance_path, f"{schema_path}.format", "format_mismatch", str(schema["format"]), value)


def _validate_number(value: int | float, schema: dict[str, Any], *, instance_path: str, schema_path: str) -> None:
    if "minimum" in schema and value < schema["minimum"]:
        _raise(instance_path, f"{schema_path}.minimum", "number_too_small", f">= {schema['minimum']}", value)
    if "maximum" in schema and value > schema["maximum"]:
        _raise(instance_path, f"{schema_path}.maximum", "number_too_large", f"<= {schema['maximum']}", value)
    if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
        _raise(
            instance_path,
            f"{schema_path}.exclusiveMinimum",
            "number_too_small",
            f"> {schema['exclusiveMinimum']}",
            value,
        )
    if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
        _raise(
            instance_path,
            f"{schema_path}.exclusiveMaximum",
            "number_too_large",
            f"< {schema['exclusiveMaximum']}",
            value,
        )
    if "multipleOf" in schema and not _multiple_of(value, schema["multipleOf"]):
        _raise(instance_path, f"{schema_path}.multipleOf", "not_multiple_of", str(schema["multipleOf"]), value)


def _type_matches(value: Any, schema_type: str) -> bool:
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "null":
        return value is None
    return False


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _child_path(parent: str, key: str) -> str:
    if key.replace("_", "").isalnum():
        return f"{parent}.{key}"
    return f"{parent}[{key!r}]"


def _raise(instance_path: str, schema_path: str, violation: str, expected: str, value: Any) -> None:
    raise SchemaValidationError(
        SchemaViolation(
            instance_path=instance_path,
            schema_path=schema_path,
            violation=violation,
            expected=expected,
            actual_type=_json_type(value),
        )
    )


def _validate_regex(value: Any, schema_path: str) -> None:
    if not isinstance(value, str):
        raise SchemaDefinitionError(f"{schema_path} must be a string")
    try:
        re.compile(value)
    except re.error as exc:
        raise SchemaDefinitionError(f"{schema_path} is not a valid regex: {exc}") from exc


def _format_matches(value: str, format_name: str) -> bool:
    # Lightweight format checks, not a complete RFC implementation.
    if format_name == "email":
        return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value))
    if format_name == "uri":
        parsed = urlparse(value)
        return bool(parsed.scheme and parsed.netloc)
    if format_name == "date-time":
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return True
    if format_name == "uuid":
        try:
            uuid.UUID(value)
        except (TypeError, ValueError):
            return False
        return True
    return False


def _multiple_of(value: int | float, divisor: int | float) -> bool:
    quotient = value / divisor
    return abs(quotient - round(quotient)) < 1e-9


def _stable_json_marker(value: Any) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


def run_schema_validation_selftest() -> dict[str, Any]:
    checks = {
        "min_items": _rejects({"type": "array", "minItems": 2}, [1]),
        "max_items": _rejects({"type": "array", "maxItems": 1}, [1, 2]),
        "unique_items": _rejects({"type": "array", "uniqueItems": True}, [{"a": 1}, {"a": 1}]),
        "pattern": _rejects({"type": "string", "pattern": r"^[a-z]+$"}, "ABC"),
        "format_email": _rejects({"type": "string", "format": "email"}, "not-email"),
        "format_uri": _rejects({"type": "string", "format": "uri"}, "not-uri"),
        "format_uuid": _rejects({"type": "string", "format": "uuid"}, "not-uuid"),
        "format_date_time": _rejects({"type": "string", "format": "date-time"}, "2026-99-99"),
        "exclusive_minimum": _rejects({"type": "number", "exclusiveMinimum": 5}, 5),
        "exclusive_maximum": _rejects({"type": "number", "exclusiveMaximum": 5}, 5),
        "multiple_of": _rejects({"type": "number", "multipleOf": 2}, 3),
        "const": _rejects({"const": "fixed"}, "other"),
        "min_properties": _rejects({"type": "object", "minProperties": 2}, {"a": 1}),
        "max_properties": _rejects({"type": "object", "maxProperties": 1}, {"a": 1, "b": 2}),
        "property_names": _rejects({"type": "object", "propertyNames": {"pattern": r"^[a-z_]+$"}}, {"Bad": 1}),
        "dependent_required": _rejects(
            {"type": "object", "dependentRequired": {"token": ["expires_at"]}},
            {"token": "abc"},
        ),
        "positive_valid_schema": _accepts(
            {
                "type": "object",
                "required": ["email", "ids"],
                "properties": {
                    "email": {"type": "string", "format": "email"},
                    "ids": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "integer"}},
                },
                "additionalProperties": False,
            },
            {"email": "user@example.com", "ids": [1, 2]},
        ),
    }
    failed = sorted(name for name, ok in checks.items() if not ok)
    return {
        "status": "success" if not failed else "error",
        "checks": checks,
        "failed_checks": failed,
        "generated_at": utc_now(),
    }


def _rejects(schema: dict[str, Any], value: Any) -> bool:
    try:
        validate_json_value(value, schema)
    except SchemaValidationError:
        return True
    return False


def _accepts(schema: dict[str, Any], value: Any) -> bool:
    try:
        validate_json_value(value, schema)
    except (SchemaValidationError, SchemaDefinitionError):
        return False
    return True
