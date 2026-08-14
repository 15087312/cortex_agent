"""service_registry — 能力注册表 全部函数 + 防御性分支"""
from infra.tool_manager.service_registry import (
    register_capability,
    get_capability,
    has_capability,
    registered_names,
    unregister_capability,
)


def test_register_get_has():
    unregister_capability("t_svc")
    assert get_capability("t_svc") is None
    assert has_capability("t_svc") is False
    provider = lambda: object()  # noqa: E731
    register_capability("t_svc", provider)
    assert get_capability("t_svc") is provider
    assert has_capability("t_svc") is True
    assert "t_svc" in registered_names()
    unregister_capability("t_svc")
    assert has_capability("t_svc") is False


def test_register_overwrites():
    register_capability("t_ov", lambda: 1)
    register_capability("t_ov", lambda: 2)
    assert get_capability("t_ov")() == 2
    unregister_capability("t_ov")


def test_unregister_missing_no_error():
    unregister_capability("t_missing")
    assert has_capability("t_missing") is False
