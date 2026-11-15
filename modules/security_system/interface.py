# @link:block SecurityInterface_Guard = Guard
"""安全系统接口门面。"""
from __future__ import annotations

from typing import Dict, Tuple, Protocol, runtime_checkable


@runtime_checkable
class SecurityPort(Protocol):
    def validate_input(self, user_input: str) -> Tuple[bool, str]: ...
    def get_security_state(self) -> Dict[str, bool]: ...


_security_port: SecurityPort | None = None


def get_security_port() -> SecurityPort:
    """端口优先取默认安全端口对象：接管 ``security_port`` 端口 = 换掉全部调用点。

    取不到端口才回退内置单例（脚本、没跑过 bootstrap 的入口）。bootstrap 注册
    的 provider 必须是 ``_default_security_port`` 而不是本函数，否则端口→本函数→端口
    一调用就递归。
    """
    from infra.tool_manager.service_registry import get_capability

    factory = get_capability("security_port")
    if factory is None:
        return _default_security_port()
    return factory()


def _default_security_port() -> SecurityPort:
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
