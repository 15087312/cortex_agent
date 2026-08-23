#!/usr/bin/env python3
"""ToolPermissionController 测试 — 专家角色工具可见性 + 执行权限

回归覆盖：get_visible_tools 必须把 role 传给 _get_base_whitelist，
否则专家角色白名单为空 → 工具过滤失效（全部工具暴露 / 专家无工具可用）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.security_system.tool_permission_controller import (
    ToolPermissionController,
    get_tool_permission_controller,
)


def _reset_singleton():
    """清空模块级单例，保证每个测试用全新实例"""
    import modules.security_system.tool_permission_controller as m
    m._instance = None


def _make_controller():
    _reset_singleton()
    return get_tool_permission_controller()


# ======================================================================
# 可见性：专家白名单按 role 生效
# ======================================================================

class TestExpertToolVisibility:
    def test_code_writer_whitelist_applied(self):
        ctrl = _make_controller()
        tools = ctrl.get_visible_tools(tier="expert", mode="edit", role="code_writer")
        assert "run_command" in tools, "code_writer 应可见 run_command"
        assert "event_query" in tools
        assert "directory_tree" in tools
        assert "list_directory" in tools
        assert "read_text_file" in tools

    def test_code_writer_no_high_risk_tools(self):
        ctrl = _make_controller()
        tools = ctrl.get_visible_tools(tier="expert", mode="edit", role="code_writer")
        # 专家不可见 HIGH/CRITICAL 工具（如 exec_command）
        assert "exec_command" not in tools

    def test_reviewer_whitelist_applied(self):
        ctrl = _make_controller()
        tools = ctrl.get_visible_tools(tier="expert", mode="edit", role="code_reviewer")
        assert "memory_match" in tools
        assert "event_query" in tools

    def test_role_naming_fallback(self):
        """role 命名兼容：test_writer → expert_test_writer，customer → expert_customer"""
        ctrl = _make_controller()
        tester = ctrl.get_visible_tools(tier="expert", mode="edit", role="test_writer")
        assert "run_pytest" in tester
        customer = ctrl.get_visible_tools(tier="expert", mode="edit", role="customer")
        assert customer == ["event_query", "todo"]

    def test_supervisor_whitelist(self):
        ctrl = _make_controller()
        tools = ctrl.get_visible_tools(tier="supervisor", mode="edit", role="code_supervisor")
        assert "event_query" in tools
        assert "exec_command" not in tools


# ======================================================================
# 执行权限：code_writer 可用 run_command（whitelist 内 admin 工具）
# ======================================================================

class TestExpertExecutionPermission:
    def test_code_writer_can_run_command(self):
        ctrl = _make_controller()
        allowed, reason = ctrl.check_execution_permission(
            "run_command", "expert", "expert_implementer_001", "code_writer"
        )
        assert allowed, f"code_writer 应可执行 run_command: {reason}"

    def test_code_writer_can_query(self):
        ctrl = _make_controller()
        allowed, _ = ctrl.check_execution_permission(
            "event_query", "expert", "expert_implementer_001", "code_writer"
        )
        assert allowed

    def test_customer_cannot_admin(self, monkeypatch):
        # 密闭化：屏蔽 model_factory 实例查找（全量跑时其他测试注册的实例
        # 会经 tier 回退返回宽松权限），强制走角色默认权限表
        import types as _types

        class _EmptyFactory:
            def get(self, _mid):
                return None
            def list_by_tier(self, _tier):
                return []

        import modules.thinking.model_factory as _mf
        monkeypatch.setattr(_mf, "get_model_factory", lambda: _EmptyFactory())
        ctrl = _make_controller()
        allowed, _ = ctrl.check_execution_permission(
            "run_command", "expert", "expert_customer_001", "customer"
        )
        assert not allowed, "customer 角色不应能执行 admin 类工具"
