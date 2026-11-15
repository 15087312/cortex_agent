"""management.core.interfaces 测试（此前 0% 覆盖）：状态端口"""
from unittest.mock import MagicMock, patch

from modules.management.core.interfaces import (
    get_perception_status_port,
    get_security_status_port,
    PerceptionStatusAdapter,
    SecurityStatusAdapter,
)


def test_perception_status_port(take_over_port):
    ps = MagicMock()
    ps._started = True
    ps.voice_detector = MagicMock()
    ps.world_state = None
    # 桩打在端口上：本适配器就是经端口取感知系统的，打访问器名只会悄悄拿到内置那套
    take_over_port("perception.system", ps)
    status = PerceptionStatusAdapter().get_status()
    assert status["started"] is True
    assert status["voice_detector"] is True


def test_security_status_port(monkeypatch):
    fake_port = MagicMock()
    fake_port.get_security_state.return_value = {"enabled": True}
    import modules.security_system.interface as sec_iface
    monkeypatch.setattr(sec_iface, "get_security_port", lambda: fake_port)
    status = SecurityStatusAdapter().get_status()
    assert status["state"] == {"enabled": True}


def test_singletons():
    assert isinstance(get_perception_status_port(), PerceptionStatusAdapter)
    assert isinstance(get_security_status_port(), SecurityStatusAdapter)
