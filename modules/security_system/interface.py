"""安全系统接口门面。"""
from __future__ import annotations

from typing import Dict, Tuple, Protocol, runtime_checkable


@runtime_checkable
class SecurityPort(Protocol):
    def validate_input(self, user_input: str) -> Tuple[bool, str]: ...
    def get_security_state(self) -> Dict[str, bool]: ...


_security_port: SecurityPort | None = None


def get_security_port() -> SecurityPort:
    """返回默认安全端口。"""
    global _security_port
    if _security_port is None:
        from modules.security_system.api import get_security_api
        _security_port = get_security_api()  # type: ignore[assignment]
    return _security_port


def set_security_port(port: SecurityPort | None) -> None:
    """覆盖安全端口，主要用于集成测试。"""
    global _security_port
    _security_port = port
    if _security_port is None:
        from modules.security_system.api import get_security_api
        _security_port = get_security_api()  # type: ignore[assignment]


__all__ = ["SecurityPort", "get_security_port", "set_security_port"]
