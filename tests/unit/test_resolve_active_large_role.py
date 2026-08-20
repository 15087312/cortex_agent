"""resolve_active_large_role 测试：编排器启动总指挥时跟随编排页激活的 large 角色"""
import sys
import types

import modules.thinking.multi_model_orchestrator as mmo
from modules.thinking.multi_model_orchestrator import MultiModelOrchestrator


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


# ── 回归：调用点必须使用 resolve_active_large_role 的结果，而非硬编码 ──

def test_probe_started_identity_key_follows_active_large_role(monkeypatch):
    """probe_started 的 identity_key 必须是激活总指挥，而不是写死的 orchestrator"""
    orch = MultiModelOrchestrator.__new__(MultiModelOrchestrator)
    monkeypatch.setattr(mmo, "resolve_active_large_role", lambda: "custom_orchestrator")
    content = orch._build_probe_started_content("帮我写代码", "sess123", skill_id="sk")
    assert content["identity_key"] == "custom_orchestrator"
    assert content["target_tier"] == "large"
    assert content["action"] == "probe_started"


def test_probe_started_content_fields(monkeypatch):
    """probe_started 载荷字段完整（skill_id/return_to_session_id 等）"""
    orch = MultiModelOrchestrator.__new__(MultiModelOrchestrator)
    monkeypatch.setattr(mmo, "resolve_active_large_role", lambda: "orchestrator")
    content = orch._build_probe_started_content("hi", "sess_x", skill_id="code")
    assert content["skill_id"] == "code"
    assert content["return_to_session_id"] == "sess_x"
    assert content["task_description"] == "hi"
    assert content["priority"] == 10