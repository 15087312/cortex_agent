"""tool_permission_controller 补充测试：技能规则 / role_tools / 权限查找 / 控制工具"""
from unittest.mock import MagicMock, patch

from modules.security_system.tool_permission_controller import (
    ToolPermissionController,
    get_tool_permission_controller,
)
from modules.thinking.identity import ModelPermissions


import sys as _sys

_SETTINGS_MODULE = _sys.modules["config.settings"]


def _make():
    return ToolPermissionController()


def _fake_settings(monkeypatch, settings):
    monkeypatch.setattr(_SETTINGS_MODULE, "settings", settings)


# ── skill_tool_rules 入口 ─────────────────────────────────────────────────────

def test_visible_with_skill_rules_dict(monkeypatch):
    ctrl = _make()
    monkeypatch.setattr(ctrl, "_get_base_whitelist", lambda tier, role: ["calc", "todo", "event_query"])
    monkeypatch.setattr(ctrl, "_expand_tags", lambda wl: wl)
    monkeypatch.setattr(ctrl, "_apply_tier_filter", lambda tools, tier, reg: tools)
    rules = {
        "restrict_to": False,
        "allow_tools": ["todo"],
        "block_tools": [],
        "block_tags": [],
        "block_categories": [],
    }
    result = ctrl.get_visible_tools(tier="large", mode="edit", skill_tool_rules=rules)
    assert result[0] == "todo"


# ── _get_base_whitelist：role_tools 覆盖 ───────────────────────────────────────

def test_base_whitelist_role_tools_override(monkeypatch):
    ctrl = _make()
    settings = MagicMock()
    settings.get_role_tools.return_value = {"whitelist": ["calc", "todo"], "blacklist": []}
    _fake_settings(monkeypatch, settings)
    assert ctrl._get_base_whitelist("large", "code_writer") == ["calc", "todo"]


def test_base_whitelist_role_tools_star_and_blacklist(monkeypatch):
    ctrl = _make()
    settings = MagicMock()
    settings.get_role_tools.return_value = {"whitelist": ["*"], "blacklist": ["calc"]}
    _fake_settings(monkeypatch, settings)
    result = ctrl._get_base_whitelist("large", "boss")
    assert "calc" not in result
    assert "web_search" in result


def test_base_whitelist_role_tools_empty_falls_back(monkeypatch):
    ctrl = _make()
    settings = MagicMock()
    settings.get_role_tools.return_value = {"whitelist": [], "blacklist": []}
    _fake_settings(monkeypatch, settings)
    from modules.thinking.identity import DEFAULT_TOOL_WHITELISTS
    result = ctrl._get_base_whitelist("large", "x")
    assert result == list(DEFAULT_TOOL_WHITELISTS["large"])


def test_base_whitelist_role_tools_exception(monkeypatch):
    ctrl = _make()
    def boom(role):
        raise RuntimeError("no yaml")
    settings = MagicMock()
    settings.get_role_tools = boom
    _fake_settings(monkeypatch, settings)
    from modules.thinking.identity import DEFAULT_TOOL_WHITELISTS
    result = ctrl._get_base_whitelist("large", "x")
    assert result == list(DEFAULT_TOOL_WHITELISTS["large"])


def test_base_whitelist_no_role_tools(monkeypatch):
    # get_role_tools 返回空 dict → 跳过覆盖，走 identity
    ctrl = _make()
    settings = MagicMock()
    settings.get_role_tools.return_value = {}
    _fake_settings(monkeypatch, settings)
    from modules.thinking.identity import DEFAULT_TOOL_WHITELISTS
    assert ctrl._get_base_whitelist("large", "x") == list(DEFAULT_TOOL_WHITELISTS["large"])


# ── _identity_whitelist 分支 ──────────────────────────────────────────────────

def test_identity_whitelist_tier_match(monkeypatch):
    fake_ids = {
        "w1": {"tier": "expert", "role": "code_writer", "tool_whitelist": ["run_command", "todo"]},
        "w2": {"tier": "expert", "role": "other", "tool_whitelist": ["x"]},
    }
    monkeypatch.setattr("modules.thinking.identity.get_identities", lambda: fake_ids)
    assert ToolPermissionController._identity_whitelist("expert", "code_writer", {}) == ["run_command", "todo"]


def test_identity_whitelist_no_tier_match(monkeypatch):
    # 身份存在但 tier 都不匹配 → 继续循环 → expert(无 role) 回退空列表
    fake_ids = {
        "w1": {"tier": "supervisor", "tool_whitelist": ["x"]},
        "w2": {"tier": "large", "tool_whitelist": ["y"]},
    }
    monkeypatch.setattr("modules.thinking.identity.get_identities", lambda: fake_ids)
    assert ToolPermissionController._identity_whitelist("expert", "", {"large": ["fallback"]}) == []


def test_identity_whitelist_exception(monkeypatch):
    def boom():
        raise Exception("yaml err")
    monkeypatch.setattr("modules.thinking.identity.get_identities", boom)
    assert ToolPermissionController._identity_whitelist("large", "", {"large": ["a"]}) == ["a"]


def test_identity_whitelist_large_supervisor_fallback():
    assert ToolPermissionController._identity_whitelist("large", "", {"large": ["a"]}) == ["a"]
    assert ToolPermissionController._identity_whitelist("supervisor", "", {"supervisor": ["b"]}) == ["b"]


def test_identity_whitelist_expert_role_fallback():
    assert ToolPermissionController._identity_whitelist(
        "expert", "code_writer", {"expert_code_writer": ["x"]}) == ["x"]
    assert ToolPermissionController._identity_whitelist(
        "expert", "tester", {"tester": ["y"]}) == ["y"]
    assert ToolPermissionController._identity_whitelist("expert", "unknown_role", {}) == []


# ── _expand_tags / _apply_tier_filter ─────────────────────────────────────────

def test_expand_tags(monkeypatch):
    ctrl = _make()
    from infra.tool_manager.tool_registry import ToolRegistry
    monkeypatch.setattr(ToolRegistry, "get_tools_by_tag", lambda tag: {"a", "b"} if tag == "git_dev" else set())
    result = ctrl._expand_tags(["calc", "tag:git_dev"])
    assert "calc" in result
    assert "a" in result and "b" in result


def test_apply_tier_filter_non_expert_keeps_all():
    ctrl = _make()
    assert ctrl._apply_tier_filter(["calc", "exec_command"], "large", None) == ["calc", "exec_command"]


def test_apply_tier_filter_expert_drops_high(monkeypatch):
    ctrl = _make()
    from infra.tool_manager.tool_registry import ToolRegistry

    class _Reg:
        _tools = {}

        @staticmethod
        def get_tool(name):
            return MagicMock(risk_level="HIGH" if name == "exec_command" else "LOW")

    result = ctrl._apply_tier_filter(["calc", "exec_command"], "expert", _Reg)
    assert result == ["calc"]


# ── _apply_skill_rules 分支 ───────────────────────────────────────────────────

def _rule_holder(**kw):
    return type("_R", (), kw)


def test_apply_skill_rules_restrict_to(monkeypatch):
    ctrl = _make()
    from infra.tool_manager.tool_registry import ToolRegistry
    rules = {"restrict_to": True, "allow_tools": ["memory_match"], "block_tools": [], "block_tags": [], "block_categories": []}
    result = ctrl._apply_skill_rules(["calc", "todo", "memory_match", "web_search"], rules, ToolRegistry)
    assert set(result) <= {"memory_match", "calc", "todo", "tools_search"}


def test_apply_skill_rules_block_tools(monkeypatch):
    ctrl = _make()
    from infra.tool_manager.tool_registry import ToolRegistry
    rules = _rule_holder(restrict_to=False, allow_tools=[], block_tools=["calc"], block_tags=[], block_categories=[])
    result = ctrl._apply_skill_rules(["calc", "todo"], rules, ToolRegistry)
    assert "calc" not in result


def test_apply_skill_rules_block_tags(monkeypatch):
    ctrl = _make()
    from infra.tool_manager.tool_registry import ToolRegistry
    ToolRegistry.register_tool(name="ext_tag_block", func=lambda: None, tags=["ext_forbidden"])
    try:
        rules = _rule_holder(restrict_to=False, allow_tools=[], block_tools=[], block_tags=["ext_forbidden"], block_categories=[])
        result = ctrl._apply_skill_rules(["calc", "ext_tag_block"], rules, ToolRegistry)
        assert "ext_tag_block" not in result
    finally:
        ToolRegistry.unregister("ext_tag_block")


def test_apply_skill_rules_block_categories(monkeypatch):
    ctrl = _make()
    from infra.tool_manager.tool_registry import ToolRegistry
    ToolRegistry.register_tool(name="ext_admin_block", func=lambda: None, category="admin")
    try:
        rules = _rule_holder(restrict_to=False, allow_tools=[], block_tools=[], block_tags=[], block_categories=["admin"])
        result = ctrl._apply_skill_rules(["calc", "ext_admin_block"], rules, ToolRegistry)
        assert "ext_admin_block" not in result
        assert "calc" in result
    finally:
        ToolRegistry.unregister("ext_admin_block")


# ── check_execution_permission ────────────────────────────────────────────────

def test_exec_permission_tool_not_in_registry(monkeypatch):
    ctrl = _make()
    monkeypatch.setattr("infra.tool_manager.tool_registry.ToolRegistry.get_tool", lambda n: None)
    allowed, reason = ctrl.check_execution_permission("delegate_task", "large")
    assert allowed is True


def test_exec_permission_denied_by_category(monkeypatch):
    ctrl = _make()
    tool_info = MagicMock()
    tool_info.category = "admin"
    monkeypatch.setattr("infra.tool_manager.tool_registry.ToolRegistry.get_tool", lambda n: tool_info)
    perms = MagicMock()
    perms.can_use_tool_category.return_value = False
    perms.allowed_tool_categories = ["query"]
    monkeypatch.setattr(ctrl, "_get_caller_permissions", lambda *a, **k: perms)
    allowed, reason = ctrl.check_execution_permission("exec_command", "expert", "m1", "code_writer")
    assert allowed is False
    assert "无权调用" in reason


def test_exec_permission_permissions_none_allowed(monkeypatch):
    ctrl = _make()
    tool_info = MagicMock()
    tool_info.category = "admin"
    monkeypatch.setattr("infra.tool_manager.tool_registry.ToolRegistry.get_tool", lambda n: tool_info)
    monkeypatch.setattr(ctrl, "_get_caller_permissions", lambda *a, **k: None)
    allowed, reason = ctrl.check_execution_permission("exec_command", "expert")
    assert allowed is True


def test_exec_permission_allowed(monkeypatch):
    ctrl = _make()
    tool_info = MagicMock()
    tool_info.category = "query"
    monkeypatch.setattr("infra.tool_manager.tool_registry.ToolRegistry.get_tool", lambda n: tool_info)
    perms = MagicMock()
    perms.can_use_tool_category.return_value = True
    monkeypatch.setattr(ctrl, "_get_caller_permissions", lambda *a, **k: perms)
    allowed, reason = ctrl.check_execution_permission("event_query", "expert")
    assert allowed is True


# ── _get_caller_permissions 分支 ──────────────────────────────────────────────

def test_caller_permissions_model_id_match(monkeypatch):
    factory = MagicMock()
    inst = MagicMock()
    inst.identity.permissions = "PERM_BY_ID"
    factory.get.return_value = inst
    monkeypatch.setattr("modules.thinking.model_factory.get_model_factory", lambda: factory)
    result = ToolPermissionController._get_caller_permissions("model_x", "expert", "code_writer")
    assert result == "PERM_BY_ID"
    factory.get.assert_called_with("model_x")


def test_caller_permissions_model_id_no_permissions(monkeypatch):
    # instance 命中但无 permissions 属性 → 回退 tier 查找
    factory = MagicMock()
    inst = MagicMock()
    del inst.identity.permissions  # 触发 hasattr 检查失败路径
    factory.get.return_value = inst
    factory.list_by_tier.return_value = []
    monkeypatch.setattr("modules.thinking.model_factory.get_model_factory", lambda: factory)
    monkeypatch.setattr("modules.thinking.identity.get_identities", lambda: {})
    monkeypatch.setattr(
        "modules.thinking.identity.get_permissions",
        lambda key: ModelPermissions(allowed_tool_categories=["query"]),
    )
    result = ToolPermissionController._get_caller_permissions("model_x", "large", "")
    assert result is not None
    assert result.allowed_tool_categories == ["query"]


def test_caller_permissions_role_exact_match(monkeypatch):
    # role 精确匹配实例 → 走 261-263 返回
    factory = MagicMock()
    inst = MagicMock()
    inst.identity.permissions = "PERM_ROLE_EXACT"
    inst.identity.role = "code_writer"
    factory.get.return_value = None
    factory.list_by_tier.return_value = [inst]
    monkeypatch.setattr("modules.thinking.model_factory.get_model_factory", lambda: factory)
    result = ToolPermissionController._get_caller_permissions("", "expert", "code_writer")
    assert result == "PERM_ROLE_EXACT"


def test_caller_permissions_tier_role_match(monkeypatch):
    factory = MagicMock()
    inst = MagicMock()
    inst.identity.permissions = "PERM_ROLE"
    inst.identity.role = "code_writer"
    factory.get.return_value = None
    factory.list_by_tier.return_value = [inst]
    monkeypatch.setattr("modules.thinking.model_factory.get_model_factory", lambda: factory)
    result = ToolPermissionController._get_caller_permissions("", "expert", "expert_code_writer")
    assert result == "PERM_ROLE"


def test_caller_permissions_tier_fallback(monkeypatch):
    factory = MagicMock()
    inst = MagicMock()
    inst.identity.permissions = "PERM_FIRST"
    factory.get.return_value = None
    factory.list_by_tier.return_value = [inst]
    monkeypatch.setattr("modules.thinking.model_factory.get_model_factory", lambda: factory)
    result = ToolPermissionController._get_caller_permissions("", "supervisor", "")
    assert result == "PERM_FIRST"


def test_caller_permissions_yaml_match(monkeypatch):
    factory = MagicMock()
    factory.get.return_value = None
    factory.list_by_tier.return_value = []
    monkeypatch.setattr("modules.thinking.model_factory.get_model_factory", lambda: factory)
    fake_ids = {
        "expert_code_writer": {
            "tier": "expert",
            "permissions": {"allowed_tool_categories": ["query", "admin"]},
        },
    }
    monkeypatch.setattr("modules.thinking.identity.get_identities", lambda: fake_ids)
    result = ToolPermissionController._get_caller_permissions("", "expert", "code_writer")
    assert result is not None
    assert result.allowed_tool_categories == ["query", "admin"]


def test_caller_permissions_yaml_tier_match(monkeypatch):
    factory = MagicMock()
    factory.get.return_value = None
    factory.list_by_tier.return_value = []
    monkeypatch.setattr("modules.thinking.model_factory.get_model_factory", lambda: factory)
    fake_ids = {
        "w1": {"tier": "expert", "permissions": {"allowed_tool_categories": ["query"]}},
    }
    monkeypatch.setattr("modules.thinking.identity.get_identities", lambda: fake_ids)
    result = ToolPermissionController._get_caller_permissions("", "expert", "unmatched_role")
    assert result is not None
    assert result.allowed_tool_categories == ["query"]


def test_caller_permissions_yaml_empty_cats(monkeypatch):
    # YAML 存在 permissions 但 allowed_tool_categories 为空 → 不命中，继续回退
    factory = MagicMock()
    factory.get.return_value = None
    factory.list_by_tier.return_value = []
    monkeypatch.setattr("modules.thinking.model_factory.get_model_factory", lambda: factory)
    fake_ids = {
        "w1": {"tier": "expert", "permissions": {"allowed_tool_categories": []}},
        "w2": {"tier": "expert", "permissions": None},
    }
    monkeypatch.setattr("modules.thinking.identity.get_identities", lambda: fake_ids)
    monkeypatch.setattr(
        "modules.thinking.identity.get_permissions",
        lambda key: ModelPermissions(allowed_tool_categories=["query"]),
    )
    result = ToolPermissionController._get_caller_permissions("", "expert", "x")
    assert result.allowed_tool_categories == ["query"]


def test_caller_permissions_tier_empty(monkeypatch):
    # tier 无法识别（非 large/supervisor/expert，且 role 非 expert_* 前缀）→ 走 YAML
    factory = MagicMock()
    factory.get.return_value = None
    factory.list_by_tier.return_value = []
    monkeypatch.setattr("modules.thinking.model_factory.get_model_factory", lambda: factory)
    fake_ids = {
        "w1": {"tier": "large", "permissions": {"allowed_tool_categories": ["query"]}},
    }
    monkeypatch.setattr("modules.thinking.identity.get_identities", lambda: fake_ids)
    result = ToolPermissionController._get_caller_permissions("", "weird_tier", "x")
    assert result is not None
    assert result.allowed_tool_categories == ["query"]


def test_caller_permissions_none_found(monkeypatch):
    # 所有回退均未命中 → 返回 None（默认放行）
    factory = MagicMock()
    factory.get.return_value = None
    factory.list_by_tier.return_value = []
    monkeypatch.setattr("modules.thinking.model_factory.get_model_factory", lambda: factory)
    monkeypatch.setattr("modules.thinking.identity.get_identities", lambda: {})
    monkeypatch.setattr(
        "modules.thinking.identity.get_permissions",
        lambda key: ModelPermissions(allowed_tool_categories=[]),
    )
    result = ToolPermissionController._get_caller_permissions("", "large", "")
    assert result is None


def test_caller_permissions_get_permissions_fallback(monkeypatch):
    factory = MagicMock()
    factory.get.return_value = None
    factory.list_by_tier.return_value = []
    monkeypatch.setattr("modules.thinking.model_factory.get_model_factory", lambda: factory)
    monkeypatch.setattr("modules.thinking.identity.get_identities", lambda: {})
    from modules.thinking.identity import ModelPermissions
    monkeypatch.setattr(
        "modules.thinking.identity.get_permissions",
        lambda key: ModelPermissions(allowed_tool_categories=["query"]),
    )
    result = ToolPermissionController._get_caller_permissions("", "large", "code_writer")
    assert result is not None
    assert result.allowed_tool_categories == ["query"]


def test_caller_permissions_exception_fail_closed(monkeypatch):
    def boom():
        raise RuntimeError("factory down")
    monkeypatch.setattr("modules.thinking.model_factory.get_model_factory", boom)
    result = ToolPermissionController._get_caller_permissions("", "large", "")
    assert result is not None
    assert result.allowed_tool_categories == []


# ── get_control_tools ─────────────────────────────────────────────────────────

def _names(tools):
    return [t["function"]["name"] for t in tools]


def test_get_control_tools_large_full():
    ctrl = _make()
    tools = _names(ctrl.get_control_tools("large", "edit", delegation_available=True))
    assert "continue_thinking" in tools
    assert "delegate_task" in tools
    assert "create_supervisor" in tools
    assert "request_skill" in tools


def test_get_control_tools_supervisor():
    ctrl = _make()
    tools = _names(ctrl.get_control_tools("supervisor", "edit", delegation_available=True))
    assert "delegate_task" in tools
    assert "create_supervisor" not in tools
    assert "request_skill" not in tools


def test_get_control_tools_no_delegation():
    ctrl = _make()
    tools = _names(ctrl.get_control_tools("large", "edit", delegation_available=False))
    assert "delegate_task" not in tools
    assert "stop_task" not in tools


def test_get_control_tools_expert():
    ctrl = _make()
    tools = _names(ctrl.get_control_tools("expert", "edit", delegation_available=False))
    assert tools == ["continue_thinking", "query_tool_details"]


# ── 单例 ──────────────────────────────────────────────────────────────────────

def test_get_singleton(monkeypatch):
    import modules.security_system.tool_permission_controller as m
    monkeypatch.setattr(m, "_instance", None)
    a = get_tool_permission_controller()
    b = get_tool_permission_controller()
    assert a is b
    assert isinstance(a, ToolPermissionController)
    monkeypatch.setattr(m, "_instance", None)
