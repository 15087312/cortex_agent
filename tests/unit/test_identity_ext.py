"""identity 测试：身份加载 / 模板 / 权限 / 白名单"""
from unittest.mock import MagicMock

import pytest

import modules.thinking.identity as id_mod
from modules.thinking.identity import (
    ModelIdentity,
    ModelPermissions,
    get_identities,
    load_external_identities,
    get_startup_mode,
    list_persistent_experts,
    get_permissions,
    DEFAULT_TOOL_WHITELISTS,
)


# ── 身份字典 ───────────────────────────────────────────────────────────

def test_get_identities_cached(monkeypatch):
    monkeypatch.setattr(id_mod, "_merged_identities", {"roles": {"a": 1}})
    assert get_identities() == {"roles": {"a": 1}}


def test_load_from_yaml_error(monkeypatch):
    monkeypatch.setattr(id_mod, "_merged_identities", None)
    def boom(*a, **k):
        raise RuntimeError("yaml fail")
    monkeypatch.setattr("config.prompts.loader.get_loader", boom)
    import sys, types
    cfg_mod = sys.modules["config.settings"]
    monkeypatch.setattr(cfg_mod, "settings", types.SimpleNamespace(get_custom_agents=lambda: []))
    roles = id_mod._load_from_yaml()
    assert roles == {}


def test_load_from_yaml_with_custom_agents(monkeypatch):
    monkeypatch.setattr(id_mod, "_merged_identities", None)
    loader = MagicMock()
    loader.load = MagicMock(return_value={"roles": {"existing": {"name": "旧"}}})
    monkeypatch.setattr("config.prompts.loader.get_loader", lambda: loader)
    import sys, types
    cfg_mod = sys.modules["config.settings"]
    agents = [{"role": "custom_agent", "name": "自定义", "expertise": "代码, 测试", "tier": "expert", "model_id": "custom_001"}]
    monkeypatch.setattr(cfg_mod, "settings", types.SimpleNamespace(get_custom_agents=lambda: agents))
    roles = id_mod._load_from_yaml()
    assert "custom_agent" in roles
    assert roles["custom_agent"]["expertise"] == ["代码", "测试"]


def test_load_external_identities(monkeypatch):
    monkeypatch.setattr(id_mod, "_merged_identities", None)
    monkeypatch.setattr("modules.thinking.identity_loader.load_and_merge", lambda base, d: {"k": 1})
    assert load_external_identities("/tmp/x") == {"k": 1}


def test_load_external_identities_error(monkeypatch):
    monkeypatch.setattr(id_mod, "_merged_identities", None)
    def boom(base, d):
        raise RuntimeError("loader fail")
    monkeypatch.setattr("modules.thinking.identity_loader.load_and_merge", boom)
    monkeypatch.setattr(id_mod, "_load_from_yaml", lambda: {"fallback": 1})
    assert load_external_identities() == {"fallback": 1}


# ── 启动模式 ───────────────────────────────────────────────────────────

def test_startup_mode():
    assert get_startup_mode("large") == "on_demand"
    assert get_startup_mode("nonexistent") == "on_demand"


def test_list_persistent_experts():
    experts = list_persistent_experts()
    assert isinstance(experts, list)


# ── 权限 ───────────────────────────────────────────────────────────────

def test_permissions_methods():
    p = ModelPermissions(
        can_delegate=True, delegatable_tiers=["expert"],
        controllable_tiers=["expert"], allowed_tool_categories=["query"],
    )
    assert p.can_control_tier("expert") is True
    assert p.can_control_tier("large") is False
    assert p.can_delegate_to("expert") is True
    assert p.can_delegate_to("large") is False
    assert p.can_use_tool_category("query") is True
    assert p.can_use_tool_category("admin") is False


def test_get_permissions():
    assert get_permissions("large").can_delegate is True
    assert get_permissions("supervisor_custom").max_instances == 1
    assert get_permissions("expert_anything").can_write_memory is False
    assert get_permissions("unknown_role").allowed_tool_categories == ["query"]


# ── ModelIdentity ──────────────────────────────────────────────────────

def test_from_template(monkeypatch):
    template = {
        "model_id": "large_primary",
        "name": "总指挥",
        "tier": "large",
        "role": "orchestrator",
        "personality": "你是总指挥",
        "speaking_style": "自然",
        "expertise": ["规划"],
        "tool_whitelist": ["web_search"],
        "max_tokens": 4096,
        "temperature": 0.7,
    }
    monkeypatch.setattr(id_mod, "get_identities", lambda: {"orchestrator": template, "_model_names": {"large": "deepseek-v4"}})
    import sys, types
    cfg_mod = sys.modules["config.settings"]
    monkeypatch.setattr(cfg_mod, "settings", types.SimpleNamespace(get_model_params=lambda k: None))
    ident = ModelIdentity.from_template("orchestrator", max_tokens=512)
    assert ident.name == "总指挥"
    assert ident.tier == "large"
    assert ident.max_tokens == 512  # override 生效
    assert ident.permissions.can_delegate is True


def test_from_template_unknown():
    with pytest.raises(ValueError):
        ModelIdentity.from_template("不存在")


def test_from_template_default_whitelist(monkeypatch):
    template = {
        "model_id": "m1", "name": "主管", "tier": "supervisor", "role": "code_supervisor",
        "personality": "p", "speaking_style": "s", "expertise": [], "weaknesses": [],
    }
    monkeypatch.setattr(id_mod, "get_identities", lambda: {"code_supervisor": template, "_model_names": {}})
    import sys, types
    cfg_mod = sys.modules["config.settings"]
    monkeypatch.setattr(cfg_mod, "settings", types.SimpleNamespace(get_model_params=lambda k: None))
    ident = ModelIdentity.from_template("code_supervisor")
    assert ident.tool_whitelist == DEFAULT_TOOL_WHITELISTS["supervisor"]


def test_tier_label_and_to_dict():
    ident = ModelIdentity(model_id="m", name="n", tier="large", role="r", expertise=["e"])
    assert ident._tier_label() == "大模型层"
    d = ident.to_dict()
    assert d["tier"] == "large"
    assert d["permissions"]["can_delegate"] is False
