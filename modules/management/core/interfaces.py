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

        from infra.ports import resolve

        ps = resolve("perception.system")
        if ps is None:
            # 感知模块未加载：如实报告，不去 import 它（那会形成 management → perception 实依赖）
            return {"status": "unknown", "reason": "perception 未加载"}
        return {
            "status": "healthy",
            "platform": platform.system(),
            "started": ps._started,
            "voice_detector": ps.voice_detector is not None,
            "world_state": ps.world_state is not None,
        }


class SecurityStatusAdapter:
    def get_status(self) -> Dict[str, Any]:
        from infra.ports import resolve

        port = resolve("security.port")
        if port is None:
            return {"status": "unknown", "reason": "security_system 未加载"}
        return {
            "status": "healthy",
            "audit_enabled": True,
            "available": True,
            "state": port.get_security_state(),
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
