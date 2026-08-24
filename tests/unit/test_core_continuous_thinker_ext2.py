"""core/continuous_thinker 补充测试：初始化 / 委托记录 / 循环错误 / 提示词分支"""
import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

import modules.thinking.core.continuous_thinker as ctc
from modules.thinking.core.continuous_thinker import ContinuousThinker
from modules.thinking.core.control_tools import ThinkingControlDecision


def _ct(monkeypatch):
    ct = ContinuousThinker.__new__(ContinuousThinker)
    ct.logger = MagicMock()
    ct._blackboard = MagicMock()
    ct.history_thoughts = []
    ct._model_id = "m1"
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
    ct._process_collector.complete = MagicMock(return_value="snap")
    ct._delegation_port = MagicMock()
    ct.notebook = MagicMock()
    ct.notebook.content = "任务刚开始，请制定初步计划。"
    ct.notebook.append = MagicMock()
    monkeypatch.setattr(ctc, "pausable_wait_for", lambda coro, timeout: coro)
    return ct


# ── 初始化 ─────────────────────────────────────────────────────────────

def test_init_memory_set_session_id(monkeypatch):
    mem = MagicMock()
    mem.set_session_id = MagicMock()

    class FakeNotebook:
        def __init__(self, sid):
            self.content = "任务刚开始，请制定初步计划。"
            self.clear = MagicMock()

    monkeypatch.setattr("modules.memory.utils.task_notebook.TaskNotebook", FakeNotebook)
    ct = ContinuousThinker(memory_manager=mem, session_id="s9", model_id="m", tier="large")
    mem.set_session_id.assert_called_once_with("s9")
    assert ct.notebook is not None


def test_init_notebook_failure(monkeypatch):
    def boom(*a, **k):
        raise ImportError("no task_notebook")
    monkeypatch.setattr("modules.memory.utils.task_notebook.TaskNotebook", boom)
    ct = ContinuousThinker(session_id="s9", model_id="m", tier="large")
    assert ct.notebook is None


def test_record_delegation_metadata_task_id():
    ct = ContinuousThinker.__new__(ContinuousThinker)
    ct.logger = MagicMock()
    ct.history_thoughts = []
    ct._pending_delegations = {}
    ct._delegation_results = []
    result = type("R", (), {"metadata": {"task_id": "t_meta"}})()
    ct.record_delegation("expert", "任务", result)
    assert "t_meta" in ct._pending_delegations


def test_record_delegation_failure_obj():
    ct = ContinuousThinker.__new__(ContinuousThinker)
    ct.logger = MagicMock()
    ct.history_thoughts = []
    ct._pending_delegations = {}
    ct._delegation_results = []
    result = type("R", (), {"success": False, "error": "失败"})()
    ct.record_delegation("expert", "任务", result)
    assert ct._delegation_results[0]["success"] is False


def test_clear_memory():
    ct = ContinuousThinker.__new__(ContinuousThinker)
    ct.logger = MagicMock()
    ct.memory = MagicMock()
    nb = MagicMock()
    nb.clear = MagicMock()
    ct.notebook = nb
    ct.clear_memory()
    ct.memory.clear_short_term.assert_called_once()
    nb.clear.assert_called_once()


def test_jaccard_short():
    assert ContinuousThinker._jaccard_similarity("短", "短") == 0.0


def test_strip_control_markers():
    out = ContinuousThinker._strip_control_markers("多行\n\n\n\n文本")
    assert "\n\n\n" not in out


# ── _notify_return_target ──────────────────────────────────────────────

async def test_notify_return_target_to_self(monkeypatch):
    ct = _ct(monkeypatch)
    ct._model_id = "m1"
    ctx = ctc.ThinkingTaskContext(task_id="t", loop_goal="g", origin_model_id="o", return_to_model_id="m1")
    await ct._notify_return_target(ctx, "结果")  # 返回自己 → 直接返回


async def test_notify_return_target_exception(monkeypatch):
    ct = _ct(monkeypatch)
    ct._model_id = "m1"
    ctx = ctc.ThinkingTaskContext(task_id="t", loop_goal="g", origin_model_id="o", return_to_model_id="other")
    monkeypatch.setattr("modules.thinking.communication.interface.get_message_bus_port", lambda: (_ for _ in ()).throw(RuntimeError("no bus")))
    await ct._notify_return_target(ctx, "结果")  # 不抛异常


# ── _run_final_synthesis 对话框 TypeError 路径 ─────────────────────────

async def test_run_final_synthesis_dialog_typeerror(monkeypatch):
    ct = _ct(monkeypatch)
    ct._blackboard = MagicMock()
    ct._blackboard.write_thought = MagicMock(side_effect=[TypeError("wrong sig"), MagicMock()])

    async def fake_think(prompt):
        return "总结"

    ct.think_fn = fake_think
    record = await ct._run_final_synthesis("q", [])
    assert record["final_output"] == "总结"


async def test_run_final_synthesis_dialog_exception(monkeypatch):
    ct = _ct(monkeypatch)
    ct._blackboard = MagicMock()
    ct._blackboard.write_thought = MagicMock(side_effect=RuntimeError("db fail"))

    async def fake_think(prompt):
        return "总结2"

    ct.think_fn = fake_think
    record = await ct._run_final_synthesis("q", [])
    assert record is not None


# ── _consume_external_guidance ─────────────────────────────────────────

def test_consume_external_guidance(monkeypatch):
    ct = _ct(monkeypatch)
    ct._persistent_prompts = ["持久"]
    ct._transient_prompts = ["临时"]
    mgr = MagicMock()
    mgr.build_external_guidance = MagicMock(return_value="合并引导")
    monkeypatch.setattr("modules.thinking.context.manager.ContextManager", mgr)
    out = ct._consume_external_guidance()
    assert out == "合并引导"
    assert ct._transient_prompts == []


# ── _build_prompt 分支 ─────────────────────────────────────────────────

async def test_build_prompt_with_notebook_and_history(monkeypatch):
    ct = _ct(monkeypatch)
    ct.notebook = MagicMock()
    ct.notebook.content = "记事本有进展了"
    ct.history_thoughts = ["旧思考1", "旧思考2"]
    ct._role = "orchestrator"
    ct._consume_external_guidance = lambda: ""
    ct._build_delegation_status_section = lambda: ""
    ct._external_prompt_builder = None
    ct._runner_ref = None
    ps = MagicMock()
    ps.collect = AsyncMock()
    ps.collect.return_value.content = ""
    monkeypatch.setattr("modules.thinking.context.sources.perception_source.PerceptionSource", lambda: ps)
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval", lambda: (_ for _ in ()).throw(RuntimeError("no")))
    composer = MagicMock()
    composer.build = MagicMock(return_value="最终prompt")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    prompt = await ct._build_prompt("问题")
    assert prompt == "最终prompt"


async def test_build_prompt_events_memory(monkeypatch):
    ct = _ct(monkeypatch)
    ct.notebook = None
    ct.history_thoughts = ["历史"]
    ct._role = "orchestrator"
    ct._consume_external_guidance = lambda: ""
    ct._build_delegation_status_section = lambda: ""
    ct._external_prompt_builder = None
    ct._runner_ref = None
    ev = MagicMock()
    ev.time = "2026-01-01T00:00:00"
    ev.type = "fact"
    ev.importance = 0.8
    ev.fact = "事件"
    ev.lesson = "经验"
    ev.keywords = ["k"]
    retrieval = MagicMock()
    retrieval.retrieve = AsyncMock(return_value=[ev])
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval", lambda: retrieval)
    ps = MagicMock()
    ps.collect = AsyncMock()
    ps.collect.return_value.content = ""
    monkeypatch.setattr("modules.thinking.context.sources.perception_source.PerceptionSource", lambda: ps)
    composer = MagicMock()
    composer.build = MagicMock(return_value="最终prompt")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    prompt = await ct._build_prompt("问题")
    assert prompt == "最终prompt"
    retrieval.retrieve.assert_awaited_once()


async def test_build_prompt_external_builder_async(monkeypatch):
    ct = _ct(monkeypatch)
    ct.notebook = None
    ct._role = "orchestrator"
    ct._consume_external_guidance = lambda: ""
    ct._build_delegation_status_section = lambda: ""
    ct._runner_ref = None
    ct._external_prompt_builder = AsyncMock(return_value="外部上下文")
    ps = MagicMock()
    ps.collect = AsyncMock()
    ps.collect.return_value.content = ""
    monkeypatch.setattr("modules.thinking.context.sources.perception_source.PerceptionSource", lambda: ps)
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval", lambda: (_ for _ in ()).throw(RuntimeError("no")))
    composer = MagicMock()
    composer.build = MagicMock(return_value="最终prompt")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    await ct._build_prompt("问题")
    composer.build.assert_called_once()


# ── continuous_think 循环异常 ──────────────────────────────────────────

async def test_continuous_think_loop_error(monkeypatch):
    ct = _ct(monkeypatch)
    ct._build_prompt = AsyncMock(side_effect=RuntimeError("prompt fail"))

    async def fake_think(prompt):
        return "x"

    ct.think_fn = fake_think
    ct._finalize_thinking_results = AsyncMock(return_value="")
    results = await ct.continuous_think("问题", max_rounds=3)
    assert results == []
    ct._finalize_thinking_results.assert_awaited()


async def test_continuous_think_finalize_error(monkeypatch):
    ct = _ct(monkeypatch)
    ct._build_prompt = AsyncMock(return_value="p")

    async def fake_think(prompt):
        return "x"

    ct.think_fn = fake_think

    async def boom(*a, **k):
        raise RuntimeError("finalize fail")

    ct._finalize_thinking_results = boom
    results = await ct.continuous_think("问题", max_rounds=1)
    assert results


# ── produce_intermediate_response ──────────────────────────────────────

def test_produce_intermediate_conclusion(monkeypatch):
    ct = ContinuousThinker.__new__(ContinuousThinker)
    ct.history_thoughts = ["【结论】\n这就是最终结论内容内容内容内容内容内容内容内容内容内容"]
    out = ct.produce_intermediate_response()
    assert out.startswith("[preliminary]")


# ── write_final_response ───────────────────────────────────────────────

def test_write_final_response_empty():
    ct = ContinuousThinker.__new__(ContinuousThinker)
    ct.logger = MagicMock()
    ct._blackboard = MagicMock()
    ct._model_id = "m"
    ct._tier = "large"
    ct.write_final_response("")  # 空内容跳过
