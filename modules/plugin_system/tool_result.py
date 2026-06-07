from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


MAX_TOOL_RESULT_BYTES = 256 * 1024
MAX_TOOL_RESULT_STRING_CHARS = 4096
REDACTED_VALUE = "[REDACTED]"
SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "credential",
    "password",
    "secret",
    "token",
)


class PluginToolResultError(ValueError):
    """Raised when a plugin tool result cannot be safely returned to a model."""


@dataclass(frozen=True)
class ToolResultSanitizationReport:
    sanitized_fields: list[str] = field(default_factory=list)
    truncated_fields: list[str] = field(default_factory=list)
    original_size_bytes: int | None = None
    final_size_bytes: int | None = None

    @property
    def changed(self) -> bool:
        return bool(self.sanitized_fields or self.truncated_fields)


def sanitize_tool_result(
    value: Any,
    *,
    max_bytes: int = MAX_TOOL_RESULT_BYTES,
    max_string_chars: int = MAX_TOOL_RESULT_STRING_CHARS,
) -> Any:
    """Return a model-safe JSON value with sensitive fields redacted."""

    sanitized, _report = sanitize_tool_result_with_report(
        value,
        max_bytes=max_bytes,
        max_string_chars=max_string_chars,
    )
    return sanitized


def sanitize_tool_result_with_report(
    value: Any,
    *,
    max_bytes: int = MAX_TOOL_RESULT_BYTES,
    max_string_chars: int = MAX_TOOL_RESULT_STRING_CHARS,
) -> tuple[Any, ToolResultSanitizationReport]:
    """Return a model-safe JSON value and metadata for audit."""

    sanitized_fields: list[str] = []
    truncated_fields: list[str] = []
    original_size = _json_size(value)
    sanitized = _sanitize_value(
        value,
        max_string_chars=max_string_chars,
        path="$",
        sanitized_fields=sanitized_fields,
        truncated_fields=truncated_fields,
    )
    try:
        encoded = json.dumps(sanitized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PluginToolResultError(f"tool result is not JSON serializable: {exc}") from exc
    if len(encoded) > max_bytes:
        raise PluginToolResultError(f"tool result exceeds {max_bytes} bytes")
    return sanitized, ToolResultSanitizationReport(
        sanitized_fields=sanitized_fields,
        truncated_fields=truncated_fields,
        original_size_bytes=original_size,
        final_size_bytes=len(encoded),
    )


def _sanitize_value(
    value: Any,
    *,
    max_string_chars: int,
    path: str,
    sanitized_fields: list[str],
    truncated_fields: list[str],
) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) <= max_string_chars:
            return value
        truncated_fields.append(path)
        return value[:max_string_chars] + "...[truncated]"
    if isinstance(value, list):
        return [
            _sanitize_value(
                item,
                max_string_chars=max_string_chars,
                path=f"{path}[{index}]",
                sanitized_fields=sanitized_fields,
                truncated_fields=truncated_fields,
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, tuple):
        return [
            _sanitize_value(
                item,
                max_string_chars=max_string_chars,
                path=f"{path}[{index}]",
                sanitized_fields=sanitized_fields,
                truncated_fields=truncated_fields,
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            child_path = _child_path(path, key_text)
            if _is_sensitive_key(key_text):
                result[key_text] = REDACTED_VALUE
                sanitized_fields.append(child_path)
            else:
                result[key_text] = _sanitize_value(
                    item,
                    max_string_chars=max_string_chars,
                    path=child_path,
                    sanitized_fields=sanitized_fields,
                    truncated_fields=truncated_fields,
                )
        return result
    raise PluginToolResultError(f"tool result contains unsupported value type: {type(value).__name__}")


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _child_path(parent: str, key: str) -> str:
    if key.replace("_", "").isalnum():
        return f"{parent}.{key}"
    return f"{parent}[{key!r}]"


def _json_size(value: Any) -> int | None:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return None
