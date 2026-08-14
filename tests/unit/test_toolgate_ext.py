"""tool_security_gate 补测 — 剩余分支：回调异常 / 模式异常 / 审计异常 / yolo MEDIUM / 兜底 HIGH / 超时 / 解析失败

不真实调用 LLM：mock lite model 的 generate。
"""
import asyncio
import pytest
from unittest.mock import MagicMock, patch

from modules.security_system.tool_security_gate import (
    ToolSecurityGate,
    _emit_security_event,
    set_security_event_callback,
    get_tool_security_gate,
    _check_extreme_danger,
)
from utils.suspension import Suspension


def _set_mode(mode):
    from config.settings import settings
    object.__setattr__(settings, 'EXECUTION_MODE', mode)


@pytest.fixture
def gate():
    g = ToolSecurityGate(lite_model=None)
    g._audit = MagicMock()
    yield g
    _set_mode("edit")
    ToolSecurityGate._pending_reviews.clear()
    ToolSecurityGate._pending_review_sessions.clear()


@pytest.fixture
def allow_perm(monkeypatch):
    """放行角色类别权限检查（避免真实 ModelPermissions 拦截 git_* 等工具）"""
    import modules.security_system.tool_permission_controller as tpc
    ctrl = MagicMock()
    ctrl.check_execution_permission.return_value = (True, "ok")
    monkeypatch.setattr(tpc, "get_tool_permission_controller", lambda: ctrl)


class _Lite:
    def __init__(self, response='{"approved": true, "reason": "安全", "guidance": ""}'):
        self._response = response

    async def generate(self, prompt, max_tokens=0, temperature=0, system_prompt=None):
        return self._response


# ── 安全事件回调：extra_fields / 异常 ─────────────────────────────────────

def test_emit_security_event_extra_fields():
    cb = MagicMock()
    set_security_event_callback(cb)
    try:
        _emit_security_event("t", "tool", "m", True, "d", extra_fields={"extra": 1})
        payload = cb.call_args[0][0]
        assert payload["payload"]["extra"] == 1
    finally:
        set_security_event_callback(None)


def test_emit_security_event_callback_exception():
    def boom(payload):
        raise RuntimeError("stream down")

    set_security_event_callback(boom)
    try:
        _emit_security_event("t", "tool", "m", True)  # 不抛异常
    finally:
        set_security_event_callback(None)


# ── _check_extreme_danger 空参数分支 ──────────────────────────────────────

def test_extreme_danger_command_empty():
    assert _check_extreme_danger("exec_command", {"command": ""}) is None
    assert _check_extreme_danger("run_script", {"code": ""}) is None


# ── _execution_mode 异常 → "edit" ─────────────────────────────────────────

def test_execution_mode_exception(gate, monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "config.settings":
            raise ImportError("no settings")
        return real_import(name, *a, **k)

    with patch("builtins.__import__", fake_import):
        assert gate._execution_mode == "edit"


# ── check() 缓存命中 ─────────────────────────────────────────────────────

async def test_check_cache_hit(gate):
    gate._check_cache["edit|read_file|{}"] = (True, "cached")
    allowed, reason = await gate.check("read_file", {}, "expert", "m1")
    assert allowed is True
    assert reason == "cached"


# ── 极端危险时审计异常（241-242） ────────────────────────────────────────

async def test_extreme_danger_audit_exception(gate, allow_perm):
    gate._audit.log.side_effect = IOError("disk full")
    allowed, reason = await gate.check("exec_command", {"command": "rm -rf /"}, "large", "m1")
    assert allowed is False
    assert "极端危险" in reason


# ── 角色拦截时审计异常（260-261） ─────────────────────────────────────────

async def test_perm_block_audit_exception(gate, monkeypatch):
    import modules.security_system.tool_permission_controller as tpc
    ctrl = MagicMock()
    ctrl.check_execution_permission.return_value = (False, "无权")
    monkeypatch.setattr(tpc, "get_tool_permission_controller", lambda: ctrl)
    gate._audit.log.side_effect = IOError("disk full")
    allowed, reason = await gate.check("git_add", {}, "expert", "m1")
    assert allowed is False
    assert "无权" in reason


# ── 权限检查异常时 emit 失败（272-273） ───────────────────────────────────

async def test_perm_check_exception_emit_fails(gate, monkeypatch):
    import modules.security_system.tool_permission_controller as tpc
    ctrl = MagicMock()
    ctrl.check_execution_permission.side_effect = RuntimeError("db down")
    monkeypatch.setattr(tpc, "get_tool_permission_controller", lambda: ctrl)
    with patch("modules.security_system.tool_security_gate._emit_security_event",
               side_effect=RuntimeError("emit down")):
        allowed, reason = await gate.check("read_file", {}, "expert", "m1")
    assert allowed is False
    assert "安全拒绝" in reason


# ── Blackboard 安全拦截（283-289） ────────────────────────────────────────

async def test_blackboard_security_block(gate, allow_perm):
    bb = MagicMock()
    bb.has_security_block.return_value = True
    bb.get_security_block.return_value = {"description": "检测到风险"}
    gate.set_active_blackboard(bb)
    allowed, reason = await gate.check("git_add", {"path": "/tmp/x"}, "large", "m1")
    assert allowed is False
    assert "安全系统已拦截" in reason


async def test_blackboard_block_audit_exception(gate, allow_perm):
    bb = MagicMock()
    bb.has_security_block.side_effect = RuntimeError("boom")
    gate.set_active_blackboard(bb)
    gate._audit.log.side_effect = IOError("disk full")
    allowed, reason = await gate.check("git_add", {"path": "/tmp/x"}, "large", "m1")
    assert allowed is False
    assert "安全拒绝" in reason


# ── plan 写操作审计异常（313-314） ────────────────────────────────────────

async def test_plan_block_audit_exception(gate, allow_perm):
    _set_mode("plan")
    gate._audit.log.side_effect = IOError("disk full")
    allowed, reason = await gate.check("git_add", {"path": "/tmp/x"}, "large", "m1")
    assert allowed is False
    assert "plan" in reason


# ── control 模式审计异常（346-347, 358-359） ──────────────────────────────

async def test_control_high_audit_exception(gate, allow_perm):
    _set_mode("control")
    gate._audit.log.side_effect = IOError("disk full")

    async def fake_review(*a, **k):
        return False, "用户拒绝"

    gate._check_user_review = fake_review
    allowed, reason = await gate.check("git_push", {}, "expert", "m1")
    assert allowed is False


async def test_control_low_audit_exception(gate):
    _set_mode("control")
    gate._audit.log.side_effect = IOError("disk full")
    allowed, reason = await gate.check("read_file", {}, "expert", "m1")
    assert allowed is True
    assert "LOW" in reason


# ── HIGH 审计异常（381-382） ──────────────────────────────────────────────

async def test_high_audit_exception(gate, allow_perm):
    _set_mode("yolo")

    async def fake_llm(*a, **k):
        return True, "通过"

    gate._lite_model = _Lite()
    gate._model_available = True
    gate._audit.log.side_effect = IOError("disk full")
    with patch.object(gate, "_check_llm_review", fake_llm):
        allowed, reason = await gate.check("git_push", {}, "expert", "m1")
    assert allowed is True


# ── MEDIUM 分支 400-410 ───────────────────────────────────────────────────

async def test_medium_yolo_llm_approved(gate, allow_perm):
    _set_mode("yolo")
    gate._lite_model = _Lite()
    gate._model_available = True

    async def fake_llm(*a, **k):
        return True, "安全"

    with patch.object(gate, "_check_llm_review", fake_llm):
        allowed, reason = await gate.check("git_add", {"path": "/tmp/x"}, "expert", "m1")
    assert allowed is True
    assert reason == "安全"


async def test_medium_edit_no_llm_user(gate, allow_perm):
    _set_mode("edit")
    gate._model_available = False

    async def fake_review(*a, **k):
        return False, "用户拒绝"

    gate._check_user_review = fake_review
    allowed, reason = await gate.check("git_add", {"path": "/tmp/x"}, "expert", "m1")
    assert allowed is False


async def test_medium_yolo_no_llm_direct(gate, allow_perm):
    _set_mode("yolo")
    gate._model_available = False
    allowed, reason = await gate.check("git_add", {"path": "/tmp/x"}, "expert", "m1")
    assert allowed is True
    assert "yolo" in reason


async def test_medium_non_write_direct(gate, allow_perm):
    _set_mode("edit")
    gate._model_available = False
    import modules.security_system.tool_security_gate as tsg
    with patch.object(tsg, "_get_medium_risk_tools", return_value={"my_tool"}):
        allowed, reason = await gate.check("my_tool", {"x": 1}, "expert", "m1")
    assert allowed is True
    assert "直接放行" in reason


# ── MEDIUM 审计异常（419-420） ────────────────────────────────────────────

async def test_medium_audit_exception(gate, allow_perm):
    _set_mode("edit")
    gate._audit.log.side_effect = IOError("disk full")
    import modules.security_system.tool_security_gate as tsg
    with patch.object(tsg, "_get_medium_risk_tools", return_value={"my_tool"}):
        allowed, reason = await gate.check("my_tool", {"x": 1}, "expert", "m1")
    assert allowed is True


# ── _check_high_risk 兜底分支（470-475） ──────────────────────────────────

async def test_high_risk_fallback_mode_llm_available(gate, allow_perm):
    _set_mode("edit")
    gate._lite_model = _Lite()
    gate._model_available = True

    async def fake_review(*a, **k):
        return True, "用户批准: ok"

    gate._check_user_review = fake_review

    async def fake_llm(*a, **k):
        return True, "通过"

    with patch.object(gate, "_check_llm_review", fake_llm):
        allowed, reason = await gate.check("git_push", {}, "expert", "m1")
    assert allowed is True


async def test_high_risk_fallback_mode_llm_unavailable(gate, allow_perm):
    _set_mode("edit")
    gate._model_available = False

    async def fake_review(*a, **k):
        return True, "用户批准: ok"

    gate._check_user_review = fake_review
    allowed, reason = await gate.check("git_push", {}, "expert", "m1")
    assert allowed is True


async def test_high_risk_fallback_llm_available(gate, allow_perm):
    """兜底模式（非 yolo/edit）且 LLM 可用 → _check_llm_review（470-473）"""
    from unittest.mock import PropertyMock
    gate._model_available = True

    async def fake_llm(*a, **k):
        return True, "通过"

    with patch.object(gate, "_check_llm_review", fake_llm):
        with patch.object(ToolSecurityGate, "_execution_mode", new_callable=PropertyMock, return_value="auto"):
            allowed, reason = await gate._check_high_risk(
                "git_push", {}, "expert", "m1", ""
            )
    assert allowed is True


async def test_high_risk_fallback_llm_unavailable(gate, allow_perm):
    """兜底模式且 LLM 不可用 → 拒绝（474-475）"""
    from unittest.mock import PropertyMock
    gate._model_available = False
    with patch.object(ToolSecurityGate, "_execution_mode", new_callable=PropertyMock, return_value="auto"):
        allowed, reason = await gate._check_high_risk(
            "git_push", {}, "expert", "m1", ""
        )
    assert allowed is False
    assert "拒绝" in reason


# ── params_summary 截断（506） ────────────────────────────────────────────

async def test_params_summary_truncation(gate):
    ToolSecurityGate._pending_reviews.clear()
    gate._audit.log = MagicMock()
    params = {f"key{i}": "x" * 50 for i in range(10)}  # 总和远超 300
    assert len(", ".join(f"{k}={repr(v)[:100]}" for k, v in params.items())) > 300

    async def run_review():
        return await gate._check_user_review("run_script", params, "expert", "m1")

    task = asyncio.create_task(run_review())
    await asyncio.sleep(0.05)
    rid = next(iter(ToolSecurityGate._pending_reviews))
    ToolSecurityGate.resolve_review(rid, True, "用户批准")
    allowed, reason = await asyncio.wait_for(task, timeout=2)
    assert allowed is True
    assert not Suspension.is_suspended()


# ── _check_llm_review 不可用（555-556） ───────────────────────────────────

async def test_check_llm_review_model_unavailable(gate):
    gate._model_available = False
    allowed, reason = await gate._check_llm_review("x", {}, "expert", "m1", "")
    assert allowed is False
    assert "不可用" in reason


# ── _parse_review_result 嵌套 JSON 失败（659-661） ────────────────────────

def test_parse_review_result_nested_json_fail():
    gate = ToolSecurityGate.__new__(ToolSecurityGate)
    allowed, reason = gate._parse_review_result('prefix {"approved": badjson} trailing', "tool_x")
    assert allowed is False
    assert "拒绝" in reason


def test_parse_review_result_rejected_with_guidance():
    gate = ToolSecurityGate.__new__(ToolSecurityGate)
    allowed, reason = gate._parse_review_result(
        '{"approved": false, "reason": "风险", "guidance": "换用 read_file"}', "tool_x"
    )
    assert allowed is False
    assert "建议" in reason
    assert "换用 read_file" in reason


def test_parse_review_result_rejected_without_guidance():
    gate = ToolSecurityGate.__new__(ToolSecurityGate)
    allowed, reason = gate._parse_review_result('{"approved": false, "reason": "风险"}', "tool_x")
    assert allowed is False
    assert "建议" not in reason


# ── reject_session_reviews 混合会话（604->600） ───────────────────────────

def test_reject_session_reviews_mixed(monkeypatch):
    ToolSecurityGate._pending_reviews.clear()
    ToolSecurityGate._pending_review_sessions.clear()
    loop = asyncio.new_event_loop()
    fut_other = loop.create_future()
    fut_mine = loop.create_future()
    fut_other.set_result({"approved": True})  # 已完成 → 跳过
    ToolSecurityGate._pending_reviews["review_a"] = fut_other
    ToolSecurityGate._pending_reviews["review_b"] = fut_mine
    ToolSecurityGate._pending_review_sessions["review_a"] = "ses_other"
    ToolSecurityGate._pending_review_sessions["review_b"] = "ses_mine"
    rejected = ToolSecurityGate.reject_session_reviews("ses_mine")
    assert rejected == 1
    assert fut_mine.done()
    loop.close()


def test_reject_session_reviews_done_future_skip():
    """同会话但 future 已完成 → 跳过（604->600）"""
    ToolSecurityGate._pending_reviews.clear()
    ToolSecurityGate._pending_review_sessions.clear()
    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    fut.set_result({"approved": True})
    ToolSecurityGate._pending_reviews["review_done"] = fut
    ToolSecurityGate._pending_review_sessions["review_done"] = "ses_x"
    rejected = ToolSecurityGate.reject_session_reviews("ses_x")
    assert rejected == 0
    loop.close()


# ── get_tool_security_gate 单例 ───────────────────────────────────────────

def test_get_tool_security_gate_singleton(monkeypatch):
    import modules.security_system.tool_security_gate as tsg
    old = tsg._tool_security_gate
    tsg._tool_security_gate = None
    try:
        g1 = get_tool_security_gate()
        g2 = get_tool_security_gate()
        assert g1 is g2
    finally:
        tsg._tool_security_gate = old


def test_get_tool_security_gate_model_fail(monkeypatch):
    import modules.security_system.tool_security_gate as tsg
    old = tsg._tool_security_gate
    tsg._tool_security_gate = None
    try:
        with patch("infra.model.small_model_client.SmallModelClient",
                   side_effect=RuntimeError("no model")):
            g = get_tool_security_gate()
            assert g._model_available is False
    finally:
        tsg._tool_security_gate = old


# ── 超时自动拒绝（537-539）：直接构造 future 抛 TimeoutError ─────────────

async def test_user_review_timeout(gate):
    gate._audit.log = MagicMock()

    class _TimeoutFuture:
        _done = False

        def done(self):
            return self._done

    # 用 wait_for 包一层：_check_user_review 里 wait_for(future, timeout=None)
    # 无法触发超时，直接 monkeypatch asyncio.wait_for 抛 TimeoutError
    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError("timeout")):
        allowed, reason = await gate._check_user_review("run_script", {}, "expert", "m1")
    assert allowed is False
    assert "超时" in reason
    assert not Suspension.is_suspended()
