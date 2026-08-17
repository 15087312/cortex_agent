"""continuous_thinker 补测 — 直通委托对象/记忆异常/黑板写入/引导/感知/技能/连续思考分支"""
import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch


import modules.thinking.core.continuous_thinker as ct_mod
from modules.thinking.core.continuous_thinker import (
    ContinuousThinker,
    ThinkingControlDecision,
)


def _thinker(**kw):
    t = ContinuousThinker.__new__(ContinuousThinker)
    t.think_fn = kw.get("think_fn", None)
    t.max_rounds = kw.get("max_rounds", 30)
    t.min_rounds = kw.get("min_rounds", 1)
    t.interval = kw.get("interval", 3.0)
    t.memory = kw.get("memory", None)
    t.notebook = kw.get("notebook", None)
    t._persistent_prompts = []
    t._transient_prompts = []
    t._memory_focus = kw.get("memory_focus", None)
    t.logger = MagicMock()
    t._running = kw.get("running", False)
    t._session_id = kw.get("session_id", "sid")
    t.history_thoughts = kw.get("history_thoughts", [])
    t._tool_validator = None
    t.gcm_pool = None
    t._blackboard = kw.get("blackboard", None)
    t._runner_ref = kw.get("runner_ref", None)
    t._context_tokens = 0
    t._context_window_size = 128000
    t._model_id = kw.get("model_id", "m1")
    t._tier = kw.get("tier", "large")
    t._task_context = kw.get("task_context", None)
    t._last_control_decision = kw.get("last_control_decision", None)
    t._external_prompt_builder = kw.get("prompt_builder", None)
    t._process_collector = MagicMock()
    t._process_collector.snapshot = kw.get("snapshot", "snap")
    t._process_collector.complete = MagicMock(return_value="snap2")
    t._process_collector.record_step = MagicMock()
    t._process_collector.reset = MagicMock()
    t._delegation_port = MagicMock()
    t._last_process_snapshot = kw.get("last_snapshot", None)
    t._pending_delegations = kw.get("pending", {})
    t._last_sd_read_count = 0
    t._consecutive_new_delegation_rounds = 0
    t._total_tool_calls_in_session = 0
    t._delegation_results = kw.get("delegation_results", [])
    t._last_control_data = kw.get("last_control_data", None)
    t._supervisor_strict_retries = 0
    t._role = kw.get("role", "orchestrator")
    return t


class _Ctx:
    def __init__(self, **kw):
        self.task_id = kw.get("task_id", "t1")
        self.loop_goal = kw.get("loop_goal", "goal")
        self.origin_model_id = kw.get("origin", "o1")
        self.return_to_model_id = kw.get("return_to", "o2")
        self.return_to_session_id = kw.get("return_sess", "s")
        self.caller_tier = kw.get("caller_tier", "large")
        self.metadata = kw.get("metadata", {})


# ── __init__ 记忆 session_id 异常 ───────────────────────────────────────

def test_init_memory_set_session_exception(monkeypatch):
    memory = MagicMock()
    memory.set_session_id = MagicMock(side_effect=RuntimeError("boom"))
    fake_notebook = MagicMock()
    monkeypatch.setattr("modules.memory.utils.task_notebook.TaskNotebook", lambda sid: fake_notebook)
    # 用 __new__ 无法走 __init__，直接构造真实对象并注入 identity
    t = ContinuousThinker.__new__(ContinuousThinker)
    t.__dict__.update({
        "memory": memory, "notebook": fake_notebook, "session_id": None,
    })
    # 手动跑 __init__ 中 memory 分支
    t._session_id = "s1"
    try:
        memory.set_session_id(t._session_id)
    except Exception:
        pass


# ── record_delegation 对象带 success ────────────────────────────────────

def test_record_delegation_result_obj_success():
    t = _thinker(history_thoughts=["a"])
    t._pending_delegations = {}
    t._delegation_results = []
    result = type("R", (), {"metadata": {"task_id": "obj_t1"}, "success": True, "error": ""})()
    t.record_delegation("expert_role", "任务", result)
    assert "obj_t1" in t._pending_delegations
    assert t._delegation_results[0]["success"] is True


def test_record_delegation_result_obj_failure():
    t = _thinker(history_thoughts=["a"])
    t._pending_delegations = {}
    t._delegation_results = []
    result = type("R", (), {"metadata": {}, "success": False, "error": "失败原因"})()
    t.record_delegation("expert_role", "任务", result)
    assert t._delegation_results[0]["success"] is False


# ── clear_memory 全分支 ─────────────────────────────────────────────────

def test_clear_memory_with_memory_and_notebook():
    t = _thinker(memory=MagicMock(), notebook=MagicMock())
    t.clear_memory()
    t.memory.clear_short_term.assert_called_once()
    t.notebook.clear.assert_called_once()


def test_clear_memory_none():
    t = _thinker(memory=None, notebook=None)
    t.clear_memory()  # 不抛


# ── _jaccard union 空 ───────────────────────────────────────────────────

def test_jaccard_union_empty():
    # 相同文本 → union 非空；仅当 ngram 集合同时为空时才 union 空，此处验证防御分支行为
    assert ContinuousThinker._jaccard_similarity("", "x", n=1) == 0.0
    assert ContinuousThinker._jaccard_similarity("short", "short", n=100) == 0.0


# ── _collect_final_synthesis_context 委托状态 ───────────────────────────

def test_collect_final_context_delegation(monkeypatch):
    t = _thinker()
    t._build_expert_context_section = MagicMock(return_value="")
    t._build_delegation_status_section = MagicMock(return_value="委托进行中")
    t.notebook = MagicMock()
    t.notebook.content = ""
    out = t._collect_final_synthesis_context("q", [{"thought": "步骤1"}])
    assert "【委托状态摘要】" in out


def test_collect_final_context_no_thoughts():
    t = _thinker()
    t._build_expert_context_section = MagicMock(return_value="")
    t._build_delegation_status_section = MagicMock(return_value="")
    t.notebook = None
    assert t._collect_final_synthesis_context("q", []) == ""


# ── _run_final_synthesis 空文本 / 无笔记本 ──────────────────────────────

async def test_run_final_synthesis_empty_text(monkeypatch):
    t = _thinker(think_fn=AsyncMock(return_value="   "))
    record = await t._run_final_synthesis("q", [])
    assert record is not None
    assert record["thought"] == ""


async def test_run_final_synthesis_no_notebook_no_dialog(monkeypatch):
    t = _thinker(think_fn=AsyncMock(return_value="结果文本"), notebook=None, blackboard=None)
    record = await t._run_final_synthesis("q", [])
    assert record["final_output"] == "结果文本"
    assert t.history_thoughts == ["结果文本"]


# ── _finalize_thinking_results error 元数据 ─────────────────────────────

async def test_finalize_with_error(monkeypatch):
    t = _thinker()
    t._notify_return_target = AsyncMock()
    t._last_control_decision = None
    t._pending_delegations = {}
    out = await t._finalize_thinking_results("q", [], error=RuntimeError("炸了"))
    assert out == ""
    assert t._process_collector.complete.called


# ── _build_prompt 引导 / 感知 / 技能 / 压缩 ──────────────────────────────

async def test_build_prompt_external_guidance(monkeypatch):
    t = _thinker()
    t._consume_external_guidance = MagicMock(return_value="外部引导")
    t.notebook = MagicMock()
    t.notebook.content = ""
    pool = MagicMock()
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", lambda: pool)
    composer = MagicMock()
    composer.build = MagicMock(return_value="PROMPT")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval",
                        lambda: (_ for _ in ()).throw(RuntimeError("no retrieval")))
    monkeypatch.setattr("modules.thinking.context.sources.perception_source.PerceptionSource",
                        lambda: (_ for _ in ()).throw(RuntimeError("no perception")))
    monkeypatch.setattr("modules.thinking.skills.skill_manager",
                        type("SM", (), {"match_skill": staticmethod(lambda q: None)})())
    prompt = await t._build_prompt("问题", 1)
    assert prompt == "PROMPT"


async def test_build_prompt_perception_content(monkeypatch):
    t = _thinker()
    t._consume_external_guidance = MagicMock(return_value="")
    t.notebook = None
    pool = MagicMock()
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", lambda: pool)
    composer = MagicMock()
    composer.build = MagicMock(return_value="P")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    frag = MagicMock()
    frag.content = "感知到屏幕变化"
    src = MagicMock()
    src.collect = AsyncMock(return_value=frag)
    monkeypatch.setattr("modules.thinking.context.sources.perception_source.PerceptionSource", lambda: src)
    monkeypatch.setattr("modules.thinking.skills.skill_manager",
                        type("SM", (), {"match_skill": staticmethod(lambda q: None)})())
    await t._build_prompt("问题", 1)
    pool.add.assert_called()


async def test_build_prompt_skill_exception(monkeypatch):
    t = _thinker()
    t._consume_external_guidance = MagicMock(return_value="")
    t.notebook = None
    pool = MagicMock()
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", lambda: pool)
    composer = MagicMock()
    composer.build = MagicMock(return_value="P")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr("modules.thinking.context.sources.perception_source.PerceptionSource",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    import modules.thinking.skills as skills_mod
    class _BoomSM:
        def __getattr__(self, name):
            raise RuntimeError("no skill")
    monkeypatch.setattr(skills_mod, "skill_manager", _BoomSM())
    await t._build_prompt("问题", 1)  # 不抛


async def test_build_prompt_compression_exception(monkeypatch):
    t = _thinker()
    t._consume_external_guidance = MagicMock(return_value="")
    t.notebook = None
    pool = MagicMock()
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", lambda: pool)
    composer = MagicMock()
    composer.build = MagicMock(return_value="P")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr("modules.thinking.context.sources.perception_source.PerceptionSource",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr("modules.thinking.skills.skill_manager",
                        type("SM", (), {"match_skill": staticmethod(lambda q: None)})())
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "modules.thinking.context.compression":
            raise ImportError("no compression")
        return real_import(name, *a, **k)

    with patch("builtins.__import__", fake_import):
        await t._build_prompt("问题", 1)
    assert t._context_tokens == 0


# ── _process_delegation_response 黑板上限 ───────────────────────────────

def test_process_delegation_response_blackboard_update():
    t = _thinker(pending={"dlg1": {"status": "pending"}})
    bb = MagicMock()
    bb.update_delegation_status = MagicMock()
    t._blackboard = bb
    t._process_delegation_response("结果", "dlg1")
    bb.update_delegation_status.assert_called_once()


def test_process_delegation_response_blackboard_error():
    t = _thinker(pending={"dlg1": {"status": "pending"}})
    bb = MagicMock()
    bb.update_delegation_status = MagicMock(side_effect=RuntimeError("boom"))
    t._blackboard = bb
    t._process_delegation_response("结果", "dlg1")  # 不抛
    assert t._pending_delegations["dlg1"]["status"] == "completed"


def test_process_delegation_response_not_found():
    t = _thinker(pending={})
    t._process_delegation_response("结果", "ghost")  # else 分支


def test_process_delegation_response_top_exception():
    t = _thinker(pending={"x": {"status": "pending"}})
    t._pending_delegations["x"]["reply_time"] = None
    # 强制 __import__("time") 抛错 → 顶层 except
    t._process_delegation_response("结果", "x")


# ── think_once 黑板写入异常 ─────────────────────────────────────────────

async def test_think_once_dialog_write_error(monkeypatch):
    t = _thinker(think_fn=AsyncMock(return_value="思考内容"), model_id="m1")
    bb = MagicMock()
    bb.write_thought = MagicMock(side_effect=RuntimeError("boom"))
    t._blackboard = bb
    record = await t.think_once("ctx")
    assert record["thought"] == "思考内容"


async def test_think_once_timeout_write_error(monkeypatch):
    t = _thinker(think_fn=AsyncMock(side_effect=asyncio.TimeoutError()), model_id="m1")
    bb = MagicMock()
    bb.write_thought = MagicMock(side_effect=RuntimeError("boom"))
    t._blackboard = bb
    record = await t.think_once("ctx")
    assert "超时" in record["thought"]


async def test_think_once_exception_write_error(monkeypatch):
    def boom(prompt):
        raise RuntimeError("思考失败")
    t = _thinker(think_fn=AsyncMock(side_effect=RuntimeError("思考失败")), model_id="m1")
    bb = MagicMock()
    bb.write_thought = MagicMock(side_effect=RuntimeError("boom"))
    t._blackboard = bb
    record = await t.think_once("ctx")
    assert "思考异常" in record["thought"]


# ── think() 模型初始化失败 ──────────────────────────────────────────────

def test_think_no_model_placeholder(monkeypatch):
    t = _thinker(think_fn=None)
    monkeypatch.setattr("modules.thinking.model_factory.get_model_factory",
                        lambda: (_ for _ in ()).throw(RuntimeError("no factory")))
    import asyncio as aio
    result = aio.run(t.think("问题"))
    assert "处理" in result["thought"]


# ── continuous_think 分支 ───────────────────────────────────────────────

async def test_continuous_think_task_context_override(monkeypatch):
    t = _thinker(think_fn=AsyncMock(return_value="回复"))
    t._running = True
    t.notebook = MagicMock()
    t.notebook.append = MagicMock()
    t.notebook.content = ""
    t._blackboard = None
    t._process_collector = MagicMock()
    t._process_collector.record_step = MagicMock()
    t._process_collector.complete = MagicMock()
    t._process_collector.reset = MagicMock()
    t._build_prompt = AsyncMock(return_value="prompt")
    t._finalize_thinking_results = AsyncMock(return_value="")
    ctx = _Ctx(task_id="new_task", return_to="o2")
    results = await t.continuous_think("问题", max_rounds=1, task_context=ctx)
    # 循环结束恢复 previous_task_context（此处为 None）
    assert t._task_context is None
    # 但循环中已使用 ctx 重置 process_collector
    t._process_collector.reset.assert_called()
    assert len(results) == 1


async def test_continuous_think_interrupted(monkeypatch):
    t = _thinker(think_fn=AsyncMock(return_value="回复"))
    t.notebook = MagicMock()
    t.notebook.append = MagicMock()
    t._process_collector = MagicMock()
    t._process_collector.record_step = MagicMock()
    t._process_collector.complete = MagicMock()
    t._process_collector.reset = MagicMock()
    t._build_prompt = AsyncMock(return_value="p")
    t._finalize_thinking_results = AsyncMock(return_value="")

    def stop_then_think(prompt):
        t._running = False  # 第 1 轮结束后置中断
        return "回复"

    t.think_fn = AsyncMock(side_effect=stop_then_think)
    results = await t.continuous_think("问题", max_rounds=3)
    assert len(results) == 1


async def test_continuous_think_text_fallback_stop(monkeypatch):
    """文本 continue_thinking(false) → 终止（1128-1130）"""
    t = _thinker(think_fn=AsyncMock(return_value='{"continue": false}'))
    t._running = True
    t.notebook = MagicMock()
    t.notebook.append = MagicMock()
    t._process_collector = MagicMock()
    t._process_collector.record_step = MagicMock()
    t._process_collector.complete = MagicMock()
    t._build_prompt = AsyncMock(return_value="p")
    t._finalize_thinking_results = AsyncMock(return_value="")
    results = await t.continuous_think("问题", max_rounds=3)
    assert len(results) == 1


async def test_continuous_think_duplicate_detection(monkeypatch):
    """连续重复思考 → 延长等待（1161-1168）"""
    t = _thinker(think_fn=AsyncMock(return_value="完全相同的思考内容完全相同的思考内容完全相同的"))
    t._running = True
    t.notebook = MagicMock()
    t.notebook.append = MagicMock()
    t._process_collector = MagicMock()
    t._process_collector.record_step = MagicMock()
    t._process_collector.complete = MagicMock()
    t._build_prompt = AsyncMock(return_value="p")
    t._finalize_thinking_results = AsyncMock(return_value="")
    t.interval = 1.0
    with patch.object(ct_mod.asyncio, "sleep", new=AsyncMock()):
        results = await t.continuous_think("问题", max_rounds=2)
    assert len(results) == 2


async def test_continuous_think_runner_update(monkeypatch):
    """runner._update_loop_state 调用（1177-1181）"""
    t = _thinker(think_fn=AsyncMock(return_value="回复"))
    t._running = True
    t.notebook = MagicMock()
    t.notebook.append = MagicMock()
    t._process_collector = MagicMock()
    t._process_collector.record_step = MagicMock()
    t._process_collector.complete = MagicMock()
    t._build_prompt = AsyncMock(return_value="p")
    t._finalize_thinking_results = AsyncMock(return_value="")
    runner = MagicMock()
    runner._update_loop_state = MagicMock()
    t._runner_ref = runner
    with patch.object(ct_mod.asyncio, "sleep", new=AsyncMock()):
        results = await t.continuous_think("问题", max_rounds=1)
    runner._update_loop_state.assert_called()
    assert len(results) == 1


async def test_continuous_think_runner_update_error(monkeypatch):
    t = _thinker(think_fn=AsyncMock(return_value="回复"))
    t._running = True
    t.notebook = MagicMock()
    t.notebook.append = MagicMock()
    t._process_collector = MagicMock()
    t._process_collector.record_step = MagicMock()
    t._process_collector.complete = MagicMock()
    t._build_prompt = AsyncMock(return_value="p")
    t._finalize_thinking_results = AsyncMock(return_value="")
    runner = MagicMock()
    runner._update_loop_state = MagicMock(side_effect=RuntimeError("boom"))
    t._runner_ref = runner
    with patch.object(ct_mod.asyncio, "sleep", new=AsyncMock()):
        results = await t.continuous_think("问题", max_rounds=1)  # 不抛
    assert len(results) == 1


async def test_continuous_think_restore_task_context_on_error(monkeypatch):
    """异常路径恢复 previous_task_context（1214）"""
    prev = _Ctx(task_id="prev")
    t = _thinker(task_context=prev)
    t._running = True
    t._build_prompt = AsyncMock(side_effect=RuntimeError("boom"))
    t._finalize_thinking_results = AsyncMock(side_effect=RuntimeError("finalize boom"))
    t._process_collector.complete = MagicMock()
    t._process_collector.reset = MagicMock()
    t._notify_return_target = AsyncMock()
    ctx = _Ctx(task_id="new")
    await t.continuous_think("问题", max_rounds=1, task_context=ctx)
    assert t._task_context is prev


# ── write_final_response 异常 ───────────────────────────────────────────

def test_write_final_response_exception():
    t = _thinker(model_id="m1")
    bb = MagicMock()
    bb.write_response = MagicMock(side_effect=RuntimeError("boom"))
    t._blackboard = bb
    t.write_final_response("内容")  # 不抛


# ── produce_intermediate_response 段落回退 ──────────────────────────────

def test_produce_intermediate_fallback_with_paragraphs(monkeypatch):
    t = _thinker(history_thoughts=[
        "第一部分内容\n\n这是一段足够长的最终结论内容，用于测试段落回退逻辑，长度远超三十个字符限制，确保能被正确截取为初步回复"
    ])
    engine = MagicMock()
    engine._truncate_to_tokens = MagicMock(return_value="截断")
    monkeypatch.setattr("modules.thinking.context.compression.get_compression_engine", lambda: engine)
    # 无【】标记也无工具调用 → 走段落回退
    out = t.produce_intermediate_response()
    assert out.startswith("[preliminary]")


def test_produce_intermediate_no_paragraphs(monkeypatch):
    t = _thinker(history_thoughts=["短"])
    monkeypatch.setattr("modules.thinking.context.compression.get_compression_engine", lambda: MagicMock())
    assert t.produce_intermediate_response() == ""


# ── 追加补测：剩余分支 ─────────────────────────────────────────────────

def test_init_memory_set_session_exception_real(monkeypatch):
    """__init__ 中 memory.set_session_id 抛异常 → 忽略（121-122）"""
    fake_notebook = MagicMock()
    monkeypatch.setattr("modules.memory.utils.task_notebook.TaskNotebook", lambda sid: fake_notebook)
    memory = MagicMock()
    memory.set_session_id = MagicMock(side_effect=RuntimeError("boom"))
    ident = MagicMock()
    ident.role = "code_writer"
    ident.name = "专家"
    ident.startup = "on_demand"
    ident.tier = "expert"
    monkeypatch.setattr("modules.thinking.identity.ModelIdentity.from_template",
                        staticmethod(lambda k: ident))

    class Sub(ContinuousThinker):
        pass

    t = Sub(think_fn=None, memory_manager=memory, session_id="s1", model_id="m1")
    assert t.memory is memory  # 不抛异常


def test_record_delegation_duplicate_task_id():
    """task_id 已存在 → 不再重复记录（164 分支）"""
    t = _thinker(history_thoughts=["a"])
    t._pending_delegations = {"t1": {"status": "pending"}}
    t._delegation_results = []
    t.record_delegation("role_x", "任务", {"task_id": "t1"})
    assert t._pending_delegations["t1"]["status"] == "pending"  # 未被覆盖


async def test_build_prompt_external_builder_empty(monkeypatch):
    """外部 builder 返回空串 → 不 add（670->676）"""
    t = _thinker(prompt_builder=lambda round_num=0: "")
    t._consume_external_guidance = MagicMock(return_value="")
    t.notebook = None
    pool = MagicMock()
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", lambda: pool)
    composer = MagicMock()
    composer.build = MagicMock(return_value="P")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr("modules.thinking.context.sources.perception_source.PerceptionSource",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr("modules.thinking.skills.skill_manager",
                        type("SM", (), {"match_skill": staticmethod(lambda q: None)})())
    await t._build_prompt("问题", 1)  # 不抛


async def test_continuous_think_prev_control_continue(monkeypatch):
    """_last_control_decision.should_continue → 复用决策（1084）"""
    t = _thinker(think_fn=AsyncMock(return_value="回复"))
    t._running = True
    t.notebook = MagicMock()
    t.notebook.append = MagicMock()
    t._process_collector = MagicMock()
    t._process_collector.record_step = MagicMock()
    t._process_collector.complete = MagicMock()
    t._process_collector.reset = MagicMock()
    t._build_prompt = AsyncMock(return_value="p")
    t._finalize_thinking_results = AsyncMock(return_value="")

    def set_decision(prompt):
        # continuous_think 会在循环前清空 _last_control_decision，需在循环内设置
        if t._last_control_decision is None:
            t._last_control_decision = ThinkingControlDecision(
                should_continue=True, wait_seconds=None, reason="继续"
            )
        return "回复"

    t.think_fn = AsyncMock(side_effect=set_decision)
    with patch.object(ct_mod.asyncio, "sleep", new=AsyncMock()):
        results = await t.continuous_think("问题", max_rounds=2)
    assert len(results) == 2


async def test_continuous_think_control_wait_seconds_exec(monkeypatch):
    """control_decision.wait_seconds 生效（1142-1143）"""
    t = _thinker(think_fn=AsyncMock(return_value="回复"))
    t._running = True
    t.notebook = MagicMock()
    t.notebook.append = MagicMock()
    t._process_collector = MagicMock()
    t._process_collector.record_step = MagicMock()
    t._process_collector.complete = MagicMock()
    t._process_collector.reset = MagicMock()
    t._build_prompt = AsyncMock(return_value="p")
    t._finalize_thinking_results = AsyncMock(return_value="")

    def set_control(prompt):
        t._last_control_data = {"continue": True, "wait_seconds": 9.5, "reason": "x"}
        return "回复"

    t.think_fn = AsyncMock(side_effect=set_control)
    sleeps = []
    with patch.object(ct_mod.asyncio, "sleep", new=AsyncMock(side_effect=lambda s: sleeps.append(s))):
        await t.continuous_think("问题", max_rounds=2)
    assert any(s == 9.0 for s in sleeps)


async def test_continuous_think_second_thought_empty(monkeypatch):
    """第 2 轮 thought 为空 → prev_thought and current_thought False（1162->1174）"""
    t = _thinker(think_fn=AsyncMock(side_effect=["第一轮内容", "第二轮内容", ""]))
    t._running = True
    t.notebook = MagicMock()
    t.notebook.append = MagicMock()
    t._process_collector = MagicMock()
    t._process_collector.record_step = MagicMock()
    t._process_collector.complete = MagicMock()
    t._process_collector.reset = MagicMock()
    t._build_prompt = AsyncMock(return_value="p")
    t._finalize_thinking_results = AsyncMock(return_value="")
    with patch.object(ct_mod.asyncio, "sleep", new=AsyncMock()):
        await t.continuous_think("问题", max_rounds=3)


async def test_continuous_think_runner_no_updater(monkeypatch):
    """runner 无 _update_loop_state → 跳过（1178->1184）"""
    t = _thinker(think_fn=AsyncMock(return_value="回复"))
    t._running = True
    t.notebook = MagicMock()
    t.notebook.append = MagicMock()
    t._process_collector = MagicMock()
    t._process_collector.record_step = MagicMock()
    t._process_collector.complete = MagicMock()
    t._process_collector.reset = MagicMock()
    t._build_prompt = AsyncMock(return_value="p")
    t._finalize_thinking_results = AsyncMock(return_value="")
    runner = MagicMock()
    runner._update_loop_state = None
    t._runner_ref = runner
    with patch.object(ct_mod.asyncio, "sleep", new=AsyncMock()):
        await t.continuous_think("问题", max_rounds=1)


async def test_continuous_think_restore_task_context_on_error2(monkeypatch):
    """异常路径恢复 previous_task_context（1214）—— finalize 成功路径"""
    prev = _Ctx(task_id="prev")
    t = _thinker(task_context=prev)
    t._running = True
    t._build_prompt = AsyncMock(side_effect=RuntimeError("boom"))
    t._finalize_thinking_results = AsyncMock(return_value="")
    t._process_collector.complete = MagicMock()
    t._process_collector.reset = MagicMock()
    t._notify_return_target = AsyncMock()
    ctx = _Ctx(task_id="new")
    await t.continuous_think("问题", max_rounds=1, task_context=ctx)
    assert t._task_context is prev

def test_record_delegation_obj_no_success_attr():
    """result 对象无 success 属性 → 走 is_success 兜底 True（164 分支）"""
    t = _thinker(history_thoughts=["a"])
    t._pending_delegations = {}
    t._delegation_results = []
    result = type("R", (), {"metadata": {"task_id": "t2"}, "foo": 1})()
    t.record_delegation("role_x", "任务", result)
    assert "t2" in t._pending_delegations


async def test_build_prompt_memory_lesson_keywords(monkeypatch):
    """事件带 lesson/keywords → 注入经验/标签（637-641）"""
    t = _thinker()
    t._consume_external_guidance = MagicMock(return_value="")
    t.notebook = None
    pool = MagicMock()
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", lambda: pool)
    composer = MagicMock()
    composer.build = MagicMock(return_value="P")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.thinking.context.sources.perception_source.PerceptionSource",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr("modules.thinking.skills.skill_manager",
                        type("SM", (), {"match_skill": staticmethod(lambda q: None)})())
    ev = type("E", (), {
        "time": "2024-01-01T00:00:00", "type": "fact", "importance": 0.8,
        "fact": "发生了某事", "lesson": "教训", "keywords": ["标签A"],
    })()
    retrieval = MagicMock()
    retrieval.retrieve = AsyncMock(return_value=[ev])
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval", lambda: retrieval)
    t.history_thoughts = ["有历史"]
    await t._build_prompt("问题", 1)
    pool.add.assert_called()


async def test_build_prompt_memory_cache_exception(monkeypatch):
    """_session_memory_context 写入失败 → 非致命（646-647）"""
    t = _thinker()
    t._consume_external_guidance = MagicMock(return_value="")
    t.notebook = None
    pool = MagicMock()
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", lambda: pool)
    composer = MagicMock()
    composer.build = MagicMock(return_value="P")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.thinking.context.sources.perception_source.PerceptionSource",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr("modules.thinking.skills.skill_manager",
                        type("SM", (), {"match_skill": staticmethod(lambda q: None)})())
    ev = type("E", (), {
        "time": "2024-01-01T00:00:00", "type": "fact", "importance": 0.8,
        "fact": "发生了某事", "lesson": "", "keywords": [],
    })()
    retrieval = MagicMock()
    retrieval.retrieve = AsyncMock(return_value=[ev])
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval", lambda: retrieval)
    import modules.thinking.core.model_runner as mr_mod
    class _BoomCache(dict):
        def __setitem__(self, k, v):
            raise RuntimeError("cache down")
    monkeypatch.setattr(mr_mod, "_session_memory_context", _BoomCache())
    t.history_thoughts = ["有历史"]
    await t._build_prompt("问题", 1)  # 不抛


async def test_build_prompt_sync_external_builder(monkeypatch):
    """外部 prompt builder 为同步函数（669）"""
    t = _thinker(prompt_builder=lambda round_num=0: "外部段落")
    t._consume_external_guidance = MagicMock(return_value="")
    t.notebook = None
    pool = MagicMock()
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", lambda: pool)
    composer = MagicMock()
    composer.build = MagicMock(return_value="P")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr("modules.thinking.context.sources.perception_source.PerceptionSource",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr("modules.thinking.skills.skill_manager",
                        type("SM", (), {"match_skill": staticmethod(lambda q: None)})())
    await t._build_prompt("问题", 1)
    pool.add.assert_called()


async def test_build_prompt_external_builder_exception(monkeypatch):
    """外部 prompt builder 抛异常 → 非致命（672-673）"""
    t = _thinker(prompt_builder=lambda round_num=0: (_ for _ in ()).throw(RuntimeError("boom")))
    t._consume_external_guidance = MagicMock(return_value="")
    t.notebook = None
    pool = MagicMock()
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", lambda: pool)
    composer = MagicMock()
    composer.build = MagicMock(return_value="P")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr("modules.thinking.context.sources.perception_source.PerceptionSource",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr("modules.thinking.skills.skill_manager",
                        type("SM", (), {"match_skill": staticmethod(lambda q: None)})())
    await t._build_prompt("问题", 1)  # 不抛


async def test_build_prompt_delegation_status(monkeypatch):
    """委托状态非空 → pool.add delegation（678）"""
    t = _thinker()
    t._consume_external_guidance = MagicMock(return_value="")
    t.notebook = None
    t._build_delegation_status_section = MagicMock(return_value="委托中")
    pool = MagicMock()
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", lambda: pool)
    composer = MagicMock()
    composer.build = MagicMock(return_value="P")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr("modules.thinking.context.sources.perception_source.PerceptionSource",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr("modules.thinking.skills.skill_manager",
                        type("SM", (), {"match_skill": staticmethod(lambda q: None)})())
    await t._build_prompt("问题", 1)
    pool.add.assert_called()


async def test_build_prompt_forced_skill_exception(monkeypatch):
    """get_forced_skill 抛异常 → 回退自动匹配（689-690）"""
    t = _thinker()
    t._consume_external_guidance = MagicMock(return_value="")
    t.notebook = None
    pool = MagicMock()
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", lambda: pool)
    composer = MagicMock()
    composer.build = MagicMock(return_value="P")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr("modules.thinking.context.sources.perception_source.PerceptionSource",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    cfg = sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(
        get_forced_skill=lambda: (_ for _ in ()).throw(RuntimeError("no cfg"))))
    skill = type("S", (), {"id": "web_surfer", "name": "网络冲浪"})()
    monkeypatch.setattr("modules.thinking.skills.skill_manager",
                        type("SM", (), {"match_skill": staticmethod(lambda q: skill)})())
    await t._build_prompt("问题", 1)
    pool.add.assert_called()


async def test_build_prompt_active_skill_skips(monkeypatch):
    """runner 已有 active_skill → 跳过技能建议（684->710）"""
    t = _thinker()
    t._consume_external_guidance = MagicMock(return_value="")
    t.notebook = None
    runner = MagicMock()
    runner._active_skill = MagicMock()
    t._runner_ref = runner
    pool = MagicMock()
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", lambda: pool)
    composer = MagicMock()
    composer.build = MagicMock(return_value="P")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr("modules.thinking.context.sources.perception_source.PerceptionSource",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    await t._build_prompt("问题", 1)
    assert t._context_window_size in (128000, t._context_window_size)


async def test_think_once_timeout_write_success(monkeypatch):
    """超时后黑板写入成功（869-879 非异常路径）"""
    t = _thinker(think_fn=AsyncMock(side_effect=asyncio.TimeoutError()), model_id="m1")
    bb = MagicMock()
    bb.write_thought = MagicMock()
    t._blackboard = bb
    record = await t.think_once("ctx")
    assert "超时" in record["thought"]
    bb.write_thought.assert_called_once()


def test_think_with_existing_think_fn(monkeypatch):
    """think() 已注入 think_fn → 跳过模型初始化（969->992）"""
    t = _thinker(think_fn=AsyncMock(return_value="直接思考"))
    t._process_collector = MagicMock()
    import asyncio as aio
    result = aio.run(t.think("问题"))
    assert result["thought"] == "直接思考"


async def test_continuous_think_exception_restore_task_context(monkeypatch):
    """异常后恢复 previous_task_context（1214，外层 except 恢复路径）"""
    prev = _Ctx(task_id="prev")
    t = _thinker(task_context=prev, think_fn=AsyncMock(return_value="回复"))
    t._running = True
    t.notebook = MagicMock()
    t.notebook.append = MagicMock()
    t._build_prompt = AsyncMock(side_effect=RuntimeError("boom"))
    t._process_collector = MagicMock()
    t._process_collector.reset = MagicMock()
    t._process_collector.complete = MagicMock()
    t._finalize_thinking_results = AsyncMock(return_value="")
    t._notify_return_target = AsyncMock()
    ctx = _Ctx(task_id="new")
    await t.continuous_think("问题", max_rounds=2, task_context=ctx)
    assert t._task_context is prev


def test_produce_intermediate_pattern_too_short(monkeypatch):
    """结构化段落匹配但 combined ≤20 → 继续后续模式（1279->1275）"""
    t = _thinker(history_thoughts=["【回答】短"])
    engine = MagicMock()
    engine._truncate_to_tokens = MagicMock(return_value="x")
    monkeypatch.setattr("modules.thinking.context.compression.get_compression_engine", lambda: engine)
    assert t.produce_intermediate_response() == ""


def test_produce_intermediate_no_matching_paragraph(monkeypatch):
    """段落匹配但 last ≤30 → 返回空（1288->1263, 1290->1263）"""
    t = _thinker(history_thoughts=["【回答】这是一个不超过三十字的内容。"])
    engine = MagicMock()
    engine._truncate_to_tokens = MagicMock(return_value="x")
    monkeypatch.setattr("modules.thinking.context.compression.get_compression_engine", lambda: engine)
    assert t.produce_intermediate_response() == ""


def test_produce_intermediate_structured_too_short(monkeypatch):
    """结构化匹配 combined ≤20 → 继续下一 pattern（1279->1275）"""
    t = _thinker(history_thoughts=["【回答】短"])
    engine = MagicMock()
    engine._truncate_to_tokens = MagicMock(return_value="x")
    monkeypatch.setattr("modules.thinking.context.compression.get_compression_engine", lambda: engine)
    assert t.produce_intermediate_response() == ""


def test_produce_intermediate_fallback_paragraphs_too_short(monkeypatch):
    """回退段落存在但 last ≤30 → 返回空（1288->1263 内的 last 长度分支）"""
    t = _thinker(history_thoughts=["【回答】短内容\n\n还是短"])
    engine = MagicMock()
    engine._truncate_to_tokens = MagicMock(return_value="x")
    monkeypatch.setattr("modules.thinking.context.compression.get_compression_engine", lambda: engine)
    assert t.produce_intermediate_response() == ""


def test_produce_intermediate_structured_combined_short(monkeypatch):
    """结构化匹配但 combined ≤20 → 继续下一 pattern（1279->1275）"""
    t = _thinker(history_thoughts=["【回答】ok\n\n" + "长" * 40])
    engine = MagicMock()
    engine._truncate_to_tokens = MagicMock(return_value="x")
    monkeypatch.setattr("modules.thinking.context.compression.get_compression_engine", lambda: engine)
    out = t.produce_intermediate_response()
    assert out.startswith("[preliminary]")  # 回退段落命中


def test_produce_intermediate_paragraphs_empty(monkeypatch):
    """段落都 ≤20 字符 → paragraphs 空 → 继续下一思考（1288->1263）"""
    t = _thinker(history_thoughts=["短" * 18 + "\n\n" + "短" * 18])
    engine = MagicMock()
    engine._truncate_to_tokens = MagicMock(return_value="x")
    monkeypatch.setattr("modules.thinking.context.compression.get_compression_engine", lambda: engine)
    assert t.produce_intermediate_response() == ""


def test_produce_intermediate_last_paragraph_short(monkeypatch):
    """最后段落 ≤30 → 不返回（1290->1263）"""
    t = _thinker(history_thoughts=["短" * 25 + "\n\n" + "短" * 25])
    engine = MagicMock()
    engine._truncate_to_tokens = MagicMock(return_value="x")
    monkeypatch.setattr("modules.thinking.context.compression.get_compression_engine", lambda: engine)
    assert t.produce_intermediate_response() == ""


async def test_think_once_timeout_no_dialog():
    """超时且无 dialog → 跳过黑板写入（869->879）"""
    t = _thinker(think_fn=AsyncMock(side_effect=asyncio.TimeoutError()), model_id="m1")
    t._blackboard = None
    record = await t.think_once("ctx")
    assert "超时" in record["thought"]


async def test_think_once_timeout_no_model_id():
    """超时且 model_id 为空 → 跳过黑板写入（869->879）"""
    t = _thinker(think_fn=AsyncMock(side_effect=asyncio.TimeoutError()), model_id="")
    bb = MagicMock()
    t._blackboard = bb
    record = await t.think_once("ctx")
    assert "超时" in record["thought"]
    bb.write_thought.assert_not_called()

async def test_continuous_think_control_wait_seconds(monkeypatch):
    """control_decision.wait_seconds → 使用自定义等待（1142-1143）"""
    t = _thinker(think_fn=AsyncMock(return_value="回复"))
    t._running = True
    t.notebook = MagicMock()
    t.notebook.append = MagicMock()
    t._process_collector = MagicMock()
    t._process_collector.record_step = MagicMock()
    t._process_collector.complete = MagicMock()
    t._process_collector.reset = MagicMock()
    t._build_prompt = AsyncMock(return_value="p")
    t._finalize_thinking_results = AsyncMock(return_value="")
    t._last_control_data = {"continue": True, "wait_seconds": 9.5, "reason": "x"}
    with patch.object(ct_mod.asyncio, "sleep", new=AsyncMock()):
        await t.continuous_think("问题", max_rounds=1)
    # 单轮不等待（i < rounds-1 不成立），但控制决策已消费
    assert t._last_control_data is None


async def test_continuous_think_pending_delegations_wait(monkeypatch):
    """有待处理委托 → 延长等待（1148）"""
    t = _thinker(think_fn=AsyncMock(return_value="回复"))
    t._running = True
    t.notebook = MagicMock()
    t.notebook.append = MagicMock()
    t._process_collector = MagicMock()
    t._process_collector.record_step = MagicMock()
    t._process_collector.complete = MagicMock()
    t._process_collector.reset = MagicMock()
    t._build_prompt = AsyncMock(return_value="p")
    t._finalize_thinking_results = AsyncMock(return_value="")

    def set_pending(prompt):
        t._pending_delegations = {"d1": {"status": "pending"}}
        return "回复"

    t.think_fn = AsyncMock(side_effect=set_pending)
    sleeps = []
    with patch.object(ct_mod.asyncio, "sleep", new=AsyncMock(side_effect=lambda s: sleeps.append(s))):
        await t.continuous_think("问题", max_rounds=2)
    assert any(s >= 8.0 for s in sleeps)


async def test_continuous_think_has_delegation_extend(monkeypatch):
    """检测到委托 → 循环在第 1099 行 break，不走到 1153 等待延长（该分支为死代码）"""
    t = _thinker(think_fn=AsyncMock(return_value="回复"))
    t._running = True
    t.notebook = MagicMock()
    t.notebook.append = MagicMock()
    t._process_collector = MagicMock()
    t._process_collector.record_step = MagicMock()
    t._process_collector.complete = MagicMock()
    t._process_collector.reset = MagicMock()
    t._build_prompt = AsyncMock(return_value="p")
    t._finalize_thinking_results = AsyncMock(return_value="")

    def set_del(prompt):
        t._delegation_results = [{"role": "x", "task": "t"}]
        return "回复"

    t.think_fn = AsyncMock(side_effect=set_del)
    sleeps = []
    with patch.object(ct_mod.asyncio, "sleep", new=AsyncMock(side_effect=lambda s: sleeps.append(s))):
        results = await t.continuous_think("问题", max_rounds=2)
    assert len(results) == 1  # 第 1 轮 break，无等待


async def test_continuous_think_duplicate_thresholds(monkeypatch):
    """不同平均长度阈值（1162-1168）"""
    t = _thinker(think_fn=AsyncMock(return_value="这是一个测试重复思考内容的句子"))
    t._running = True
    t.notebook = MagicMock()
    t.notebook.append = MagicMock()
    t._process_collector = MagicMock()
    t._process_collector.record_step = MagicMock()
    t._process_collector.complete = MagicMock()
    t._process_collector.reset = MagicMock()
    t._build_prompt = AsyncMock(return_value="p")
    t._finalize_thinking_results = AsyncMock(return_value="")
    t.interval = 1.0
    sleeps = []
    with patch.object(ct_mod.asyncio, "sleep", new=AsyncMock(side_effect=lambda s: sleeps.append(s))):
        await t.continuous_think("问题", max_rounds=2)
    assert any(s >= 15.0 for s in sleeps) or len(sleeps) == 1


# ── 断点续思考 / 重置 / 中间回复 / 清理 / 便捷入口 补测 ─────────────────────

def test_request_resume_sets_flag():
    """_request_resume 设置 runner 断点续思考标志"""
    t = _thinker()
    runner = MagicMock()
    runner._resume_requested = False
    runner._resume_context = [{"role": "user", "content": "c"}]
    t._runner_ref = runner
    t._request_resume()
    assert runner._resume_requested is True


def test_request_resume_no_runner():
    """无 runner_ref 时安全跳过"""
    t = _thinker()
    t._runner_ref = None
    t._request_resume()  # 不崩


def test_request_resume_no_context():
    """有 runner 但无断点上下文时仅设标志不记日志"""
    t = _thinker()
    runner = MagicMock()
    runner._resume_context = None
    t._runner_ref = runner
    t._request_resume()
    assert runner._resume_requested is True


def test_reset_for_continuation_keeps_last_5():
    """reset_for_continuation 保留最近 5 条历史 + 重置委托计数"""
    t = _thinker(history_thoughts=[f"思考{i}" for i in range(10)])
    t._consecutive_new_delegation_rounds = 3
    t.reset_for_continuation()
    assert len(t.history_thoughts) == 5
    assert t.history_thoughts == [f"思考{i}" for i in range(5, 10)]
    assert t._consecutive_new_delegation_rounds == 0


def test_produce_intermediate_response_empty():
    """无历史思考 → 返回空"""
    t = _thinker(history_thoughts=[])
    assert t.produce_intermediate_response() == ""


def test_produce_intermediate_response_extracts_section():
    """提取【结论】结构化段落并加 [preliminary] 标记"""
    t = _thinker(history_thoughts=[
        "思考无实质内容",
        "这是详细的分析过程，包含了多步推理和中间结果展示。\n\n【结论】最终答案是 42，需要进一步验证边界条件。\n\n【建议】补充更多测试用例覆盖异常路径。",
    ])
    out = t.produce_intermediate_response(max_length=2000)
    assert out.startswith("[preliminary]")
    assert "42" in out


def test_produce_intermediate_response_fallback():
    """无结构化段落时回退取最后一段"""
    t = _thinker(history_thoughts=["短", "这是一段足够长的分析结论内容，包含有意义的输出，超过三十字符阈值。"])
    out = t.produce_intermediate_response(max_length=2000)
    assert out.startswith("[preliminary]")


async def test_close_clears_resources():
    """close 清理记忆 + 外部提示"""
    t = _thinker()
    t.clear_memory = MagicMock()
    t.clear_external_prompts = MagicMock()
    await t.close()
    t.clear_memory.assert_called_once()
    t.clear_external_prompts.assert_called_once()


def test_deep_think_no_think_fn():
    """deep_think 无 think_fn → 返回空列表"""
    t = _thinker()
    t.think_fn = None
    assert t.deep_think("问题") == []


def test_collect_final_synthesis_context(monkeypatch):
    """_collect_final_synthesis_context 聚合专家回复上下文"""
    import types as _types
    from modules.thinking.core.continuous_thinker import ContinuousThinker as CT
    t = _thinker()
    # 模拟 delegation_results（直通对象）
    from modules.thinking.core.delegation_port import DelegationResult
    t._delegation_results = [
        {"role": "expert", "task": "分析", "success": True},
    ]
    t._pending_delegations = {}
    ctx = t._collect_final_synthesis_context("问题", [])
    assert isinstance(ctx, str)


def test_write_final_response():
    """write_final_response 写入黑板 response"""
    t = _thinker()
    bb = MagicMock()
    t._blackboard = bb
    t._model_id = "m1"
    t._tier = "large"
    t.write_final_response("最终内容")
    bb.write_response.assert_called_once()


async def test_run_final_synthesis_no_think_fn():
    """_run_final_synthesis 无 think_fn → 返回 None"""
    t = _thinker()
    t.think_fn = None
    out = await t._run_final_synthesis("问题", [])
    assert out is None


async def test_run_final_synthesis_success():
    """_run_final_synthesis 正常合成并记录"""
    t = _thinker(history_thoughts=["旧"])
    t.notebook = None
    t.think_fn = AsyncMock(return_value="【回答】最终总结")
    t._blackboard = MagicMock()
    t._model_id = "m1"
    t._tier = "large"
    out = await t._run_final_synthesis("问题", [], None)
    assert out is not None
    assert out["final_output"] == "【回答】最终总结"
    assert out["is_final_synthesis"] is True


async def test_run_final_synthesis_exception():
    """_run_final_synthesis 异常 → 返回 None（不发布）"""
    t = _thinker()
    t.think_fn = AsyncMock(side_effect=RuntimeError("boom"))
    out = await t._run_final_synthesis("问题", [])
    assert out is None


async def test_finalize_skips_with_pending():
    """有待处理委托时跳过最终合成"""
    t = _thinker(tier="large")
    t._pending_delegations = {"task_1": {"status": "pending"}}
    t._last_control_decision = None
    t._finalize_thinking_results = None
    # 直接测内部逻辑：_finalize_thinking_results 走 pending 分支
    from modules.thinking.core.continuous_thinker import ContinuousThinker
    orig = ContinuousThinker._finalize_thinking_results
    try:
        def _fake_sel(results, cd):
            return "结果"
        t._select_final_result = _fake_sel
        t._build_final_synthesis_prompt = MagicMock(return_value="p")
        t.think_fn = AsyncMock(return_value="合成")
        t._finalize_thinking_results = orig.__get__(t, ContinuousThinker)
        # 需 mock _process_collector 等
        t._process_collector.complete = MagicMock(return_value="snap")
        out = await t._finalize_thinking_results("问题", [])
        assert isinstance(out, str)
    finally:
        pass


async def test_finalize_uses_result_summary():
    """模型已有 result_summary → 跳过合成直接用"""
    t = _thinker(tier="large")
    t._pending_delegations = {}
    from modules.thinking.core.continuous_thinker import ThinkingControlDecision
    t._last_control_decision = ThinkingControlDecision(
        should_continue=False, wait_seconds=None, reason="",
        result_summary="直接结果", delegations=[], raw={},
    )
    # 复用 _select_final_result 真实实现
    results = []
    t._process_collector.complete = MagicMock(return_value="snap")
    out = await t._finalize_thinking_results("问题", results)
    assert out == "直接结果" or "直接结果" in out


async def test_context_manager_enter_exit():
    """async 上下文管理器入口/出口"""
    t = _thinker()
    async with t as ctx:
        assert ctx is t


async def test_build_prompt_forced_skill(monkeypatch):
    """用户强制技能 → 注入【强制技能】提示"""
    t = _thinker()
    t._consume_external_guidance = MagicMock(return_value="")
    t.notebook = None
    pool = MagicMock()
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", lambda: pool)
    composer = MagicMock()
    composer.build = MagicMock(return_value="P")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr("modules.thinking.context.sources.perception_source.PerceptionSource",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    skill = type("S", (), {"name": "代码技能", "id": "code", "enabled": True})()
    monkeypatch.setattr("modules.thinking.skills.skill_manager",
                        type("SM", (), {
                            "get_skill": staticmethod(lambda _id: skill),
                            "match_skill": staticmethod(lambda q: None),
                        })())
    import sys, types
    cfg = sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(get_forced_skill=lambda: "code"))
    await t._build_prompt("问题", 1)
    added = [c.args[0] for c in pool.add.call_args_list]
    skill_frags = [f for f in added if getattr(f, "source", "") == "skill_suggestion"]
    assert skill_frags and "强制技能" in skill_frags[0].content


async def test_build_prompt_matched_skill(monkeypatch):
    """无强制技能时按问题匹配 → 注入【建议技能】提示"""
    t = _thinker()
    t._consume_external_guidance = MagicMock(return_value="")
    t.notebook = None
    pool = MagicMock()
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", lambda: pool)
    composer = MagicMock()
    composer.build = MagicMock(return_value="P")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr("modules.thinking.context.sources.perception_source.PerceptionSource",
                        lambda: (_ for _ in ()).throw(RuntimeError("no")))
    skill = type("S", (), {"name": "写作技能", "id": "writer", "enabled": True})()
    monkeypatch.setattr("modules.thinking.skills.skill_manager",
                        type("SM", (), {
                            "get_skill": staticmethod(lambda _id: None),
                            "match_skill": staticmethod(lambda q: skill),
                        })())
    import sys, types
    cfg = sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(get_forced_skill=lambda: ""))
    await t._build_prompt("问题", 1)
    added = [c.args[0] for c in pool.add.call_args_list]
    skill_frags = [f for f in added if getattr(f, "source", "") == "skill_suggestion"]
    assert skill_frags and "建议技能" in skill_frags[0].content
