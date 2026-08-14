"""security_system/switch_manager 测试：分级安全开关"""
from modules.security_system.security_level import SecurityLevel
from modules.security_system.switch_manager import SecuritySwitchManager


def _mgr():
    return SecuritySwitchManager()


def test_init_default_state():
    m = _mgr()
    state = m.get_all_state()
    assert state == {"L0": True, "L1": True, "L4": True}


def test_get_switch_state_known_level():
    m = _mgr()
    assert m.get_switch_state(SecurityLevel.CONTENT) is True


def test_get_switch_state_unknown_level():
    m = _mgr()
    assert m.get_switch_state("L9") is False


def test_set_switch_core_refused():
    """L0 核心安全层不可关闭"""
    m = _mgr()
    assert m.set_switch(SecurityLevel.CORE, False, user_auth=True) is False
    assert m.get_switch_state(SecurityLevel.CORE) is True


def test_set_switch_requires_user_auth():
    """非用户授权一律拒绝"""
    m = _mgr()
    assert m.set_switch(SecurityLevel.CONTENT, False, user_auth=False) is False
    assert m.get_switch_state(SecurityLevel.CONTENT) is True


def test_set_switch_disable_enable():
    m = _mgr()
    assert m.set_switch(SecurityLevel.CONTENT, False, user_auth=True) is True
    assert m.get_switch_state(SecurityLevel.CONTENT) is False
    assert m.get_all_state()["L1"] is False
    assert m.set_switch(SecurityLevel.CONTENT, True, user_auth=True) is True
    assert m.get_switch_state(SecurityLevel.CONTENT) is True


def test_set_switch_output_level():
    m = _mgr()
    assert m.set_switch(SecurityLevel.OUTPUT, False, user_auth=True) is True
    assert m.get_switch_state(SecurityLevel.OUTPUT) is False


def test_is_enabled_core_always_true():
    m = _mgr()
    assert m.is_enabled(SecurityLevel.CORE) is True


def test_is_enabled_level_state():
    m = _mgr()
    assert m.is_enabled(SecurityLevel.CONTENT) is True
    m.set_switch(SecurityLevel.CONTENT, False, user_auth=True)
    assert m.is_enabled(SecurityLevel.CONTENT) is False


def test_is_enabled_unknown_level_default_true():
    """未知级别默认视为启用（保守放行）"""
    m = _mgr()
    assert m.is_enabled("L2") is True
