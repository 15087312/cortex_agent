from __future__ import annotations

import re
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
PLUGIN_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
PYTHON_REQ_PATTERN = re.compile(r"^(>=|<=|==|~=|>|<)\s*\d+\.\d+(?:\.\d+)?$")
ENTRY_PATTERN = re.compile(r"^[a-zA-Z_][\w.]*:[a-zA-Z_]\w*$")
PARAM_NAME_PATTERN = re.compile(r"^[a-zA-Z_]\w{0,63}$")


class PluginValidationError(ValueError):
    """Raised when a plugin package violates the plugin specification."""


class PluginToolArgumentError(ValueError):
    """Raised when a model supplied invalid arguments for a plugin tool."""


class ExtensionType(str, Enum):
    TOOL = "tool"
    MIDDLEWARE = "middleware"
    EVENT_LISTENER = "event_listener"
    MEMORY_PROVIDER = "memory_provider"


class RunMode(str, Enum):
    IN_PROCESS = "in_process"
    SUB_PROCESS = "sub_process"
    AUTO = "auto"


class TrustLevel(str, Enum):
    OFFICIAL = "official"
    TRUSTED = "trusted"
    THIRD_PARTY = "third_party"


class PluginStatus(str, Enum):
    DISCOVERED = "discovered"
    VERIFIED = "verified"
    INSTALLED = "installed"
    CONFIGURED = "configured"
    PENDING_APPROVAL = "pending_approval"
    PERMISSION_PENDING = "permission_pending"
    ENABLED = "enabled"
    RUNNING = "running"
    SUSPENDED = "suspended"
    DISABLED = "disabled"
    QUARANTINED = "quarantined"
    REVOKED = "revoked"
    UNINSTALLED = "uninstalled"


class PermissionName(str, Enum):
    COMPUTE = "compute"
    MEMORY_READ = "memory.read"
    CONFIG_READ = "config.read"
    NETWORK_OUTBOUND = "network.outbound"
    FS_READ = "fs.read"
    FS_WRITE = "fs.write"
    MEMORY_WRITE = "memory.write"
    OUTPUT_SEND = "output.send"


class ToolParamDecl(BaseModel):
    """Schema for a model-callable plugin tool parameter."""

    model_config = ConfigDict(populate_by_name=True)

    type: Literal["string", "number", "integer", "boolean", "array", "object"] = "string"
    description: str = ""
    required: bool = False
    enum: list[Any] | None = None
    items: dict[str, Any] | None = None
    properties: dict[str, Any] | None = None
    additional_properties: bool | dict[str, Any] = Field(default=True, alias="additionalProperties")
    max_length: int | None = Field(default=None, alias="maxLength", ge=0)
    min_length: int | None = Field(default=None, alias="minLength", ge=0)
    maximum: float | int | None = None
    minimum: float | int | None = None

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        value = " ".join(str(value).strip().split())
        if len(value) > 512:
            raise ValueError("tool parameter description must be at most 512 characters")
        return value

    @field_validator("enum")
    @classmethod
    def validate_enum(cls, value: list[Any] | None) -> list[Any] | None:
        if value is not None and not value:
            raise ValueError("tool parameter enum cannot be empty")
        return value

    @model_validator(mode="after")
    def validate_schema_shape(self) -> "ToolParamDecl":
        if self.items is not None and self.type != "array":
            raise ValueError("items is only supported for array parameters")
        if self.properties is not None and self.type != "object":
            raise ValueError("properties is only supported for object parameters")
        if self.additional_properties is not True and self.type != "object":
            raise ValueError("additionalProperties is only supported for object parameters")
        if (self.max_length is not None or self.min_length is not None) and self.type != "string":
            raise ValueError("maxLength and minLength are only supported for string parameters")
        if (self.maximum is not None or self.minimum is not None) and self.type not in {"number", "integer"}:
            raise ValueError("maximum and minimum are only supported for numeric parameters")
        return self

    def to_json_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": self.type}
        if self.description:
            schema["description"] = self.description
        if self.enum is not None:
            schema["enum"] = self.enum
        if self.items is not None:
            schema["items"] = self.items
        if self.properties is not None:
            schema["properties"] = self.properties
        if self.type == "object":
            schema["additionalProperties"] = self.additional_properties
        if self.max_length is not None:
            schema["maxLength"] = self.max_length
        if self.min_length is not None:
            schema["minLength"] = self.min_length
        if self.maximum is not None:
            schema["maximum"] = self.maximum
        if self.minimum is not None:
            schema["minimum"] = self.minimum
        return schema


PERMISSION_LEVELS: dict[PermissionName, str] = {
    PermissionName.COMPUTE: "L0",
    PermissionName.MEMORY_READ: "L1",
    PermissionName.CONFIG_READ: "L1",
    PermissionName.NETWORK_OUTBOUND: "L2",
    PermissionName.FS_READ: "L3",
    PermissionName.FS_WRITE: "L3",
    PermissionName.MEMORY_WRITE: "L4",
    PermissionName.OUTPUT_SEND: "L4",
}

PERMISSION_RISK_LABELS: dict[str, str] = {
    "L0": "low",
    "L1": "low",
    "L2": "medium",
    "L3": "high",
    "L4": "critical",
}

PERMISSION_DESCRIPTIONS: dict[PermissionName, str] = {
    PermissionName.COMPUTE: "Local computation only; no external system access.",
    PermissionName.MEMORY_READ: "Read memory records exposed by the host gateway.",
    PermissionName.CONFIG_READ: "Read approved plugin configuration values.",
    PermissionName.NETWORK_OUTBOUND: "Send outbound HTTP requests to approved targets.",
    PermissionName.FS_READ: "Read files from the plugin sandbox data directory.",
    PermissionName.FS_WRITE: "Write files inside the plugin sandbox data directory.",
    PermissionName.MEMORY_WRITE: "Modify memory records exposed by the host gateway.",
    PermissionName.OUTPUT_SEND: "Send output messages through host-controlled channels.",
}


def permission_risk(permission: PermissionName | str, value: Any = None) -> dict[str, Any]:
    permission_name = PermissionName(permission)
    level = PERMISSION_LEVELS[permission_name]
    return {
        "name": permission_name.value,
        "level": level,
        "risk": PERMISSION_RISK_LABELS[level],
        "description": PERMISSION_DESCRIPTIONS[permission_name],
        "value": value,
    }


def permission_risks(permissions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        permission_risk(next(iter(item.keys())), next(iter(item.values())))
        for item in permissions
    ]


def validate_permission_decls(value: list[dict[str, Any]], *, default_compute: bool) -> list[dict[str, Any]]:
    if not value:
        return [{"compute": True}] if default_compute else []
    allowed = {permission.value for permission in PermissionName}
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or len(item) != 1:
            raise ValueError("each permission must be a single-key mapping")
        key, permission_value = next(iter(item.items()))
        if key not in allowed:
            raise ValueError(f"unknown permission: {key}")
        normalized.append({key: permission_value})
    return normalized


DANGEROUS_IMPORTS = {
    "aiohttp",
    "builtins",
    "ctypes",
    "ftplib",
    "http",
    "httpx",
    "importlib",
    "inspect",
    "marshal",
    "multiprocessing",
    "os",
    "pathlib",
    "pickle",
    "pkgutil",
    "pty",
    "resource",
    "requests",
    "shutil",
    "signal",
    "site",
    "socket",
    "ssl",
    "subprocess",
    "sys",
    "urllib",
}

DANGEROUS_CALLS = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "input",
}


class ExtensionDecl(BaseModel):
    """A single extension point exported by a plugin."""

    type: ExtensionType
    entry: str | None = Field(default=None, description="Python entry point, for example src.main:run")
    events: list[str] = Field(default_factory=list)
    name: str | None = Field(default=None, description="Public tool or middleware name")
    description: str | None = Field(default=None, description="Model-facing tool or extension description")
    params: dict[str, ToolParamDecl] = Field(default_factory=dict)
    permissions: list[dict[str, Any]] = Field(default_factory=list)
    returns: dict[str, Any] | None = Field(default=None, description="Model-facing tool result JSON Schema")

    @model_validator(mode="after")
    def validate_shape(self) -> "ExtensionDecl":
        if self.type in {
            ExtensionType.TOOL,
            ExtensionType.MIDDLEWARE,
            ExtensionType.MEMORY_PROVIDER,
        }:
            if not self.entry:
                raise ValueError(f"{self.type.value} extension requires an entry")
            if not ENTRY_PATTERN.match(self.entry):
                raise ValueError(f"invalid entry point: {self.entry}")
        if self.type == ExtensionType.EVENT_LISTENER:
            if not self.events:
                raise ValueError("event_listener extension requires events")
            if not self.entry:
                raise ValueError("event_listener extension requires an entry")
            if not ENTRY_PATTERN.match(self.entry):
                raise ValueError(f"invalid entry point: {self.entry}")
        if self.params and self.type != ExtensionType.TOOL:
            raise ValueError("params are only supported for tool extensions")
        if self.permissions and self.type != ExtensionType.TOOL:
            raise ValueError("per-extension permissions are only supported for tool extensions")
        if self.returns is not None and self.type != ExtensionType.TOOL:
            raise ValueError("returns schema is only supported for tool extensions")
        return self

    @field_validator("events")
    @classmethod
    def validate_events(cls, value: list[str]) -> list[str]:
        for event in value:
            if not re.match(r"^[a-zA-Z0-9_.:-]{1,128}$", event):
                raise ValueError(f"invalid event name: {event}")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("extension name cannot be empty")
        if len(value) > 64:
            raise ValueError("extension name must be at most 64 characters")
        return value

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = " ".join(str(value).strip().split())
        if len(value) > 1024:
            raise ValueError("extension description must be at most 1024 characters")
        return value or None

    @field_validator("params")
    @classmethod
    def validate_params(cls, value: dict[str, ToolParamDecl]) -> dict[str, ToolParamDecl]:
        for name in value:
            if not PARAM_NAME_PATTERN.match(name):
                raise ValueError(f"invalid tool parameter name: {name}")
        return value

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return validate_permission_decls(value, default_compute=False)

    @field_validator("returns")
    @classmethod
    def validate_returns(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return value
        if not value:
            raise ValueError("returns schema cannot be empty")
        schema_type = value.get("type")
        if schema_type is not None and schema_type not in {
            "string",
            "number",
            "integer",
            "boolean",
            "array",
            "object",
            "null",
        }:
            raise ValueError(f"invalid returns schema type: {schema_type}")
        return value


class RequirementsDecl(BaseModel):
    python: str = Field(default=">=3.11")
    packages: list[str] = Field(default_factory=list)

    @field_validator("python")
    @classmethod
    def validate_python_requirement(cls, value: str) -> str:
        if not PYTHON_REQ_PATTERN.match(value.strip()):
            raise ValueError("python requirement must look like '>=3.11'")
        return value.strip()

    @field_validator("packages")
    @classmethod
    def validate_packages(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for package in value:
            package = package.strip()
            if not package:
                continue
            if any(token in package for token in [";", "&", "|", "`", "$", "\n", "\r"]):
                raise ValueError(f"unsafe package requirement: {package}")
            normalized.append(package)
        return normalized


class RuntimeDecl(BaseModel):
    mode: RunMode = RunMode.AUTO
    trust: TrustLevel = TrustLevel.THIRD_PARTY
    memory_mb: int = Field(default=256, ge=16, le=2048)
    timeout_seconds: float = Field(default=5.0, gt=0, le=120)
    cpu_seconds: int = Field(default=5, ge=1, le=120)
    max_concurrency: int = Field(default=1, ge=1, le=64)
    failure_threshold: int = Field(default=3, ge=1, le=100)
    disable_on_failure_threshold: bool = True


class PluginMetadata(BaseModel):
    name: str
    version: str
    description: str = Field(min_length=5)
    author: str
    license: str = "MIT"
    extensions: list[ExtensionDecl] = Field(default_factory=list)
    permissions: list[dict[str, Any]] = Field(default_factory=lambda: [{"compute": True}])
    requires: RequirementsDecl = Field(default_factory=RequirementsDecl)
    runtime: RuntimeDecl = Field(default_factory=RuntimeDecl)
    signature: str | None = Field(default=None, description="Detached package signature")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not PLUGIN_NAME_PATTERN.match(value):
            raise ValueError("plugin name must use lowercase letters, numbers, and underscores")
        return value

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not SEMVER_PATTERN.match(value):
            raise ValueError("version must be semantic version, for example 1.2.0")
        return value

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return validate_permission_decls(value, default_compute=True)

    @model_validator(mode="after")
    def validate_runtime_policy(self) -> "PluginMetadata":
        if self.runtime.trust == TrustLevel.OFFICIAL and self.runtime.mode == RunMode.SUB_PROCESS:
            return self
        if self.runtime.trust == TrustLevel.THIRD_PARTY and self.runtime.mode == RunMode.IN_PROCESS:
            raise ValueError("third-party plugins cannot force in_process runtime")
        requested = self.requested_permissions
        for extension in self.extensions:
            if extension.type != ExtensionType.TOOL or not extension.permissions:
                continue
            tool_name = extension.name or (extension.entry or "").rsplit(":", 1)[-1]
            tool_permissions = {next(iter(item.keys())) for item in extension.permissions}
            unexpected = sorted(tool_permissions - requested)
            if unexpected:
                raise ValueError(
                    f"tool {tool_name} cannot request permissions not declared by plugin: {unexpected}"
                )
        return self

    @property
    def requested_permissions(self) -> set[str]:
        return {next(iter(item.keys())) for item in self.permissions}

    @property
    def effective_run_mode(self) -> RunMode:
        if self.runtime.mode != RunMode.AUTO:
            return self.runtime.mode
        if self.runtime.trust in {TrustLevel.OFFICIAL, TrustLevel.TRUSTED}:
            return RunMode.IN_PROCESS
        return RunMode.SUB_PROCESS

    def has_permission(self, permission: PermissionName | str) -> bool:
        key = permission.value if isinstance(permission, PermissionName) else permission
        return key in self.requested_permissions

    def permission_value(self, permission: PermissionName | str, default: Any = None) -> Any:
        key = permission.value if isinstance(permission, PermissionName) else permission
        for item in self.permissions:
            if key in item:
                return item[key]
        return default

    def tool_entries(self) -> dict[str, str]:
        entries: dict[str, str] = {}
        for extension in self.extensions:
            if extension.type == ExtensionType.TOOL and extension.entry:
                tool_name = extension.name or extension.entry.rsplit(":", 1)[-1]
                entries[tool_name] = extension.entry
        return entries

    def tool_extension_specs(self) -> dict[str, ExtensionDecl]:
        entries: dict[str, ExtensionDecl] = {}
        for extension in self.extensions:
            if extension.type == ExtensionType.TOOL and extension.entry:
                tool_name = extension.name or extension.entry.rsplit(":", 1)[-1]
                entries[tool_name] = extension
        return entries

    def tool_permissions(self, tool_name: str) -> list[dict[str, Any]]:
        spec = self.tool_extension_specs().get(tool_name)
        if spec is None:
            return []
        return spec.permissions

    def tool_requested_permissions(self, tool_name: str) -> set[str]:
        return {next(iter(item.keys())) for item in self.tool_permissions(tool_name)}

    def tool_result_schema(self, tool_name: str) -> dict[str, Any] | None:
        spec = self.tool_extension_specs().get(tool_name)
        if spec is None:
            return None
        return spec.returns

    def validate_tool_args(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Validate model-supplied tool arguments before entering plugin code."""

        tool_specs = self.tool_extension_specs()
        if tool_name not in tool_specs:
            raise PluginToolArgumentError(f"tool is not declared by plugin: {tool_name}")
        if not isinstance(args, dict):
            raise PluginToolArgumentError("tool arguments must be an object")

        params = tool_specs[tool_name].params
        if not params:
            return dict(args)

        missing = [
            name
            for name, param in params.items()
            if param.required and (name not in args or args.get(name) in (None, ""))
        ]
        if missing:
            raise PluginToolArgumentError(f"missing required tool arguments: {', '.join(sorted(missing))}")

        unexpected = sorted(set(args) - set(params))
        if unexpected:
            raise PluginToolArgumentError(f"unexpected tool arguments: {', '.join(unexpected)}")

        for name, value in args.items():
            self._validate_tool_arg_value(tool_name, name, value, params[name])
        return dict(args)

    def _validate_tool_arg_value(
        self,
        tool_name: str,
        name: str,
        value: Any,
        param: ToolParamDecl,
    ) -> None:
        if value is None:
            if param.required:
                raise PluginToolArgumentError(f"{tool_name}.{name} is required")
            return
        if param.enum is not None and value not in param.enum:
            raise PluginToolArgumentError(f"{tool_name}.{name} must be one of {param.enum!r}")
        if param.type == "string" and not isinstance(value, str):
            raise PluginToolArgumentError(f"{tool_name}.{name} must be a string")
        if param.type == "string" and isinstance(value, str):
            if param.min_length is not None and len(value) < param.min_length:
                raise PluginToolArgumentError(f"{tool_name}.{name} length must be at least {param.min_length}")
            if param.max_length is not None and len(value) > param.max_length:
                raise PluginToolArgumentError(f"{tool_name}.{name} length must be at most {param.max_length}")
        if param.type == "boolean" and not isinstance(value, bool):
            raise PluginToolArgumentError(f"{tool_name}.{name} must be a boolean")
        if param.type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise PluginToolArgumentError(f"{tool_name}.{name} must be an integer")
        if param.type == "number" and (
            not isinstance(value, (int, float)) or isinstance(value, bool)
        ):
            raise PluginToolArgumentError(f"{tool_name}.{name} must be a number")
        if param.type in {"integer", "number"} and isinstance(value, (int, float)) and not isinstance(value, bool):
            if param.minimum is not None and value < param.minimum:
                raise PluginToolArgumentError(f"{tool_name}.{name} must be at least {param.minimum}")
            if param.maximum is not None and value > param.maximum:
                raise PluginToolArgumentError(f"{tool_name}.{name} must be at most {param.maximum}")
        if param.type == "array" and not isinstance(value, list):
            raise PluginToolArgumentError(f"{tool_name}.{name} must be an array")
        if param.type == "object" and not isinstance(value, dict):
            raise PluginToolArgumentError(f"{tool_name}.{name} must be an object")

    def event_listener_entries(self) -> dict[str, list[str]]:
        entries: dict[str, list[str]] = {}
        for extension in self.extensions:
            if extension.type == ExtensionType.EVENT_LISTENER and extension.entry:
                for event in extension.events:
                    entries.setdefault(event, []).append(extension.entry)
        return entries

    def middleware_entries(self) -> dict[str, str]:
        entries: dict[str, str] = {}
        for extension in self.extensions:
            if extension.type == ExtensionType.MIDDLEWARE and extension.entry:
                middleware_name = extension.name or extension.entry.rsplit(":", 1)[-1]
                entries[middleware_name] = extension.entry
        return entries

    def memory_provider_entries(self) -> dict[str, str]:
        entries: dict[str, str] = {}
        for extension in self.extensions:
            if extension.type == ExtensionType.MEMORY_PROVIDER and extension.entry:
                provider_name = extension.name or extension.entry.rsplit(":", 1)[-1]
                entries[provider_name] = extension.entry
        return entries


class InstalledPlugin(BaseModel):
    metadata: PluginMetadata
    path: str
    package_hash: str | None = None
    installed_at: str | None = None
    status: PluginStatus = PluginStatus.PENDING_APPROVAL
    granted_permissions: list[dict[str, Any]] = Field(default_factory=list)
    permission_review: dict[str, Any] = Field(default_factory=dict)

    @field_validator("granted_permissions")
    @classmethod
    def validate_granted_permissions(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return validate_permission_decls(value, default_compute=False)

    @property
    def granted_permission_names(self) -> set[str]:
        return {next(iter(item.keys())) for item in self.granted_permissions}

    def has_granted_permission(self, permission: PermissionName | str) -> bool:
        key = permission.value if isinstance(permission, PermissionName) else permission
        return key in self.granted_permission_names

    def granted_permission_value(self, permission: PermissionName | str, default: Any = None) -> Any:
        key = permission.value if isinstance(permission, PermissionName) else permission
        for item in self.granted_permissions:
            if key in item:
                return item[key]
        return default


def normalize_archive_path(path: str) -> str:
    """Return a safe POSIX archive path or raise PluginValidationError."""

    candidate = PurePosixPath(path.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        raise PluginValidationError(f"archive contains unsafe path: {path}")
    return str(candidate)
