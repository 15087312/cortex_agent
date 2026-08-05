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
        from modules.perception import get_perception_system

        ps = get_perception_system()
        return {
            "status": "healthy",
            "platform": platform.system(),
            "started": ps._started,
            "voice_detector": ps.voice_detector is not None,
            "proactive_trigger": ps.proactive_trigger is not None,
            "world_state": ps.world_state is not None,
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


def get_perception_status_port() -> ModuleStatusPort:
    return PerceptionStatusAdapter()


def get_security_status_port() -> ModuleStatusPort:
    return SecurityStatusAdapter()


__all__ = [
    "ModuleStatusPort",
    "get_perception_status_port",
    "get_security_status_port",
]
