"""工作流式思维内核 (WorkflowThinker) 单元测试

验证工作流范式下的各状态节点与执行逻辑与原有 ContinuousThinker 完全等价。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import modules.thinking.core.workflow_thinker as wft_mod
from modules.thinking.core.workflow_thinker import WorkflowThinker, WorkflowThinkingState


def _run(coro):
    return asyncio.run(coro)


def _make_wt(monkeypatch):
    wt = WorkflowThinker.__new__(WorkflowThinker)
    wt.logger = MagicMock()
    wt._blackboard = MagicMock()
    wt.history_thoughts = []
    wt._model_id = "test_large"
    wt._tier = "large"
    wt.memory = MagicMock()
    monkeypatch.setattr(wft_mod, "pausable_wait_for", lambda coro, timeout, **kwargs: coro)
    return wt


def test_workflow_think_once_returns_thought(monkeypatch):
    wt = _make_wt(monkeypatch)

    async def fake_think(prompt):
        return "工作流深度思考结果"

    wt.think_fn = fake_think
    result = _run(wt.think_once("上下文"))
    assert result["thought"] == "工作流深度思考结果"
    assert result["duration_ms"] >= 0
    assert "error" not in result


def test_workflow_think_once_without_think_fn():
    wt = WorkflowThinker.__new__(WorkflowThinker)
    wt.logger = MagicMock()
    wt.think_fn = None
    result = _run(wt.think_once("上下文"))
    assert result["thought"] == ""
    assert "error" in result
    wt.logger.warning.assert_called()


def test_workflow_think_once_retries_on_error(monkeypatch):
    wt = _make_wt(monkeypatch)
    calls = {"n": 0}

    async def failing_think(prompt):
        calls["n"] += 1
        raise RuntimeError("模型超时")

    wt.think_fn = failing_think
    result = _run(wt.think_once("上下文"))
    assert calls["n"] >= 1
    assert "思考异常" in result["thought"] or result["thought"] == ""


def test_workflow_build_prompt_contains_question(monkeypatch):
    wt = _make_wt(monkeypatch)
    wt.notebook = None
    wt._memory_focus = None
    wt._session_id = "s1"
    wt._external_prompt_builder = None
    wt._runner_ref = None
    wt._persistent_prompts = []
    wt._transient_prompts = []

    # Mock 感知收集
    import modules.thinking.context.sources.perception_source as ps_mod
    monkeypatch.setattr(
        ps_mod, "PerceptionSource",
        lambda: type("P", (), {"collect": lambda: type("F", (), {"content": ""})()})(),
    )

    prompt = _run(wt._build_prompt("工作流为什么高效"))
    assert "工作流为什么高效" in prompt
    assert prompt.strip() != ""


def test_workflow_jaccard_similarity():
    assert WorkflowThinker._jaccard_similarity("你好", "你好") == 0.0
    assert WorkflowThinker._jaccard_similarity("完全相同的一段文字", "完全相同的一段文字") == 1.0
    assert 0.0 < WorkflowThinker._jaccard_similarity("abcdefghij", "abcdefghxx") < 1.0


def test_workflow_record_delegation():
    wt = WorkflowThinker(blackboard=MagicMock())
    wt.record_delegation("expert", "编写工作流节点", {"task_id": "t1", "success": True})
    assert "t1" in wt._pending_delegations
    assert wt._pending_delegations["t1"]["status"] == "pending"
    assert len(wt._delegation_results) == 1

    wt.record_delegation("expert", "无效角色", {"success": False, "error": "失败"})
    assert wt._delegation_results[-1]["success"] is False


def test_workflow_record_control_decision():
    wt = WorkflowThinker(blackboard=MagicMock())
    wt.record_control_decision({"continue": False, "result_summary": "已完成"})
    assert wt._last_control_data == {"continue": False, "result_summary": "已完成"}


def test_workflow_external_prompts():
    wt = WorkflowThinker(blackboard=MagicMock())
    wt.add_external_prompt("系统引导", persistent=True)
    wt.add_external_prompt("临时提示", persistent=False)
    assert wt.get_external_prompts() == ["系统引导", "临时提示"]
    wt.clear_external_prompts()
    assert wt.get_external_prompts() == []


def test_workflow_select_final_result():
    wt = WorkflowThinker(blackboard=MagicMock())
    results = [
        {"thought": "raw1", "final_output": "最终汇总"},
        {"thought": "raw2", "final_output": ""},
    ]
    assert wt._select_final_result(results) == "最终汇总"


def test_workflow_continuous_think_single_round():
    wt = WorkflowThinker(think_fn=AsyncMock(return_value="一轮结论"), max_rounds=1)
    wt._node_context_prep = AsyncMock()
    results = _run(wt.continuous_think("测试问题"))
    assert len(results) == 1
    assert results[0]["thought"] == "一轮结论"


def test_workflow_continuous_think_text_continue_false():
    wt = WorkflowThinker(think_fn=AsyncMock(), max_rounds=5)
    wt.think_once = AsyncMock(return_value={"thought": '思考完成 "continue": false', "duration_ms": 10})
    wt._node_context_prep = AsyncMock()
    results = _run(wt.continuous_think("测试问题"))
    assert len(results) == 1  # 文本标记终止


def test_workflow_continuous_think_control_decision_stops():
    wt = WorkflowThinker(think_fn=AsyncMock(), max_rounds=5)

    async def fake_think(prompt, q=""):
        wt._last_control_data = {"continue": False, "result_summary": "工作流达成"}
        return {"thought": "执行结束", "duration_ms": 10}

    wt.think_once = fake_think
    wt._node_context_prep = AsyncMock()
    results = _run(wt.continuous_think("测试问题"))
    assert wt._node_context_prep.await_count == 1
    assert wt._select_final_result(results) == "工作流达成"


def test_workflow_continuous_think_delegation_stops():
    wt = WorkflowThinker(think_fn=AsyncMock(), max_rounds=5)

    async def fake_think(prompt, q=""):
        wt._delegation_results.append({"role": "expert", "task": "subtask", "success": True})
        return {"thought": "已委托子工作流", "duration_ms": 10}

    wt.think_once = fake_think
    wt._node_context_prep = AsyncMock()
    results = _run(wt.continuous_think("测试问题"))
    assert len(results) == 1
    assert wt._last_control_decision is not None
    assert wt._last_control_decision.should_continue is False
