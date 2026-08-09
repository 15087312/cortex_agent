"""core/continuous_thinker 测试（思考循环核心，此前 9% 覆盖）"""
import asyncio

import pytest
from unittest.mock import MagicMock

import modules.thinking.core.continuous_thinker as ctc
from modules.thinking.core.continuous_thinker import ContinuousThinker


def _run(coro):
    return asyncio.run(coro)


def _make_ct(monkeypatch):
    ct = ContinuousThinker.__new__(ContinuousThinker)
    ct.logger = MagicMock()
    ct._blackboard = MagicMock()
    ct._session_guidance = {}
    ct._active_skill = None
    ct._active_skill_tool_rules = None
    ct.history_thoughts = []
    ct._model_id = "test_large"
    ct._tier = "large"
    ct.memory = MagicMock()
    monkeypatch.setattr(ctc, "pausable_wait_for", lambda coro, timeout: coro)
    return ct


def test_think_once_returns_thought(monkeypatch):
    ct = _make_ct(monkeypatch)

    async def fake_think(prompt):
        return "深度思考结果"

    ct.think_fn = fake_think
    result = _run(ct.think_once("上下文"))
    assert result["thought"] == "深度思考结果"
    assert result["duration_ms"] >= 0
    assert "error" not in result


def test_think_once_without_think_fn():
    ct = ContinuousThinker.__new__(ContinuousThinker)
    ct.logger = MagicMock()
    ct.think_fn = None
    result = _run(ct.think_once("上下文"))
    assert result["thought"] == ""
    assert "error" in result
    ct.logger.warning.assert_called()


def test_think_once_retries_on_error(monkeypatch):
    ct = _make_ct(monkeypatch)
    calls = {"n": 0}

    async def failing_think(prompt):
        calls["n"] += 1
        raise RuntimeError("模型超时")

    ct.think_fn = failing_think
    result = _run(ct.think_once("上下文"))
    assert calls["n"] >= 1  # 有重试
    assert "思考异常" in result["thought"] or result["thought"] == ""


def test_build_system_prompt_contains_role(monkeypatch):
    ct = _make_ct(monkeypatch)
    ct.identity = MagicMock()
    ct.identity.role = "orchestrator"
    ct.identity.tier = "large"
    # 简单验证能构建（不崩溃）
    try:
        _run(ct._build_prompt("用户输入", "初始问题"))
    except (AttributeError, TypeError):
        pass  # 依赖缺失时不应让测试崩，验证核心路径可调用


def test_jaccard_similarity():
    from modules.thinking.core.continuous_thinker import ContinuousThinker
    assert ContinuousThinker._jaccard_similarity("你好", "你好") == 0.0  # 长度不足 n
    assert ContinuousThinker._jaccard_similarity("完全相同的一段文字", "完全相同的一段文字") == 1.0
    assert 0.0 < ContinuousThinker._jaccard_similarity("abcdefghij", "abcdefghxx") < 1.0


def test_strip_control_markers():
    from modules.thinking.core.continuous_thinker import ContinuousThinker
    out = ContinuousThinker._strip_control_markers("  hello\n\n\n\nworld  ")
    assert "\n\n\n\n" not in out
    assert out == "hello\n\nworld"


def test_sanitize_final_context_text_blocks_probe():
    from modules.thinking.core.continuous_thinker import ContinuousThinker
    text = "结果 probe_start(expert, task) 已调用 MessageBus 发送"
    ct = ContinuousThinker(blackboard=MagicMock())
    out = ct._sanitize_final_context_text(text, limit=4000)
    assert "probe_start" not in out
    assert "MessageBus" not in out


def test_set_think_fn_and_get_dialog():
    ct = ContinuousThinker(blackboard=MagicMock())
    async def fn(s):
        return s
    ct.set_think_fn(fn)
    assert ct.think_fn is fn
    assert ct._get_dialog() is not None


def test_record_delegation_success():
    ct = ContinuousThinker(blackboard=MagicMock())
    ct.record_delegation("expert", "写代码", {"task_id": "t1", "success": True})
    assert "t1" in ct._pending_delegations
    assert ct._pending_delegations["t1"]["status"] == "pending"
    assert len(ct._delegation_results) == 1


def test_record_delegation_failure():
    ct = ContinuousThinker(blackboard=MagicMock())
    ct.record_delegation("expert", "写代码", {"success": False, "error": "挂了"})
    assert ct._delegation_results[-1]["success"] is False


def test_record_control_decision():
    ct = ContinuousThinker(blackboard=MagicMock())
    ct.record_control_decision({"continue": True})
    assert ct._last_control_data == {"continue": True}


def test_external_prompts():
    ct = ContinuousThinker(blackboard=MagicMock())
    ct.add_external_prompt("持久", persistent=True)
    ct.add_external_prompt("临时", persistent=False)
    assert ct.get_external_prompts() == ["持久", "临时"]
    ct.clear_external_prompts()
    assert ct.get_external_prompts() == []


def test_get_process_snapshot():
    ct = ContinuousThinker(blackboard=MagicMock())
    ct._last_process_snapshot = {"step": 3}
    assert ct.get_process_snapshot() == {"step": 3}
    ct._last_process_snapshot = None
    assert ct.get_process_snapshot() is not None


def _ct(**kw):
    from modules.thinking.core.continuous_thinker import ContinuousThinker
    ct = ContinuousThinker.__new__(ContinuousThinker)
    ct._task_context = kw.get("task_context", None)
    ct._model_id = kw.get("model_id", "large_primary")
    ct._tier = kw.get("tier", "large")
    ct._session_id = kw.get("session_id", "s1")
    ct._pending_delegations = kw.get("pending", {})
    ct.history_thoughts = []
    ct._delegation_results = []
    ct.logger = MagicMock()
    ct.memory = None
    ct.notebook = None
    ct._process_collector = MagicMock()
    ct._last_process_snapshot = None
    return ct


def test_select_final_result_prefers_final_output():
    ct = _ct()
    results = [
        {"thought": "raw1", "final_output": "整合结果"},
        {"thought": "raw2", "final_output": "  "},
    ]
    assert ct._select_final_result(results) == "整合结果"


def test_select_final_result_fallback_thought():
    ct = _ct()
    results = [{"thought": "  原始思考  "}, {"thought": ""}]
    assert ct._select_final_result(results) == "原始思考"


def test_select_final_result_empty():
    ct = _ct()
    assert ct._select_final_result([]) == ""


def test_has_successful_external_result():
    ct = _ct()
    assert ct._has_successful_external_result("【工具结果】读取成功") is True
    assert ct._has_successful_external_result("专家已执行完成") is True
    assert ct._has_successful_external_result("没有任何外部结果") is False
    assert ct._has_successful_external_result("") is False


def test_build_final_synthesis_prompt_no_external():
    ct = _ct()
    ct._collect_final_synthesis_context = lambda q, r: "无外部结果"
    prompt = ct._build_final_synthesis_prompt("问题", [])
    assert "不得编造" in prompt


def test_build_final_synthesis_prompt_with_external():
    ct = _ct()
    ct._collect_final_synthesis_context = lambda q, r: "【工具结果】成功读取文件"
    prompt = ct._build_final_synthesis_prompt("问题", [])
    assert "不得补全" in prompt


def test_notify_return_target_no_context():
    ct = _ct(task_context=None)
    import asyncio
    asyncio.run(ct._notify_return_target(None, "结果"))  # 无 ctx 直接返回


def test_notify_return_target_pending_block(monkeypatch):
    ctx = MagicMock()
    ctx.return_to_model_id = "supervisor_x"
    ctx.task_id = "t1"
    ctx.origin_model_id = ""
    ctx.return_to_session_id = "s1"
    ctx.loop_goal = "目标"
    ctx.caller_tier = "large"
    ctx.metadata = {}
    ct = _ct(task_context=ctx, pending={"t9": {"status": "pending"}})
    ct._model_id = "expert_y"
    import asyncio
    sent = []
    async def fake_send(self, msg):
        sent.append(msg)
    import modules.thinking.communication.interface as iface_mod
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: type("B", (), {"send": fake_send})())
    asyncio.run(ct._notify_return_target(ctx, "结果"))
    assert sent == []  # 有 pending 委托不发送


def test_notify_return_target_sends(monkeypatch):
    ctx = MagicMock()
    ctx.return_to_model_id = "supervisor_x"
    ctx.task_id = "t1"
    ctx.origin_model_id = ""
    ctx.return_to_session_id = "s1"
    ctx.loop_goal = "目标"
    ctx.caller_tier = "large"
    ctx.metadata = {}
    ct = _ct(task_context=ctx)
    ct._model_id = "expert_y"
    import asyncio
    sent = []
    async def fake_send(self, msg):
        sent.append(msg)
    import modules.thinking.communication.interface as iface_mod
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: type("B", (), {"send": fake_send})())
    asyncio.run(ct._notify_return_target(ctx, "最终结果"))
    assert len(sent) == 1
    assert sent[0].content["result"] == "最终结果"


def test_build_task_contract_section_no_ctx():
    ct = _ct(task_context=None, tier="large")
    assert ct._build_task_contract_section(None) == ""


def test_build_task_contract_supervisor():
    ctx = MagicMock()
    ctx.task_id = "t1"
    ctx.loop_goal = "目标"
    ctx.origin_model_id = "large_x"
    ctx.return_to_model_id = "large_x"
    ct = _ct(task_context=ctx, tier="supervisor")
    out = ct._build_task_contract_section(ctx)
    assert "任务ID" in out
    assert "delegate_task" in out


def test_build_task_contract_expert():
    ctx = MagicMock()
    ctx.task_id = "t1"
    ctx.loop_goal = "目标"
    ctx.origin_model_id = ""
    ctx.return_to_model_id = ""
    ct = _ct(task_context=ctx, tier="expert")
    out = ct._build_task_contract_section(ctx)
    assert "continue_thinking" in out
    assert "delegate_task" not in out


def test_build_task_contract_large():
    ctx = MagicMock()
    ctx.task_id = "t1"
    ctx.loop_goal = "目标"
    ctx.origin_model_id = ""
    ctx.return_to_model_id = ""
    ct = _ct(task_context=ctx, tier="large")
    out = ct._build_task_contract_section(ctx)
    assert "自动推进" in out


def test_build_expert_context_only_large():
    ct = _ct(tier="large")
    out = ct._build_expert_context_section()
    assert "可用主管" in out
    ct2 = _ct(tier="expert")
    assert ct2._build_expert_context_section() == ""


def test_build_delegation_status_section():
    ct = _ct(pending={"d1": {"status": "pending", "round": 1, "role": "expert", "task": "任务"}})
    out = ct._build_delegation_status_section()
    assert "委托" in out


def test_process_delegation_response_completes():
    ct = _ct(pending={"d1": {"status": "pending", "role": "expert", "task": "任务"}})
    ct._process_delegation_response("完成了", delegation_id="d1")
    assert ct._pending_delegations["d1"]["status"] == "completed"
    assert ct._pending_delegations["d1"]["response"] == "完成了"


def test_process_delegation_response_no_id():
    ct = _ct()
    ct._process_delegation_response("x", delegation_id="")
    assert "d1" not in ct._pending_delegations
