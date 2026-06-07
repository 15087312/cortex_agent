from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from .models import PermissionName


SENSITIVE_GATEWAY_PERMISSIONS = frozenset(
    {
        PermissionName.MEMORY_READ.value,
        PermissionName.MEMORY_WRITE.value,
        PermissionName.CONFIG_READ.value,
        PermissionName.NETWORK_OUTBOUND.value,
        PermissionName.FS_READ.value,
        PermissionName.FS_WRITE.value,
        PermissionName.OUTPUT_SEND.value,
    }
)


def permission_for_gateway_request(request_type: str) -> PermissionName | None:
    """Return the permission guarded by a gateway request, if it is sensitive."""

    if request_type not in SENSITIVE_GATEWAY_PERMISSIONS:
        return None
    return PermissionName(request_type)


def permission_names_from_decls(decls: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> set[str]:
    names: set[str] = set()
    for item in decls:
        if isinstance(item, dict) and item:
            names.add(str(next(iter(item.keys()))))
    return names


@dataclass(frozen=True)
class ToolPermissionScope:
    """Active permissions for one model-triggered plugin tool request."""

    request_id: str
    plugin_id: str
    plugin_version: str
    tool_name: str
    plugin_permissions: frozenset[str] = field(default_factory=frozenset)
    plugin_granted_permissions: frozenset[str] = field(default_factory=frozenset)
    tool_permissions: frozenset[str] = field(default_factory=frozenset)
    policy_denied_permissions: frozenset[str] = field(default_factory=frozenset)
    effective_permissions: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def build(
        cls,
        *,
        request_id: str,
        plugin_id: str,
        plugin_version: str,
        tool_name: str,
        plugin_permissions: set[str],
        plugin_granted_permissions: set[str],
        tool_permissions: set[str],
        policy_denied_permissions: set[str] | None = None,
    ) -> "ToolPermissionScope":
        denied = set(policy_denied_permissions or set())
        effective = set(plugin_permissions) & set(plugin_granted_permissions) & set(tool_permissions)
        effective -= denied
        return cls(
            request_id=request_id,
            plugin_id=plugin_id,
            plugin_version=plugin_version,
            tool_name=tool_name,
            plugin_permissions=frozenset(plugin_permissions),
            plugin_granted_permissions=frozenset(plugin_granted_permissions),
            tool_permissions=frozenset(tool_permissions),
            policy_denied_permissions=frozenset(denied),
            effective_permissions=frozenset(effective),
        )

    def allows(self, permission: PermissionName | str) -> bool:
        key = permission.value if isinstance(permission, PermissionName) else str(permission)
        return key in self.effective_permissions

    def permission_status(self, permission: PermissionName | str) -> dict[str, bool]:
        key = permission.value if isinstance(permission, PermissionName) else str(permission)
        plugin_declared = key in self.plugin_permissions
        plugin_granted = key in self.plugin_granted_permissions
        tool_declared = key in self.tool_permissions
        policy_allowed = key not in self.policy_denied_permissions
        return {
            "plugin_declared": plugin_declared,
            "plugin_granted": plugin_granted,
            "plugin_permission_allowed": plugin_declared and plugin_granted,
            "tool_permission_allowed": tool_declared,
            "policy_permission_allowed": policy_allowed,
            "effective_permission_allowed": key in self.effective_permissions,
        }


class RequestPermissionRegistry:
    """Thread-safe registry of active tool request permission scopes."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._scopes: dict[str, ToolPermissionScope] = {}

    def register(self, scope: ToolPermissionScope) -> None:
        with self._lock:
            self._scopes[scope.request_id] = scope

    def unregister(self, request_id: str) -> None:
        with self._lock:
            self._scopes.pop(request_id, None)

    def get(self, request_id: str | None) -> ToolPermissionScope | None:
        if not request_id:
            return None
        with self._lock:
            return self._scopes.get(request_id)

    def active_count(self) -> int:
        with self._lock:
            return len(self._scopes)
