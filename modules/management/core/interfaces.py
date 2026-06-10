"""Management-facing module status ports."""
from __future__ import annotations

from typing import Any, Dict, Protocol, runtime_checkable


@runtime_checkable
class ModuleStatusPort(Protocol):
    def get_status(self) -> Dict[str, Any]:
        """Return module status for management collection."""


class PerceptionStatusAdapter:
    def get_status(self) -> Dict[str, Any]:
        import platform
        from modules.perception.interface import get_perception_port
        from modules.perception import get_perception_system

        ps = get_perception_system()
        perception = get_perception_port()
        return {
            "status": "healthy",
            "platform": platform.system(),
            "started": ps._started,
            "pipeline": ps.pipeline.get_stats() if ps.pipeline else None,
            "voice_available": ps.voice_detector is not None,
            "file_perception": ps.file_perception is not None,
            "dialog_perception": ps.dialog_perception is not None,
            "monitoring": ps._started,
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
