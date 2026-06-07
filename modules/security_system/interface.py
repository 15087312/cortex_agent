"""Security system interface facade."""
from __future__ import annotations

from typing import Dict, Tuple, Protocol, runtime_checkable


@runtime_checkable
class SecurityPort(Protocol):
    def validate_input(self, user_input: str) -> Tuple[bool, str]: ...
    def validate_output(self, output_content: str) -> Tuple[bool, str]: ...
    def validate_module_call(self, caller: str, target: str) -> Tuple[bool, str]: ...
    def get_security_state(self) -> Dict[str, bool]: ...


class SecurityApiAdapter:
    """Adapter around the concrete SecurityAPI facade."""

    def __init__(self):
        from modules.security_system.api import SecurityAPI

        self._api = SecurityAPI()

    def validate_input(self, user_input: str) -> Tuple[bool, str]:
        return self._api.validate_input(user_input)

    def validate_output(self, output_content: str) -> Tuple[bool, str]:
        return self._api.validate_output(output_content)

    def validate_module_call(self, caller: str, target: str) -> Tuple[bool, str]:
        return self._api.validate_module_call(caller, target)

    def get_security_state(self) -> Dict[str, bool]:
        return self._api.get_security_state()


_security_port: SecurityPort | None = None


def get_security_port() -> SecurityPort:
    """Return the default security port."""
    global _security_port
    if _security_port is None:
        _security_port = SecurityApiAdapter()
    return _security_port


def set_security_port(port: SecurityPort | None) -> None:
    """Override the security port, primarily for integration/tests."""
    global _security_port
    _security_port = port or SecurityApiAdapter()


__all__ = ["SecurityPort", "get_security_port", "set_security_port"]
