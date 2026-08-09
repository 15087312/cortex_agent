"""management.core.interfaces 测试（此前 0% 覆盖）：状态端口"""
from unittest.mock import MagicMock, patch

from modules.management.core.interfaces import (
    get_perception_status_port,
    get_security_status_port,
    PerceptionStatusAdapter,
    SecurityStatusAdapter,
)


def test_perception_status_port(monkeypatch):
    ps = MagicMock()
    ps._started = True
    ps.voice_detector = MagicMock()
    ps.proactive_trigger = None
    ps.world_state = None
    import modules.perception as perception_mod
    monkeypatch.setattr(perception_mod, "get_perception_system", lambda: ps)
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
