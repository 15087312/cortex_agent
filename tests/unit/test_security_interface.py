"""modules/security_system/interface — 安全端口门面（单例 / 覆盖 / Protocol 运行时检查）"""
from unittest.mock import MagicMock, patch

import modules.security_system.interface as sec_face
from modules.security_system.interface import (
    SecurityPort,
    get_security_port,
    set_security_port,
)


def _reset_port():
    sec_face._security_port = None


def test_security_port_is_runtime_checkable_protocol():
    class Impl:
        def validate_input(self, user_input: str):
            return (True, user_input)
        def get_security_state(self):
            return {"enabled": True}

    assert isinstance(Impl(), SecurityPort)
    fake = object()
    assert isinstance(fake, SecurityPort) is False


def test_security_port_protocol_method_bodies():
    """未覆写方法的子类直接调用 → 执行 Protocol 的 ... 占位体（9-10 行）"""
    class Bare(SecurityPort):
        pass

    bare = Bare()
    assert bare.validate_input("x") is None
    assert bare.get_security_state() is None


def test_get_security_port_lazy_init():
    _reset_port()
    with patch("modules.security_system.api.get_security_api", return_value=MagicMock()) as m:
        port = get_security_port()
        assert port is not None
        assert sec_face._security_port is port
        m.assert_called_once()


def test_get_security_port_cached():
    port = MagicMock()
    sec_face._security_port = port
    assert get_security_port() is port


def test_set_security_port_override():
    new_port = MagicMock()
    set_security_port(new_port)
    assert get_security_port() is new_port


def test_set_security_port_none_reinitializes():
    _reset_port()
    with patch("modules.security_system.api.get_security_api", return_value=MagicMock()) as m:
        set_security_port(None)
        assert sec_face._security_port is not None
        m.assert_called_once()
