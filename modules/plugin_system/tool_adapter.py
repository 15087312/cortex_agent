from __future__ import annotations

import hashlib
import re
from types import MethodType
from typing import Any, Callable

from .models import InstalledPlugin, PermissionName, PluginMetadata, PluginStatus, ToolParamDecl, TrustLevel


MAX_REGISTRY_TOOL_NAME_LENGTH = 64


def registered_tool_name(plugin_name: str, tool_name: str) -> str:
    """Return the project ToolRegistry name for a plugin tool."""
    safe_plugin = _safe_tool_name_part(plugin_name)
    safe_tool = _safe_tool_name_part(tool_name)
    normalized = f"{safe_plugin}__{safe_tool}"
    raw_name = f"{plugin_name}__{tool_name}"
    if normalized == raw_name and len(normalized) <= MAX_REGISTRY_TOOL_NAME_LENGTH:
        return raw_name
    digest = hashlib.sha1(raw_name.encode("utf-8")).hexdigest()[:8]
    prefix_length = MAX_REGISTRY_TOOL_NAME_LENGTH - len(digest) - 1
    return f"{normalized[:prefix_length].rstrip('_')}_{digest}"


def _safe_tool_name_part(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]", "_", value).strip("_")
    return re.sub(r"_+", "_", normalized) or "tool"


def plugin_tool_registry_entries(metadata: PluginMetadata) -> list[dict[str, Any]]:
    tool_specs = metadata.tool_extension_specs()
    entries: list[dict[str, Any]] = []
    for tool_name, entry in sorted(metadata.tool_entries().items()):
        tool_spec = tool_specs[tool_name]
        item = {
            "plugin_name": metadata.name,
            "tool_name": tool_name,
            "registry_name": registered_tool_name(metadata.name, tool_name),
            "entry": entry,
            "description": tool_spec.description or metadata.description,
            "params": tool_params_for_api(tool_spec.params),
            "source": "plugin",
        }
        if tool_spec.returns is not None:
            item["returns"] = tool_spec.returns
        entries.append(item)
    return entries


def tool_params_for_api(params: dict[str, ToolParamDecl]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, param in sorted(params.items()):
        schema = param.to_json_schema()
        schema["required"] = param.required
        result[name] = schema
    return result


def tool_parameters_json_schema(params: dict[str, ToolParamDecl]) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            name: param.to_json_schema()
            for name, param in sorted(params.items())
        },
        "additionalProperties": False,
    }
    required = sorted(name for name, param in params.items() if param.required)
    if required:
        schema["required"] = required
    return schema


def plugin_tool_policy(metadata: PluginMetadata, tool_name: str | None = None) -> tuple[str, str]:
    requested = metadata.tool_requested_permissions(tool_name) if tool_name else metadata.requested_permissions
    if tool_name and not requested:
        requested = metadata.requested_permissions
    if PermissionName.MEMORY_WRITE.value in requested:
        return "HIGH", "mutation"
    if PermissionName.FS_WRITE.value in requested or PermissionName.OUTPUT_SEND.value in requested:
        return "HIGH", "mutation"
    if PermissionName.NETWORK_OUTBOUND.value in requested:
        return "MEDIUM", "query"
    if (
        PermissionName.MEMORY_READ.value in requested
        or PermissionName.CONFIG_READ.value in requested
        or PermissionName.FS_READ.value in requested
    ):
        return "MEDIUM", "query"
    return "LOW", "query"


def plugin_tool_visible_to_model(
    installed: InstalledPlugin,
    *,
    caller_role: str = "expert",
    allowed_plugins: set[str] | list[str] | tuple[str, ...] | None = None,
    allowed_tools: set[str] | list[str] | tuple[str, ...] | None = None,
    allow_high_risk_tools: bool = False,
    allow_third_party_tools: bool = True,
) -> bool:
    """Return whether a plugin's tools should be visible to a model caller."""

    if installed.status != PluginStatus.ENABLED:
        return False
    metadata = installed.metadata
    if not metadata.tool_entries():
        return False
    if allowed_plugins is not None and metadata.name not in set(allowed_plugins):
        return False
    if not allow_third_party_tools and metadata.runtime.trust == TrustLevel.THIRD_PARTY:
        return False
    if allowed_tools is None:
        candidate_tools = set(metadata.tool_entries())
    else:
        allowed_tool_names = set(allowed_tools)
        candidate_tools = {
            tool_name
            for tool_name in metadata.tool_entries()
            if registered_tool_name(metadata.name, tool_name) in allowed_tool_names
        }
    return any(
        plugin_tool_name_visible_to_model(
            installed,
            tool_name,
            caller_role=caller_role,
            allow_high_risk_tools=allow_high_risk_tools,
        )
        for tool_name in candidate_tools
    )


def plugin_tool_name_visible_to_model(
    installed: InstalledPlugin,
    tool_name: str,
    *,
    caller_role: str = "expert",
    allow_high_risk_tools: bool = False,
) -> bool:
    if installed.status != PluginStatus.ENABLED:
        return False
    if tool_name not in installed.metadata.tool_entries():
        return False
    risk_level, category = plugin_tool_policy(installed.metadata, tool_name)
    role = (caller_role or "").lower()
    if category in {"mutation", "admin"} and role.startswith("expert"):
        return False
    if risk_level in {"HIGH", "CRITICAL"} and not allow_high_risk_tools:
        return False
    return True


def plugin_model_tool_whitelist(
    installed_plugins: list[InstalledPlugin] | tuple[InstalledPlugin, ...],
    *,
    caller_role: str = "expert",
    allowed_plugins: set[str] | list[str] | tuple[str, ...] | None = None,
    allowed_tools: set[str] | list[str] | tuple[str, ...] | None = None,
    allow_high_risk_tools: bool = False,
    allow_third_party_tools: bool = True,
) -> list[str]:
    """Build ToolRegistry names for plugin tools visible to a model caller."""

    whitelist: list[str] = []
    allowed_tool_set = set(allowed_tools) if allowed_tools is not None else None
    for installed in installed_plugins:
        if not plugin_tool_visible_to_model(
            installed,
            caller_role=caller_role,
            allowed_plugins=allowed_plugins,
            allow_high_risk_tools=allow_high_risk_tools,
            allow_third_party_tools=allow_third_party_tools,
        ):
            continue
        for tool_name in sorted(installed.metadata.tool_entries()):
            if not plugin_tool_name_visible_to_model(
                installed,
                tool_name,
                caller_role=caller_role,
                allow_high_risk_tools=allow_high_risk_tools,
            ):
                continue
            registry_name = registered_tool_name(installed.metadata.name, tool_name)
            if allowed_tool_set is not None and registry_name not in allowed_tool_set:
                continue
            whitelist.append(registry_name)
    return whitelist


class PluginToolRegistryBridge:
    """Expose PluginEngine tools through the project ToolRegistry."""

    def __init__(self, engine: Any):
        self.engine = engine
        self._registered_by_plugin: dict[str, list[str]] = {}

    def sync_enabled_plugins(self) -> dict[str, list[str]]:
        synced: dict[str, list[str]] = {}
        seen_plugins: set[str] = set()
        for installed in list(self.engine.loader.installed_plugins.values()):
            seen_plugins.add(installed.metadata.name)
            if installed.status == PluginStatus.ENABLED:
                synced[installed.metadata.name] = self.register_plugin(installed)
            else:
                self.unregister_plugin(installed.metadata.name)
        for plugin_name in sorted(set(self._registered_by_plugin) - seen_plugins):
            self.unregister_plugin(plugin_name)
        return synced

    def register_plugin(self, installed: InstalledPlugin) -> list[str]:
        metadata = installed.metadata
        if installed.status != PluginStatus.ENABLED:
            return self.unregister_plugin(metadata.name)
        tool_entries = metadata.tool_entries()
        if not tool_entries:
            return self.unregister_plugin(metadata.name)

        registry = self._tool_registry()
        if registry is None:
            return []

        self.unregister_plugin(metadata.name)
        tool_specs = metadata.tool_extension_specs()
        registered_names: list[str] = []
        for tool_name in sorted(tool_entries):
            tool_spec = tool_specs[tool_name]
            registry_name = registered_tool_name(metadata.name, tool_name)
            risk_level, category = plugin_tool_policy(metadata, tool_name)
            registry.register_tool(
                name=registry_name,
                func=self._make_tool_callable(metadata.name, tool_name),
                description=self._tool_description(metadata, tool_name, tool_spec.description),
                params=self._tool_params_for_registry(tool_spec.params),
                source="plugin",
                plugin_name=metadata.name,
                risk_level=risk_level,
                category=category,
            )
            self._install_json_schema_override(registry, registry_name, tool_spec.params)
            registered_names.append(registry_name)
        self._registered_by_plugin[metadata.name] = registered_names
        return registered_names

    def unregister_plugin(self, plugin_name: str) -> list[str]:
        registry = self._tool_registry()
        registered_names = self._registered_by_plugin.pop(plugin_name, [])
        if registry is None:
            return registered_names
        if hasattr(registry, "unregister_by_plugin"):
            registry.unregister_by_plugin(plugin_name)
            return registered_names
        unregister = getattr(registry, "unregister", None)
        if callable(unregister):
            for name in registered_names:
                unregister(name)
        return registered_names

    def registered_tool_names(self, metadata: PluginMetadata) -> list[str]:
        return [entry["registry_name"] for entry in plugin_tool_registry_entries(metadata)]

    def _make_tool_callable(self, plugin_name: str, tool_name: str) -> Callable[..., Any]:
        def call_plugin_tool(**params: Any) -> Any:
            result = self.engine.call_tool(plugin_name, tool_name, dict(params))
            if isinstance(result, dict) and result.get("status") == "success":
                return result.get("data")
            if isinstance(result, dict):
                raise RuntimeError(str(result.get("error") or "plugin tool failed"))
            raise RuntimeError("plugin tool returned an invalid response")

        call_plugin_tool.__name__ = registered_tool_name(plugin_name, tool_name)
        return call_plugin_tool

    def _tool_description(self, metadata: PluginMetadata, tool_name: str, tool_description: str | None) -> str:
        description = (tool_description or metadata.description).strip()
        if description:
            return f"{description} (plugin: {metadata.name}, tool: {tool_name})"
        return f"Plugin tool {metadata.name}.{tool_name}"

    def _tool_params_for_registry(self, params: dict[str, ToolParamDecl]) -> dict[str, Any]:
        try:
            from infra.tool_manager.tool_registry import ParamSchema
        except Exception:
            return {name: param.description for name, param in params.items()}
        return {
            name: ParamSchema(
                description=param.description,
                type=param.type,
                required=param.required,
            )
            for name, param in sorted(params.items())
        }

    def _install_json_schema_override(
        self,
        registry: Any,
        registry_name: str,
        params: dict[str, ToolParamDecl],
    ) -> None:
        get_tool = getattr(registry, "get_tool", None)
        if not callable(get_tool):
            return
        tool_info = get_tool(registry_name)
        if tool_info is None:
            return

        def to_json_schema(_: Any) -> dict[str, Any]:
            return tool_parameters_json_schema(params)

        try:
            tool_info.to_json_schema = MethodType(to_json_schema, tool_info)
        except Exception:
            pass

    def _tool_registry(self) -> Any | None:
        try:
            from infra.tool_manager.tool_registry import ToolRegistry
        except Exception:
            return None
        return ToolRegistry
