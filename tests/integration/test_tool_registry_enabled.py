"""工具注册中心测试：运行时启用/禁用、白名单过滤"""
import pytest

from infra.tool_manager.tool_registry import ToolRegistry


@pytest.fixture
def isolate_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(ToolRegistry, "_settings_path", lambda: tmp_path / "tool_settings.json")
    ToolRegistry._disabled_loaded = False
    ToolRegistry._disabled_tools = set()
    yield


def test_set_enabled_toggle(isolate_settings):
    name = next(n for n, i in ToolRegistry._tools.items() if i.source != "security")
    ok, _ = ToolRegistry.set_tool_enabled(name, False)
    assert ok
    assert not ToolRegistry.is_tool_enabled(name)
    ok, _ = ToolRegistry.set_tool_enabled(name, True)
    assert ToolRegistry.is_tool_enabled(name)


def test_security_tool_not_disablable(isolate_settings):
    """security 源工具不可被禁用（无内置 security 工具时注册一个探针工具，
    保证这条防线始终被测到，而不是条件跳过）"""
    from infra.tool_manager.tool_registry import ToolInfo

    security_tools = [n for n, i in ToolRegistry._tools.items() if i.source == "security"]
    injected = None
    if security_tools:
        name = security_tools[0]
    else:
        name = "__security_guard_probe__"
        injected = name
        ToolRegistry._tools[name] = ToolInfo(name=name, func=lambda **kw: None, source="security")
    try:
        ok, msg = ToolRegistry.set_tool_enabled(name, False)
        assert not ok
        assert "安全工具" in msg
        assert ToolRegistry.is_tool_enabled(name) is True
    finally:
        if injected:
            ToolRegistry._tools.pop(injected, None)


def test_disabled_tool_filtered_from_api(isolate_settings):
    # 找一个非 core、非 security 的工具禁用，验证 get_tools_for_api 过滤
    name = next(n for n, i in ToolRegistry._tools.items()
                if i.source != "security" and not i.core)
    ToolRegistry.set_tool_enabled(name, False)
    names = [t["function"]["name"] for t in ToolRegistry.get_tools_for_api(["*"])]
    assert name not in names
    ToolRegistry.set_tool_enabled(name, True)
    names2 = [t["function"]["name"] for t in ToolRegistry.get_tools_for_api(["*"])]
    assert name in names2
