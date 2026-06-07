from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


class ToolErrorCategory(str):
    INPUT_SCHEMA = "input_schema"
    VISIBILITY_AUTHORIZATION = "visibility_authorization"
    GOVERNANCE = "governance"
    RUNTIME = "runtime"
    SYSTEM = "system"


@dataclass(frozen=True)
class ToolErrorInfo:
    code: str
    safe_message: str
    retryable: bool
    category: str
    expose_to_model: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


TOOL_ERROR_DEFINITIONS: dict[str, ToolErrorInfo] = {
    "INVALID_ARGUMENT_JSON": ToolErrorInfo(
        "INVALID_ARGUMENT_JSON",
        "Tool arguments are not valid JSON.",
        False,
        ToolErrorCategory.INPUT_SCHEMA,
    ),
    "PARAM_SCHEMA_ERROR": ToolErrorInfo(
        "PARAM_SCHEMA_ERROR",
        "Tool arguments do not match the declared schema.",
        False,
        ToolErrorCategory.INPUT_SCHEMA,
    ),
    "ARGUMENTS_TOO_LARGE": ToolErrorInfo(
        "ARGUMENTS_TOO_LARGE",
        "Tool arguments exceed the provider bridge limit.",
        False,
        ToolErrorCategory.INPUT_SCHEMA,
    ),
    "MULTIPLE_TOOL_CALLS_UNSUPPORTED": ToolErrorInfo(
        "MULTIPLE_TOOL_CALLS_UNSUPPORTED",
        "This entry point accepts one tool call; use the batch parser for multiple calls.",
        False,
        ToolErrorCategory.INPUT_SCHEMA,
    ),
    "TOOL_CALL_PAYLOAD_UNSUPPORTED": ToolErrorInfo(
        "TOOL_CALL_PAYLOAD_UNSUPPORTED",
        "Provider tool call payload format is not supported.",
        False,
        ToolErrorCategory.INPUT_SCHEMA,
    ),
    "TOOL_CALL_MISSING_NAME": ToolErrorInfo(
        "TOOL_CALL_MISSING_NAME",
        "Tool call is missing a tool name.",
        False,
        ToolErrorCategory.INPUT_SCHEMA,
    ),
    "TOOL_CALL_MISSING_ARGUMENTS": ToolErrorInfo(
        "TOOL_CALL_MISSING_ARGUMENTS",
        "Tool call is missing arguments.",
        False,
        ToolErrorCategory.INPUT_SCHEMA,
    ),
    "TOOL_NOT_FOUND": ToolErrorInfo(
        "TOOL_NOT_FOUND",
        "Tool is not exported for this caller.",
        False,
        ToolErrorCategory.VISIBILITY_AUTHORIZATION,
    ),
    "TOOL_NOT_VISIBLE": ToolErrorInfo(
        "TOOL_NOT_VISIBLE",
        "Tool is not available to this caller.",
        False,
        ToolErrorCategory.VISIBILITY_AUTHORIZATION,
    ),
    "PERMISSION_DENIED": ToolErrorInfo(
        "PERMISSION_DENIED",
        "Tool permission was denied.",
        False,
        ToolErrorCategory.VISIBILITY_AUTHORIZATION,
    ),
    "APPROVAL_REQUIRED": ToolErrorInfo(
        "APPROVAL_REQUIRED",
        "Tool approval is required before execution.",
        False,
        ToolErrorCategory.VISIBILITY_AUTHORIZATION,
    ),
    "CONFIRMATION_REQUIRED": ToolErrorInfo(
        "CONFIRMATION_REQUIRED",
        "This tool requires confirmation before execution.",
        False,
        ToolErrorCategory.GOVERNANCE,
    ),
    "CONFIRMATION_INVALID": ToolErrorInfo(
        "CONFIRMATION_INVALID",
        "Tool confirmation is invalid for this request.",
        False,
        ToolErrorCategory.GOVERNANCE,
    ),
    "CONFIRMATION_EXPIRED": ToolErrorInfo(
        "CONFIRMATION_EXPIRED",
        "Tool confirmation has expired.",
        True,
        ToolErrorCategory.GOVERNANCE,
    ),
    "CONFIRMATION_NOT_REQUIRED": ToolErrorInfo(
        "CONFIRMATION_NOT_REQUIRED",
        "This tool does not require confirmation.",
        False,
        ToolErrorCategory.GOVERNANCE,
    ),
    "BUDGET_EXCEEDED": ToolErrorInfo(
        "BUDGET_EXCEEDED",
        "Tool call budget has been exceeded.",
        False,
        ToolErrorCategory.GOVERNANCE,
    ),
    "RATE_LIMITED": ToolErrorInfo(
        "RATE_LIMITED",
        "Tool call rate limit has been exceeded.",
        True,
        ToolErrorCategory.GOVERNANCE,
    ),
    "DUPLICATE_TOOL_CALL": ToolErrorInfo(
        "DUPLICATE_TOOL_CALL",
        "This tool call was already completed.",
        False,
        ToolErrorCategory.GOVERNANCE,
    ),
    "DUPLICATE_IN_PROGRESS": ToolErrorInfo(
        "DUPLICATE_IN_PROGRESS",
        "An equivalent tool call is already in progress.",
        True,
        ToolErrorCategory.GOVERNANCE,
    ),
    "IDEMPOTENCY_CONFLICT": ToolErrorInfo(
        "IDEMPOTENCY_CONFLICT",
        "Idempotency key does not match this tool call.",
        False,
        ToolErrorCategory.GOVERNANCE,
    ),
    "DRY_RUN_ONLY": ToolErrorInfo(
        "DRY_RUN_ONLY",
        "Tool call was evaluated without executing the plugin.",
        False,
        ToolErrorCategory.GOVERNANCE,
    ),
    "TOOL_LOOP_DETECTED": ToolErrorInfo(
        "TOOL_LOOP_DETECTED",
        "Repeated equivalent tool calls were detected.",
        False,
        ToolErrorCategory.GOVERNANCE,
    ),
    "TOOL_STORM_RATE_LIMITED": ToolErrorInfo(
        "TOOL_STORM_RATE_LIMITED",
        "Tool call storm protection rate-limited this session.",
        True,
        ToolErrorCategory.GOVERNANCE,
    ),
    "TOOL_TIMEOUT": ToolErrorInfo(
        "TOOL_TIMEOUT",
        "Tool execution timed out.",
        True,
        ToolErrorCategory.RUNTIME,
    ),
    "TIMEOUT": ToolErrorInfo(
        "TIMEOUT",
        "Tool execution timed out.",
        True,
        ToolErrorCategory.RUNTIME,
    ),
    "TOOL_EXECUTION_FAILED": ToolErrorInfo(
        "TOOL_EXECUTION_FAILED",
        "Tool execution failed.",
        False,
        ToolErrorCategory.RUNTIME,
    ),
    "RETURN_SCHEMA_ERROR": ToolErrorInfo(
        "RETURN_SCHEMA_ERROR",
        "Tool returned data that does not match its schema.",
        False,
        ToolErrorCategory.RUNTIME,
    ),
    "SANITIZED_REJECTED": ToolErrorInfo(
        "SANITIZED_REJECTED",
        "Tool result could not be safely returned.",
        False,
        ToolErrorCategory.RUNTIME,
    ),
    "RESULT_TOO_LARGE": ToolErrorInfo(
        "RESULT_TOO_LARGE",
        "Tool result is too large to safely return.",
        False,
        ToolErrorCategory.RUNTIME,
    ),
    "INTERNAL_ERROR": ToolErrorInfo(
        "INTERNAL_ERROR",
        "Tool call failed.",
        False,
        ToolErrorCategory.SYSTEM,
        expose_to_model=False,
    ),
    "SERVICE_UNAVAILABLE": ToolErrorInfo(
        "SERVICE_UNAVAILABLE",
        "Plugin tool service is unavailable.",
        True,
        ToolErrorCategory.SYSTEM,
        expose_to_model=False,
    ),
    "AUDIT_UNAVAILABLE": ToolErrorInfo(
        "AUDIT_UNAVAILABLE",
        "Tool audit summary is unavailable.",
        False,
        ToolErrorCategory.SYSTEM,
        expose_to_model=False,
    ),
    "SANDBOX_UNAVAILABLE": ToolErrorInfo(
        "SANDBOX_UNAVAILABLE",
        "Plugin sandbox is unavailable.",
        True,
        ToolErrorCategory.SYSTEM,
        expose_to_model=False,
    ),
}


def normalize_tool_error_code(code: str | None) -> str:
    normalized = str(code or "INTERNAL_ERROR").upper()
    if normalized == "TIMEOUT":
        return "TOOL_TIMEOUT"
    if normalized in TOOL_ERROR_DEFINITIONS:
        return normalized
    return "INTERNAL_ERROR"


def tool_error_info(code: str | None) -> ToolErrorInfo:
    normalized = normalize_tool_error_code(code)
    return TOOL_ERROR_DEFINITIONS.get(normalized, TOOL_ERROR_DEFINITIONS["INTERNAL_ERROR"])


def safe_tool_error_message(code: str | None) -> str:
    return tool_error_info(code).safe_message


def tool_error_payload(code: str | None, *, message: str | None = None) -> dict[str, Any]:
    info = tool_error_info(code)
    return {
        "code": info.code,
        "message": message if info.expose_to_model and message else info.safe_message,
        "retryable": info.retryable,
        "category": info.category,
    }


def tool_error_catalog() -> dict[str, dict[str, Any]]:
    return {code: info.to_dict() for code, info in sorted(TOOL_ERROR_DEFINITIONS.items())}
