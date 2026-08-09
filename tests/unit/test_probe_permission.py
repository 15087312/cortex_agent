"""ProbePermissionManager 补测（此前 42% 覆盖）：细粒度权限校验"""
from unittest.mock import MagicMock

from modules.thinking.probes.probe_permission import ProbePermissionManager


def _mgr():
    return ProbePermissionManager.__new__(ProbePermissionManager)


def _perms(**kw):
    p = MagicMock()
    p.can_start_probes = kw.get("can_start_probes", True)
    p.can_stop_probes = kw.get("can_stop_probes", True)
    p.can_write_memory = kw.get("can_write_memory", True)
    p.controllable_tiers = kw.get("controllable_tiers", [])
    p.can_control_tier = lambda tier: tier in p.controllable_tiers
    return p


def test_validate_start_no_identity(monkeypatch):
    m = _mgr()
    import modules.thinking.identity as ident_mod
    monkeypatch.setattr(ident_mod, "get_identities", lambda: {})
    err = m.validate_probe_start_with_permissions(_perms(controllable_tiers=["expert"]), "expert", "ghost", caller_tier="large")
    assert err is not None
    assert "未知" in err


def test_validate_start_can_not_start():
    m = _mgr()
    err = m.validate_probe_start_with_permissions(_perms(can_start_probes=False), "expert", "code_writer")
    assert "can_start_probes" in err


def test_validate_start_controllable_denied():
    m = _mgr()
    err = m.validate_probe_start_with_permissions(_perms(), "large", "orchestrator", caller_tier="expert")
    assert err is not None
    assert "不能控制" in err


def test_validate_start_fallback_denied(monkeypatch):
    m = _mgr()
    import modules.thinking.identity as ident_mod
    monkeypatch.setattr(ident_mod, "get_identities", lambda: {"orchestrator": {}})
    err = m.validate_probe_start_with_permissions(None, "large", "orchestrator", caller_tier="expert")
    assert "不能启动" in err


def test_validate_stop_denied():
    m = _mgr()
    err = m.validate_probe_stop_with_permissions(_perms(can_stop_probes=False), "expert")
    assert "can_stop_probes" in err


def test_validate_stop_fallback_denied():
    m = _mgr()
    err = m.validate_probe_stop_with_permissions(None, "large", caller_tier="expert")
    assert "不能停止" in err


def test_validate_stop_allowed():
    m = _mgr()
    assert m.validate_probe_stop_with_permissions(_perms(controllable_tiers=["large"]), "large", caller_tier="large") is None


def test_can_modify_memory_with_permissions():
    m = _mgr()
    assert m.can_modify_memory_with_permissions(_perms(controllable_tiers=["expert"]), "expert") is True
    assert m.can_modify_memory_with_permissions(_perms(controllable_tiers=["expert"]), "large") is False
    assert m.can_modify_memory_with_permissions(_perms(can_write_memory=False), "expert") is False
    # 回退：空 controllable_tiers 用硬编码
    assert m.can_modify_memory_with_permissions(_perms(), "expert", caller_tier="large") is True


def test_can_modify_memory_hardcoded():
    m = _mgr()
    assert m.can_modify_memory("large", "expert") is True
    assert m.can_modify_memory("expert", "large") is False
