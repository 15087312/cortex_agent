"""自限工具覆盖：只能减不能加 / 门控拦截 / 会话隔离清除。

文档要求：新逻辑必须有测试。覆盖 narrow_my_tools 与 reset_my_tools
的交集语义，以及权限控制器执行门控与清理。
"""
from unittest.mock import MagicMock

from modules.thinking.core.model_runner import ModelRunner
from modules.security_system.tool_permission_controller import (
    get_tool_permission_controller,
    get_ai_narrowed_tools,
    clear_ai_narrowed_tools,
)


def _make_runner(model_id="m_narrow_1", session_id="sess_abcdef01"):
    identity = MagicMock()
    identity.tier = "large"
    identity.model_id = model_id
    identity.name = "总指挥"
    identity.role = "orchestrator"
    identity.tool_whitelist = ["calc"]
    identity.metadata = {}
    instance = MagicMock()
    instance.identity = identity
    instance.client = MagicMock()
    bb = MagicMock()
    runner = ModelRunner(model_instance=instance, blackboard=bb, session_id=session_id)
    runner._task_id = "task_1"
    runner._active_skill = None
    runner._active_skill_tool_rules = None
    return runner


def _stub_base(monkeypatch, runner, tools):
    from modules.security_system import tool_permission_controller as tpc
    monkeypatch.setattr(
        tpc.ToolPermissionController, "get_visible_tools", lambda self, **k: list(tools)
    )
    # 确保从干净状态开始
    runner._narrowed_tools = None
    clear_ai_narrowed_tools(runner.model_id, runner.session_id, runner._task_id)


def test_narrow_intersection_ignores_out_of_scope(monkeypatch):
    """只能减不能加：超出用户范围的自动忽略，只留交集"""
    r = _make_runner()
    _stub_base(monkeypatch, r, ["calc", "todo", "read_text_file"])
    out = r._handle_narrow_my_tools({"keep_only": ["calc", "exec_command"]})
    assert "已收窄" in out
    assert "exec_command" in out  # 超出部分被点名忽略
    assert r._narrowed_tools == {"calc"}
    assert r._visible_tool_whitelist() == ["calc"]


def test_narrow_all_out_of_scope_fails(monkeypatch):
    r = _make_runner(model_id="m_narrow_2")
    _stub_base(monkeypatch, r, ["calc"])
    out = r._handle_narrow_my_tools({"keep_only": ["exec_command"]})
    assert "收窄失败" in out
    assert r._narrowed_tools is None


def test_narrow_empty_fails(monkeypatch):
    r = _make_runner(model_id="m_narrow_3")
    _stub_base(monkeypatch, r, ["calc"])
    assert "收窄失败" in r._handle_narrow_my_tools({"keep_only": []})


def test_reset_restores(monkeypatch):
    r = _make_runner(model_id="m_narrow_4")
    _stub_base(monkeypatch, r, ["calc", "todo"])
    r._handle_narrow_my_tools({"keep_only": ["calc"]})
    assert r._visible_tool_whitelist() == ["calc"]
    out = r._handle_reset_my_tools()
    assert "已恢复" in out
    assert r._visible_tool_whitelist() == ["calc", "todo"]


def test_gate_denies_narrowed_tool(monkeypatch):
    """执行门控与显示认同一份：收窄后直接调也被拦，控制工具不受限"""
    from infra.tool_manager.tool_registry import ToolRegistry

    r = _make_runner(model_id="m_narrow_5")
    _stub_base(monkeypatch, r, ["calc", "todo"])
    r._handle_narrow_my_tools({"keep_only": ["calc"]})

    tool_info = MagicMock()
    tool_info.category = "query"
    monkeypatch.setattr(ToolRegistry, "get_tool", lambda n: tool_info)
    perms = MagicMock()
    perms.can_use_tool_category.return_value = True
    ctrl = get_tool_permission_controller()
    monkeypatch.setattr(ctrl, "_get_caller_permissions", lambda *a, **k: perms)

    allowed, _ = ctrl.check_execution_permission("todo", "large", "m_narrow_5")
    assert allowed is False
    allowed, _ = ctrl.check_execution_permission("calc", "large", "m_narrow_5")
    assert allowed is True
    # 控制工具不受限，避免锁死无法恢复
    allowed, _ = ctrl.check_execution_permission("reset_my_tools", "large", "m_narrow_5")
    assert allowed is True


def test_clear_isolation(monkeypatch):
    """任务结束清除：同模型新任务不再受旧收窄影响"""
    r = _make_runner(model_id="m_narrow_6")
    _stub_base(monkeypatch, r, ["calc", "todo"])
    r._handle_narrow_my_tools({"keep_only": ["calc"]})
    assert get_ai_narrowed_tools("m_narrow_6", r.session_id, r._task_id) == {"calc"}
    r._clear_narrowing()
    assert get_ai_narrowed_tools("m_narrow_6", r.session_id, r._task_id) is None
    assert r._visible_tool_whitelist() == ["calc", "todo"]


def test_narrow_whitespace_only_fails(monkeypatch):
    """全空白工具名视为传空，拒绝"""
    r = _make_runner(model_id="m_narrow_7")
    _stub_base(monkeypatch, r, ["calc"])
    out = r._handle_narrow_my_tools({"keep_only": ["   "]})
    assert "收窄失败" in out


def test_narrow_register_error_reported(monkeypatch):
    """登记失败不静默吞：返回收窄异常"""
    import modules.security_system.tool_permission_controller as tpc

    r = _make_runner(model_id="m_narrow_8")
    _stub_base(monkeypatch, r, ["calc", "todo"])

    def _boom(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(tpc, "set_ai_narrowed_tools", _boom)
    out = r._handle_narrow_my_tools({"keep_only": ["calc"]})
    assert "收窄异常" in out


def test_scope_helpers_empty_model_id():
    """空模型编号直接返回，不写全局表"""
    from modules.security_system.tool_permission_controller import (
        set_ai_narrowed_tools,
        clear_ai_narrowed_tools,
    )

    set_ai_narrowed_tools("", {"calc"})
    clear_ai_narrowed_tools("")
    assert get_ai_narrowed_tools("") is None


def test_gate_narrowed_read_error_continues(monkeypatch, caplog):
    """门控自限读取异常记日志后继续走类别校验，不阻断"""
    import logging
    from unittest.mock import MagicMock

    from infra.tool_manager.tool_registry import ToolRegistry
    import modules.security_system.tool_permission_controller as tpc

    tool_info = MagicMock()
    tool_info.category = "query"
    monkeypatch.setattr(ToolRegistry, "get_tool", lambda n: tool_info)
    perms = MagicMock()
    perms.can_use_tool_category.return_value = True
    ctrl = get_tool_permission_controller()
    monkeypatch.setattr(ctrl, "_get_caller_permissions", lambda *a, **k: perms)

    def _boom(model_id, *a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(tpc, "get_ai_narrowed_tools", _boom)
    with caplog.at_level(logging.WARNING):
        allowed, _ = ctrl.check_execution_permission("calc", "large", "m_narrow_x")
    assert allowed is True
