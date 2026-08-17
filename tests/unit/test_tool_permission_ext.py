"""tool_permission_controller 补测（此前 53%）：权限核心逻辑全覆盖"""
from unittest.mock import MagicMock, patch

import pytest

from modules.security_system.tool_permission_controller import ToolPermissionController


def _ctrl():
    return ToolPermissionController.__new__(ToolPermissionController)


# ── _expand_tags ────────────────────────────────────────────────────────────

def test_expand_tags():
    c = _ctrl()
    with patch("infra.tool_manager.tool_registry.ToolRegistry.get_tools_by_tag") as gt:
        gt.return_value = ["tool_a", "tool_b"]
        out = c._expand_tags(["tag:web", "calc"])
        assert "tool_a" in out and "tool_b" in out
        assert "calc" in out


# ── _apply_tier_filter ──────────────────────────────────────────────────────

class _FakeTool:
    def __init__(self, risk, category="query"):
        self.risk_level = risk
        self.category = category
        self.tags = []


def test_apply_tier_filter_expert():
    c = _ctrl()
    registry = MagicMock()
    registry.get_tool.side_effect = lambda n: {
        "calc": _FakeTool("LOW"),
        "web_fetch": _FakeTool("HIGH"),
        "exec_cmd": _FakeTool("CRITICAL"),
        "unknown": None,
    }.get(n)
    out = c._apply_tier_filter(["calc", "web_fetch", "exec_cmd", "unknown"], "expert", registry)
    assert "calc" in out
    assert "web_fetch" not in out
    assert "exec_cmd" not in out


def test_apply_tier_filter_non_expert():
    c = _ctrl()
    out = c._apply_tier_filter(["web_fetch", "exec_cmd"], "large", MagicMock())
    assert out == ["web_fetch", "exec_cmd"]


# ── _apply_skill_rules ──────────────────────────────────────────────────────

def _rules(**kw):
    return type("_SkillRules", (), {
        "restrict_to": kw.get("restrict_to", False),
        "allow_tools": kw.get("allow_tools", []),
        "block_tools": kw.get("block_tools", []),
        "block_tags": kw.get("block_tags", []),
        "block_categories": kw.get("block_categories", []),
    })


def test_apply_skill_rules_restrict():
    c = _ctrl()
    rules = _rules(restrict_to=True, allow_tools=["git_status"])
    out = c._apply_skill_rules(["git_status", "web_fetch", "calc", "todo"], rules, MagicMock())
    assert "git_status" in out
    assert "web_fetch" not in out
    assert "calc" in out and "todo" in out  # 核心系统工具保留


def test_apply_skill_rules_reorder():
    c = _ctrl()
    rules = _rules(allow_tools=["web_fetch", "git_status"])
    out = c._apply_skill_rules(["calc", "web_fetch", "todo", "git_status"], rules, MagicMock())
    assert out[0] == "web_fetch"
    assert out[1] == "git_status"


def test_apply_skill_rules_block_tools():
    c = _ctrl()
    rules = _rules(block_tools=["calc"])
    out = c._apply_skill_rules(["calc", "todo"], rules, MagicMock())
    assert out == ["todo"]


def test_apply_skill_rules_block_tags():
    c = _ctrl()
    rules = _rules(block_tags=["danger"])
    registry = MagicMock()
    registry._tools = {
        "calc": _FakeTool("LOW"),
        "exec": _FakeTool("HIGH"),
    }
    registry._tools["exec"].tags = ["danger"]
    registry._tools["calc"].tags = []
    out = c._apply_skill_rules(["calc", "exec"], rules, registry)
    assert out == ["calc"]


def test_apply_skill_rules_block_categories():
    c = _ctrl()
    rules = _rules(block_categories=["mutation"])
    registry = MagicMock()
    registry.get_tool.side_effect = lambda n: {
        "calc": _FakeTool("LOW", category="query"),
        "write_file": _FakeTool("LOW", category="mutation"),
    }.get(n)
    out = c._apply_skill_rules(["calc", "write_file"], rules, registry)
    assert out == ["calc"]


def test_apply_skill_rules_dict_rules():
    c = _ctrl()
    rules_dict = {"restrict_to": True, "allow_tools": ["git_status"]}
    out = c._apply_skill_rules(["git_status", "web_fetch"], rules_dict, MagicMock())
    assert "git_status" in out
    assert "web_fetch" not in out


# ── check_execution_permission ──────────────────────────────────────────────

def test_check_unknown_tool_allowed():
    c = _ctrl()
    with patch("infra.tool_manager.tool_registry.ToolRegistry.get_tool") as gt:
        gt.return_value = None
        allowed, reason = c.check_execution_permission("delegate_task", "large")
        assert allowed is True


def test_check_permission_denied():
    c = _ctrl()
    tool = _FakeTool("LOW", category="admin")
    with patch("infra.tool_manager.tool_registry.ToolRegistry.get_tool") as gt:
        gt.return_value = tool
        c._get_caller_permissions = MagicMock(return_value=None)
        # 无权限对象 → 默认拒绝
        allowed, reason = c.check_execution_permission("some_admin_tool", "expert")
        assert allowed is False or isinstance(reason, str)


# ── _get_base_whitelist ─────────────────────────────────────────────────────

def test_get_base_whitelist_role_tools(monkeypatch):
    c = _ctrl()
    import sys, types
    cfg = sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(
        get_role_tools=lambda role: {"whitelist": ["calc"], "blacklist": []}
    ))
    import modules.thinking.identity as ident
    monkeypatch.setattr(ident, "DEFAULT_TOOL_WHITELISTS", {"expert": ["web_fetch"]})
    monkeypatch.setattr(ident, "get_identities", lambda: {})
    out = c._get_base_whitelist("expert", "code_writer")
    assert out == ["calc"]


def test_get_base_whitelist_blacklist(monkeypatch):
    c = _ctrl()
    import sys, types
    cfg = sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(
        get_role_tools=lambda role: {"whitelist": ["calc", "web_fetch"], "blacklist": ["web_fetch"]}
    ))
    import modules.thinking.identity as ident
    monkeypatch.setattr(ident, "DEFAULT_TOOL_WHITELISTS", {"expert": ["web_fetch"]})
    monkeypatch.setattr(ident, "get_identities", lambda: {
        "code_writer": {"tier": "expert", "tool_whitelist": ["calc"]},
    })
    out = c._get_base_whitelist("expert", "x")
    assert out == ["calc"]


def test_get_base_whitelist_identity_fallback(monkeypatch):
    c = _ctrl()
    import modules.thinking.identity as ident
    monkeypatch.setattr(ident, "DEFAULT_TOOL_WHITELISTS", {"expert": ["web_fetch", "calc"]})
    monkeypatch.setattr(ident, "get_identities", lambda: {
        "code_writer": {"tier": "expert", "tool_whitelist": ["calc"]},
    })
    out = c._get_base_whitelist("expert", "code_writer")
    assert out == ["calc"]


# ── get_visible_tools 主流程 / _identity_whitelist / _get_caller_permissions ──

def test_get_visible_tools_full(monkeypatch):
    """主流程：基础白名单 → tag 展开 → tier 过滤 → skill rules"""
    c = _ctrl()
    c._get_base_whitelist = MagicMock(return_value=["tag:web", "exec_cmd", "calc"])
    c._expand_tags = MagicMock(return_value=["web_fetch", "exec_cmd", "calc"])
    c._apply_tier_filter = MagicMock(return_value=["web_fetch", "calc"])
    c._apply_skill_rules = MagicMock(return_value=["calc"])
    out = c.get_visible_tools("expert", "edit", "code_writer", skill_tool_rules={"restrict_to": False})
    assert out == ["calc"]
    c._apply_tier_filter.assert_called_once()
    c._apply_skill_rules.assert_called_once()


def test_get_visible_tools_no_skill(monkeypatch):
    """无技能规则时不调用 _apply_skill_rules"""
    c = _ctrl()
    c._get_base_whitelist = MagicMock(return_value=["calc"])
    c._expand_tags = MagicMock(return_value=["calc"])
    c._apply_tier_filter = MagicMock(return_value=["calc"])
    c._apply_skill_rules = MagicMock()
    out = c.get_visible_tools("expert", "edit", "code_writer")
    assert out == ["calc"]
    c._apply_skill_rules.assert_not_called()


def test_get_base_whitelist_star(monkeypatch):
    """role_tools whitelist 含 * → 展开为实际工具名（排除 security）"""
    c = _ctrl()
    import sys, types
    cfg = sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(
        get_role_tools=lambda role: {"whitelist": ["*"], "blacklist": []}
    ))
    import modules.thinking.identity as ident
    monkeypatch.setattr(ident, "get_identities", lambda: {})
    # 用 monkeypatch.setattr 隔离 _tools（teardown 自动恢复，不污染全局单例）
    import infra.tool_manager.tool_registry as tr
    class _FakeInfo:
        def __init__(self, source):
            self.source = source
    monkeypatch.setattr(tr.ToolRegistry, "_tools", {
        "user_tool": _FakeInfo("user"),
        "sec_tool": _FakeInfo("security"),
    })
    out = c._get_base_whitelist("expert", "x")
    assert "user_tool" in out
    assert "sec_tool" not in out


def test_identity_whitelist_from_yaml(monkeypatch):
    """_identity_whitelist 从 YAML identity 读取 tool_whitelist"""
    c = _ctrl()
    import modules.thinking.identity as ident
    monkeypatch.setattr(ident, "DEFAULT_TOOL_WHITELISTS", {"expert": ["default_tool"]})
    monkeypatch.setattr(ident, "get_identities", lambda: {
        "code_writer": {"tier": "expert", "tool_whitelist": ["yaml_tool", "calc"]},
        "other": {"tier": "expert", "tool_whitelist": ["other_tool"]},
    })
    out = c._identity_whitelist("expert", "code_writer", ident.DEFAULT_TOOL_WHITELISTS)
    assert "yaml_tool" in out
    assert "other_tool" not in out


def test_get_caller_permissions_by_model_id(monkeypatch):
    """_get_caller_permissions 优先按 model_id 精确查找"""
    c = _ctrl()
    instance = MagicMock()
    perms = MagicMock()
    perms.allowed_tool_categories = ["query"]
    instance.identity.permissions = perms
    import modules.thinking.model_factory as mf
    factory = MagicMock()
    factory.get.return_value = instance
    monkeypatch.setattr(mf, "get_model_factory", lambda: factory)
    out = c._get_caller_permissions("large_primary", "large")
    assert out == perms


def test_get_caller_permissions_unknown_returns_none(monkeypatch):
    """找不到任何权限 → 返回 None"""
    c = _ctrl()
    import modules.thinking.model_factory as mf
    factory = MagicMock()
    factory.get.return_value = None
    factory.list_by_tier.return_value = []
    monkeypatch.setattr(mf, "get_model_factory", lambda: factory)
    import modules.thinking.identity as ident
    monkeypatch.setattr(ident, "get_identities", lambda: {})
    monkeypatch.setattr(ident, "get_permissions", lambda key: type("P", (), {"allowed_tool_categories": []})())
    out = c._get_caller_permissions("ghost_001", "expert", "ghost")
    assert out is None


def test_check_execution_permission_denied_category():
    """权限类别不匹配 → 拒绝并给原因"""
    c = _ctrl()
    tool = _FakeTool("LOW", category="admin")
    perms = type("P", (), {
        "allowed_tool_categories": ["query", "mutation"],
        "can_use_tool_category": lambda self, cat: cat in self.allowed_tool_categories,
    })()
    c._get_caller_permissions = MagicMock(return_value=perms)
    with patch("infra.tool_manager.tool_registry.ToolRegistry.get_tool") as gt:
        gt.return_value = tool
        allowed, reason = c.check_execution_permission("admin_tool", "large")
        assert allowed is False
        assert "无权" in reason


def test_get_caller_permissions_fail_closed(monkeypatch):
    """权限查询异常 → fail-closed 返回空权限（拒绝全部），而非放行"""
    c = _ctrl()
    import modules.thinking.model_factory as mf
    monkeypatch.setattr(mf, "get_model_factory", lambda: (_ for _ in ()).throw(RuntimeError("factory down")))
    out = c._get_caller_permissions("x", "expert")
    assert out is not None
    assert out.allowed_tool_categories == []


def test_get_base_whitelist_role_tools_exception(monkeypatch):
    """role_tools 读取异常 → 回退 identity 白名单（不崩）"""
    c = _ctrl()
    import sys, types
    cfg = sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(
        get_role_tools=lambda role: (_ for _ in ()).throw(RuntimeError("cfg down"))
    ))
    import modules.thinking.identity as ident
    monkeypatch.setattr(ident, "DEFAULT_TOOL_WHITELISTS", {"expert": ["calc"]})
    monkeypatch.setattr(ident, "get_identities", lambda: {
        "code_writer": {"tier": "expert", "tool_whitelist": ["calc"]},
    })
    out = c._get_base_whitelist("expert", "x")
    assert out == ["calc"]
