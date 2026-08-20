"""resolve_active_large_role 测试：编排器启动总指挥时跟随编排页激活的 large 角色"""
import sys
import types

import modules.thinking.multi_model_orchestrator as mmo


def _stub_settings(monkeypatch, get_agent_active, custom_agents):
    cfg_mod = sys.modules["config.settings"]
    monkeypatch.setattr(
        cfg_mod, "settings",
        types.SimpleNamespace(
            get_agent_active=get_agent_active,
            get_custom_agents=lambda: custom_agents,
        ),
    )


def test_orchestrator_active_preferred(monkeypatch):
    _stub_settings(
        monkeypatch,
        get_agent_active=lambda role: role == "orchestrator",
        custom_agents=[{"role": "123", "tier": "large"}],
    )
    assert mmo.resolve_active_large_role() == "orchestrator"


def test_custom_large_used_when_orchestrator_disabled(monkeypatch):
    _stub_settings(
        monkeypatch,
        get_agent_active=lambda role: role != "orchestrator",
        custom_agents=[{"role": "123", "tier": "large"}],
    )
    assert mmo.resolve_active_large_role() == "123"


def test_orchestrator_always_preferred_when_active(monkeypatch):
    """orchestrator 激活时优先，即使有自定义 large 也激活"""
    _stub_settings(
        monkeypatch,
        get_agent_active=lambda role: role in ("orchestrator", "live"),
        custom_agents=[
            {"role": "dead", "tier": "large"},
            {"role": "live", "tier": "large"},
        ],
    )
    assert mmo.resolve_active_large_role() == "orchestrator"


def test_supervisor_custom_not_considered(monkeypatch):
    _stub_settings(
        monkeypatch,
        get_agent_active=lambda role: True,
        custom_agents=[{"role": "sup", "tier": "supervisor"}],
    )
    assert mmo.resolve_active_large_role() == "orchestrator"


def test_first_active_custom_large_when_orchestrator_off(monkeypatch):
    _stub_settings(
        monkeypatch,
        get_agent_active=lambda role: role == "live",
        custom_agents=[
            {"role": "dead", "tier": "large"},
            {"role": "live", "tier": "large"},
        ],
    )
    assert mmo.resolve_active_large_role() == "live"


def test_fallback_orchestrator_on_error(monkeypatch):
    cfg_mod = sys.modules["config.settings"]
    monkeypatch.setattr(
        cfg_mod, "settings",
        types.SimpleNamespace(),  # 缺少方法 → 内部异常 → 回退 orchestrator
    )
    assert mmo.resolve_active_large_role() == "orchestrator"