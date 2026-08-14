"""core/continuous_thinker 扩展测试：最终整合 / 提示词构建 / 循环控制 / 工具"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import modules.thinking.core.continuous_thinker as ctc
from modules.thinking.core.continuous_thinker import ContinuousThinker
from modules.thinking.core.control_tools import ThinkingControlDecision, ThinkingTaskContext


def _make_ct(monkeypatch):
    ct = ContinuousThinker.__new__(ContinuousThinker)
    ct.logger = MagicMock()
    ct._blackboard = MagicMock()
    ct.history_thoughts = []
    ct._model_id = "test_large"
    ct._tier = "large"
    ct.memory = None
    ct.think_fn = None
    ct.interval = 0.01
    ct._session_id = "s1"
    ct._task_context = None
    ct._last_control_decision = None
    ct._last_control_data = None
    ct._pending_delegations = {}
    ct._delegation_results = []
    ct._persistent_prompts = []
    ct._transient_prompts = []
    ct._memory_focus = None
    ct._external_prompt_builder = None
    ct._runner_ref = None
    ct._context_tokens = 0
    ct._context_window_size = 128000
    ct._supervisor_strict_retries = 0
    ct._consecutive_new_delegation_rounds = 0
    ct._last_sd_read_count = 0
    ct._last_process_snapshot = None
    ct._process_collector = MagicMock()
    ct._process_collector.reset = MagicMock()
    ct._process_collector.record_step = MagicMock()
    ct._process_collector.snapshot = MagicMock()
    ct._process_collector.complete = MagicMock(return_value="snap")
    ct._delegation_port = MagicMock()
    ct.notebook = MagicMock()
    ct.notebook.content = "任务刚开始，请制定初步计划。"
    ct.notebook.append = MagicMock()
    ct.notebook.clear = MagicMock()
    monkeypatch.setattr(ctc, "pausable_wait_for", lambda coro, timeout: coro)
    return ct


def _ctx(**kw):
    return ThinkingTaskContext(
        task_id="t1", loop_goal="目标", origin_model_id="m1",
        return_to_model_id=kw.get("return_to_model_id", ""),
        return_to_session_id=kw.get("return_to_session_id", ""),
        caller_tier="large",
        metadata={"identity_key": "large_primary"},
    )


# ── _run_final_synthesis ───────────────────────────────────────────────

async def test_run_final_synthesis_success(monkeypatch):
    ct = _make_ct(monkeypatch)
    ct._blackboard.write_thought = MagicMock(return_value=MagicMock())

    async def fake_think(prompt):
        return "最终总结文本"

    ct.think_fn = fake_think
    record = await ct._run_final_synthesis("问题", [{"thought": "r1"}])
    assert record["is_final_synthesis"] is True
    assert record["final_output"] == "最终总结文本"
    assert ct.history_thoughts == ["最终总结文本"]
    ct.notebook.append.assert_called_once()
    ct._blackboard.write_thought.assert_called_once()
    ct._process_collector.record_step.assert_called_once()


async def test_run_final_synthesis_no_think_fn(monkeypatch):
    ct = _make_ct(monkeypatch)
    ct.think_fn = None
    assert await ct._run_final_synthesis("q", []) is None


async def test_run_final_synthesis_error(monkeypatch):
    ct = _make_ct(monkeypatch)

    async def boom(prompt):
        raise RuntimeError("fail")

    ct.think_fn = boom
    assert await ct._run_final_synthesis("q", []) is None


# ── _finalize_thinking_results ─────────────────────────────────────────

async def test_finalize_large_with_pending(monkeypatch):
    ct = _make_ct(monkeypatch)
    ct._tier = "large"
    ct._pending_delegations = {"d1": {"status": "pending"}}
    ct._last_control_decision = None
    out = await ct._finalize_thinking_results("q", [{"thought": "x", "final_output": "结果"}])
    assert out == "结果"


async def test_finalize_with_result_summary(monkeypatch):
    ct = _make_ct(monkeypatch)
    ct._last_control_decision = ThinkingControlDecision(should_continue=False, result_summary="模型给的结果")
    out = await ct._finalize_thinking_results("q", [])
    assert out == "模型给的结果"


async def test_finalize_empty(monkeypatch):
    ct = _make_ct(monkeypatch)
    ct._last_control_decision = None
    out = await ct._finalize_thinking_results("q", [])
    assert out == ""


# ── _collect_final_synthesis_context ───────────────────────────────────

def test_collect_final_synthesis_context(monkeypatch):
    ct = _make_ct(monkeypatch)
    ct._tier = "large"
    ct.notebook = None
    out = ct._collect_final_synthesis_context("q", [
        {"thought": "思考内容"}, "not dict", {"thought": ""},
    ])
    assert "本次内部思考摘要" in out


def test_collect_final_context_with_notebook(monkeypatch):
    ct = _make_ct(monkeypatch)
    ct._tier = "expert"
    ct.notebook = MagicMock()
    ct.notebook.content = "记事本有进展"
    out = ct._collect_final_synthesis_context("q", [])
    assert "记事本状态" in out


# ── produce_intermediate_response ──────────────────────────────────────

def test_produce_intermediate_empty():
    ct = ContinuousThinker.__new__(ContinuousThinker)
    ct.history_thoughts = []
    assert ct.produce_intermediate_response() == ""


def test_produce_intermediate_structured(monkeypatch):
    ct = ContinuousThinker.__new__(ContinuousThinker)
    ct.history_thoughts = [
        "先前的短思考",
        "【回答】\n这是最终的回答内容内容内容内容内容内容内容内容内容\n\n【建议】\n另一个建议",
    ]
    engine = MagicMock()
    engine._truncate_to_tokens = MagicMock(side_effect=lambda t, m: t)
    monkeypatch.setattr("modules.thinking.context.compression.get_compression_engine", lambda: engine)
    out = ct.produce_intermediate_response()
    assert out.startswith("[preliminary]")


def test_produce_intermediate_fallback(monkeypatch):
    ct = ContinuousThinker.__new__(ContinuousThinker)
    ct.history_thoughts = ["这是一个很长的思考过程，包含了多个段落内容，用于测试回退路径是否正常工作。"]
    engine = MagicMock()
    engine._truncate_to_tokens = MagicMock(side_effect=lambda t, m: t)
    monkeypatch.setattr("modules.thinking.context.compression.get_compression_engine", lambda: engine)
    out = ct.produce_intermediate_response()
    assert out.startswith("[preliminary]")


def test_produce_intermediate_short_only():
    ct = ContinuousThinker.__new__(ContinuousThinker)
    ct.history_thoughts = ["太短", "也不够长"]
    assert ct.produce_intermediate_response() == ""


# ── 生命周期工具 ───────────────────────────────────────────────────────

def test_reset_for_continuation():
    ct = ContinuousThinker.__new__(ContinuousThinker)
    ct.logger = MagicMock()
    ct.history_thoughts = [f"h{i}" for i in range(10)]
    ct._consecutive_new_delegation_rounds = 5
    ct.reset_for_continuation()
    assert ct._consecutive_new_delegation_rounds == 0
    assert len(ct.history_thoughts) == 5


def test_write_final_response(monkeypatch):
    ct = _make_ct(monkeypatch)
    ct._blackboard.write_response = MagicMock(return_value=MagicMock())
    ct.write_final_response("最终回复")
    ct._blackboard.write_response.assert_called_once()
    ct2 = _make_ct(monkeypatch)
    ct2.write_final_response("")  # 空内容跳过


async def test_close_and_context_manager(monkeypatch):
    ct = _make_ct(monkeypatch)
    ct.memory = MagicMock()
    ct._persistent_prompts = ["p"]
    await ct.close()
    ct.memory.clear_short_term.assert_called_once()
    assert ct._persistent_prompts == []
    async with ct as cm:
        assert cm is ct


# ── deep_think ─────────────────────────────────────────────────────────

def test_deep_think_no_loop(monkeypatch):
    ct = _make_ct(monkeypatch)
    ct.continuous_think = AsyncMock(return_value=[{"thought": "x"}])
    out = ct.deep_think("q", max_rounds=3)
    assert out == [{"thought": "x"}]


def test_deep_think_with_running_loop(monkeypatch):
    ct = _make_ct(monkeypatch)
    ct.continuous_think = AsyncMock(return_value=[{"thought": "y"}])
    async def run():
        return ct.deep_think("q", max_rounds=2)
    out = asyncio.run(run())
    assert out == [{"thought": "y"}]


# ── _build_prompt 扩展 ─────────────────────────────────────────────────

async def test_build_prompt_with_memory_focus(monkeypatch):
    ct = _make_ct(monkeypatch)
    ct.notebook = None
    ct._role = "orchestrator"
    ct._memory_focus = {"主题": 1.0}
    ct._consume_external_guidance = lambda: ""
    ct._build_delegation_status_section = lambda: ""
    ct._external_prompt_builder = None
    ct._runner_ref = None

    ev = MagicMock()
    ev.time = "2026-01-01T00:00:00"
    ev.type = "fact"
    ev.importance = 0.8
    ev.fact = "发生过的事"
    ev.lesson = "经验"
    ev.keywords = ["k"]
    retrieval = MagicMock()
    retrieval.retrieve_mixed = AsyncMock(return_value=[ev])
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval", lambda: retrieval)
    ps = MagicMock()
    ps.collect = AsyncMock()
    ps.collect.return_value.content = ""
    monkeypatch.setattr("modules.thinking.context.sources.perception_source.PerceptionSource", lambda: ps)
    import modules.thinking.skills as sk_mod
    monkeypatch.setattr(sk_mod.skill_manager, "match_skill", lambda q: None)

    import modules.thinking.core.continuous_thinker as mod
    composer = MagicMock()
    composer.build = MagicMock(return_value="合成prompt")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)

    prompt = await ct._build_prompt("问题")
    assert prompt == "合成prompt"
    retrieval.retrieve_mixed.assert_awaited_once()


async def test_build_prompt_with_forced_skill(monkeypatch):
    ct = _make_ct(monkeypatch)
    ct.notebook = None
    ct._role = "orchestrator"
    ct._consume_external_guidance = lambda: ""
    ct._build_delegation_status_section = lambda: ""
    ct._external_prompt_builder = None
    ct._runner_ref = None
    ps = MagicMock()
    ps.collect = AsyncMock()
    ps.collect.return_value.content = ""
    monkeypatch.setattr("modules.thinking.context.sources.perception_source.PerceptionSource", lambda: ps)

    skill = MagicMock()
    skill.id = "code_review"
    skill.name = "代码审查"
    skill.enabled = True
    import modules.thinking.skills as sk_mod
    monkeypatch.setattr(sk_mod.skill_manager, "get_skill", lambda sid: skill)
    import sys, types
    cfg = sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(get_forced_skill=lambda: "code_review"))
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval", lambda: (_ for _ in ()).throw(RuntimeError("no")))

    composer = MagicMock()
    composer.build = MagicMock(return_value="合成prompt")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    prompt = await ct._build_prompt("问题")
    assert "合成prompt" in prompt


# ── continuous_think 循环控制 ─────────────────────────────────────────

async def test_continuous_think_control_stop(monkeypatch):
    ct = _make_ct(monkeypatch)
    ct._build_prompt = AsyncMock(return_value="prompt")
    ct._role = "orchestrator"
    ct._notify_return_target = AsyncMock()
    called = {"n": 0}

    async def fake_think(prompt):
        if called["n"] == 0:
            ct.record_control_decision({"continue": False, "reason": "完成", "result_summary": "总结"})
        called["n"] += 1
        return "思考内容"

    ct.think_fn = fake_think
    results = await ct.continuous_think("问题", max_rounds=5)
    assert results
    assert ct._last_control_decision.result_summary == "总结"


async def test_continuous_think_delegation_stops(monkeypatch):
    ct = _make_ct(monkeypatch)
    ct._build_prompt = AsyncMock(return_value="prompt")
    ct._notify_return_target = AsyncMock()
    called = {"n": 0}

    async def fake_think(prompt):
        if called["n"] == 0:
            ct.record_delegation("expert", "写代码", {"success": True, "task_id": "t1"})
        called["n"] += 1
        return "需要委托"

    ct.think_fn = fake_think
    results = await ct.continuous_think("问题", max_rounds=5)
    assert results
    assert ct._last_control_decision is not None


async def test_continuous_think_callback_error(monkeypatch):
    ct = _make_ct(monkeypatch)
    ct._build_prompt = AsyncMock(return_value="prompt")

    async def fake_think(prompt):
        return "普通思考"

    ct.think_fn = fake_think

    async def bad_cb(result):
        raise RuntimeError("cb boom")

    results = await ct.continuous_think("问题", max_rounds=1, callback=bad_cb)
    assert results


async def test_continuous_think_stopped_flag(monkeypatch):
    ct = _make_ct(monkeypatch)
    ct._build_prompt = AsyncMock(return_value="prompt")
    ct._running = False
    results = await ct.continuous_think("问题", max_rounds=5)
    assert results == []


# ── think_once 超时 ────────────────────────────────────────────────────

async def test_think_once_timeout_all(monkeypatch):
    ct = _make_ct(monkeypatch)
    ct._blackboard.write_thought = MagicMock(return_value=MagicMock())

    async def never(prompt):
        raise asyncio.TimeoutError()

    ct.think_fn = never
    monkeypatch.setattr(ctc, "pausable_wait_for", lambda coro, timeout: coro)
    result = await ct.think_once("ctx")
    assert result["is_finished"] is True
    assert "超时" in result["thought"]
