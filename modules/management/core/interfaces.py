"""Management-facing module status ports."""
from __future__ import annotations

from typing import Any, Dict, Protocol, runtime_checkable


@runtime_checkable
class ModuleStatusPort(Protocol):
    def get_status(self) -> Dict[str, Any]:
        """Return module status for management collection."""


class PerceptionStatusAdapter:
    def get_status(self) -> Dict[str, Any]:
        from modules.perception.interface import get_perception_port
        from modules.perception.manager import PERCEPTION_PLATFORM, perception_manager

        context = perception_manager.get_full_context()
        perception = get_perception_port()
        return {
            "status": "healthy",
            "platform": PERCEPTION_PLATFORM,
            "watch_paths": perception_manager.watch_paths,
            "stats": context.get("stats", {}),
            "recent_changes_count": len(context.get("recent_changes", [])),
            "attention_pool_size": len(perception_manager.attention_pool),
            "monitoring": perception.is_running,
        }


class SecurityStatusAdapter:
    def get_status(self) -> Dict[str, Any]:
        from modules.security_system.interface import get_security_port

        return {
            "status": "healthy",
            "audit_enabled": True,
            "available": True,
            "state": get_security_port().get_security_state(),
        }


class PluginStatusAdapter:
    def get_status(self) -> Dict[str, Any]:
        return {
            "status": "healthy",
            "available": True,
        }


def get_perception_status_port() -> ModuleStatusPort:
    return PerceptionStatusAdapter()


def get_security_status_port() -> ModuleStatusPort:
    return SecurityStatusAdapter()


def get_plugin_status_port() -> ModuleStatusPort:
    return PluginStatusAdapter()


__all__ = [
    "ModuleStatusPort",
    "get_perception_status_port",
    "get_security_status_port",
    "get_plugin_status_port",
]
