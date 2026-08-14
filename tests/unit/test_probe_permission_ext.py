"""probe_permission 扩展测试：tier 规范化 / 基础校验 / 单例"""
from modules.thinking.probes.probe_permission import (
    ProbePermissionManager,
    get_probe_permission_manager,
    _permission_manager,
)


def _mgr():
    return ProbePermissionManager()


def test_get_tier_level():
    m = _mgr()
    assert m.get_tier_level("large") == 3
    assert m.get_tier_level("supervisor") == 2
    assert m.get_tier_level("expert") == 1
    assert m.get_tier_level("unknown") == 0


def test_can_control():
    m = _mgr()
    assert m.can_control("large", "supervisor") is True
    assert m.can_control("large", "expert") is True
    assert m.can_control("supervisor", "expert") is True
    assert m.can_control("expert", "supervisor") is False
    assert m.can_control("unknown", "large") is False
    assert m.can_control("large", "unknown") is False


def test_can_modify_memory():
    m = _mgr()
    assert m.can_modify_memory("large", "expert") is True
    assert m.can_modify_memory("supervisor", "expert") is True
    assert m.can_modify_memory("expert", "large") is False
    assert m.can_modify_memory("unknown", "large") is False
    assert m.can_modify_memory("large", "unknown") is False


def test_validate_probe_start(monkeypatch):
    m = _mgr()
    import modules.thinking.identity as ident_mod
    monkeypatch.setattr(ident_mod, "get_identities", lambda: {"expert_1": {}})
    assert m.validate_probe_start("large", "expert", "expert_1") is None
    err = m.validate_probe_start("expert", "large", "expert_1")
    assert err and "权限不足" in err
    err2 = m.validate_probe_start("large", "expert", "ghost")
    assert err2 and "未知的身份模板" in err2


def test_get_probe_permission_manager_singleton(monkeypatch):
    import modules.thinking.probes.probe_permission as mod
    monkeypatch.setattr(mod, "_permission_manager", None)
    a = get_probe_permission_manager()
    b = get_probe_permission_manager()
    assert a is b
