"""multi_model_orchestrator 扩展测试：process 全流程 / 队列 / 思考循环 / 审查"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import modules.thinking.multi_model_orchestrator as mmo
from modules.thinking.multi_model_orchestrator import MultiModelOrchestrator


def _orch():
    return MultiModelOrchestrator.__new__(MultiModelOrchestrator)


# ── 懒加载 ─────────────────────────────────────────────────────────────

def test_get_activity_notifier_lazy(monkeypatch):
    orch = _orch()
    orch._activity_notifier = None
    fake = MagicMock()
    monkeypatch.setattr("modules.thinking.adapters.DifferenceDetectorActivityNotifier", lambda: fake)
    assert orch._get_activity_notifier() is fake


def test_get_security_lazy(monkeypatch):
    orch = _orch()
    orch._security = None
    fake = MagicMock()
    monkeypatch.setattr("modules.thinking.adapters.SecurityApiAdapter", lambda: fake)
    assert orch._get_security() is fake


def test_get_output_reviewer_lazy(monkeypatch):
    orch = _orch()
    orch._output_reviewer = None
    fake = MagicMock()
    monkeypatch.setattr("modules.thinking.adapters.OutputSystemReviewAdapter", lambda: fake)
    assert orch._get_output_reviewer() is fake


# ── 安全验证 ───────────────────────────────────────────────────────────

def test_build_security_error():
    out = MultiModelOrchestrator._build_security_error("危险", 1000.0)
    assert out["security_passed"] is False
    assert "危险" in out["response"]
    assert out["focus"] == "security_blocked"


async def test_validate_security():
    orch = _orch()
    orch._security = MagicMock()
    orch._security.validate_input = MagicMock(return_value=(True, ""))
    assert await orch._validate_security("hi") == (True, "")


# ── process 全流程 ─────────────────────────────────────────────────────

async def test_process_full_flow(monkeypatch):
    orch = _orch()
    orch._current_model_id = None
    notifier = MagicMock()
    orch._activity_notifier = notifier
    orch._security = MagicMock()
    orch._security.validate_input = MagicMock(return_value=(True, ""))
    orch._guidance_service = MagicMock()
    orch._guidance_service.run = AsyncMock(return_value={"inner_thoughts": "独白"})
    orch._output_reviewer = MagicMock()
    orch._output_reviewer.review = AsyncMock(return_value="清洗后的回复")
    orch._match_skill = MagicMock(return_value="")
    orch._execute_multi_model_thinking = AsyncMock(return_value={
        "response": "原始回复", "thinking_history": [], "thinking_turns": 1,
        "probe_signals": [], "blackboard": None,
    })
    orch._conscience_feedback = AsyncMock()
    orch._maybe_evolve_values = AsyncMock()
    # 主动搭话重置
    ps = MagicMock()
    ps.proactive_trigger = MagicMock()
    monkeypatch.setattr("modules.perception.setup.get_perception_system", lambda: ps)
    # 执行模式
    cfg = MagicMock()
    cfg.effective_execution_mode = "edit"
    import sys, types
    cfg_mod = sys.modules["config.settings"]
    monkeypatch.setattr(cfg_mod, "settings", cfg)
    controller = MagicMock()
    monkeypatch.setattr("modules.thinking.context.controller.get_context_controller", lambda: controller)

    result = await orch.process("你好", session_id="s1", model_id="large_primary")
    assert result["response"] == "清洗后的回复"
    assert result["security_passed"] is True
    assert result["focus"] == "multi_model"
    notifier.notify_activity.assert_called_once()
    ps.proactive_trigger.reset_cooldown.assert_called_once()


async def test_process_security_blocked(monkeypatch):
    orch = _orch()
    orch._security = MagicMock()
    orch._security.validate_input = MagicMock(return_value=(False, "包含敏感信息"))
    result = await orch.process("恶意输入", session_id="s1")
    assert result["security_passed"] is False
    assert "敏感信息" in result["response"]


async def test_process_auto_session_id(monkeypatch):
    orch = _orch()
    orch._security = MagicMock()
    orch._security.validate_input = MagicMock(return_value=(True, ""))
    orch._guidance_service = MagicMock()
    orch._guidance_service.run = AsyncMock(return_value={})
    orch._match_skill = MagicMock(return_value="")
    orch._execute_multi_model_thinking = AsyncMock(return_value={
        "response": "r", "thinking_history": [], "thinking_turns": 0,
        "probe_signals": [], "blackboard": None,
    })
    orch._output_reviewer = MagicMock()
    orch._output_reviewer.review = AsyncMock(return_value="r")
    orch._conscience_feedback = AsyncMock()
    orch._maybe_evolve_values = AsyncMock()
    result = await orch.process("hi")
    assert result["response"] == "r"


# ── process_async / 队列 ───────────────────────────────────────────────

async def test_process_async_success(monkeypatch):
    orch = _orch()
    q = asyncio.Queue()
    orch._request_queues = {"s": q}
    orch._queue_consumers = {}
    orch.process = AsyncMock(return_value={"response": "ok"})

    async def fake_put(item):
        user_input, kwargs, rq = item
        r = await orch.process(user_input, session_id="s", **kwargs)
        await rq.put(("success", r))

    q.put = fake_put
    orch._get_or_create_queue = MagicMock(return_value=q)
    out = await orch.process_async("hi", session_id="s")
    assert out["response"] == "ok"


async def test_process_async_error(monkeypatch):
    orch = _orch()
    q = asyncio.Queue()
    orch._request_queues = {"s": q}
    orch._queue_consumers = {}
    orch.process = AsyncMock(side_effect=RuntimeError("处理崩了"))

    async def fake_put(item):
        user_input, kwargs, rq = item
        try:
            await orch.process(user_input, session_id="s", **kwargs)
        except Exception as e:
            await rq.put(("error", str(e)))

    q.put = fake_put
    with pytest.raises(RuntimeError) as ei:
        await orch.process_async("hi", session_id="s")
    assert "处理崩了" in str(ei.value)


async def test_get_or_create_queue_and_consumer_close(monkeypatch):
    orch = _orch()
    orch._request_queues = {}
    orch._queue_consumers = {}
    orch.process = AsyncMock(return_value={})
    q = orch._get_or_create_queue("sess")
    assert "sess" in orch._request_queues
    # 放入 None 关闭信号 → 消费者退出
    await q.put(None)
    await asyncio.sleep(0.1)
    assert q.empty()


async def test_consumer_puts_result(monkeypatch):
    orch = _orch()
    orch._request_queues = {}
    orch._queue_consumers = {}
    orch.process = AsyncMock(return_value={"response": "你好"})
    q = orch._get_or_create_queue("sess2")
    result_q = asyncio.Queue()
    await q.put(("你好", {}, result_q))
    rtype, rdata = await asyncio.wait_for(result_q.get(), timeout=5)
    assert rtype == "success"
    assert rdata["response"] == "你好"


# ── _execute_multi_model_thinking（mock 内部依赖）──────────────────────

class FakeBlackboard:
    def __init__(self):
        self.runtime_state = {}
        self.final_response = "思考完成回复"

    def set_goal(self, g):
        pass

    def add_observation(self, tier, content, metadata=None):
        pass

    def write_user_input(self, u):
        return type("E", (), {"timestamp": 1.0})()


class FakeTurnContext:
    def __init__(self, *a, **k):
        self.turn_id = "turn-1"
        self.last_user_message_time = 0.0


class FakeBus:
    def __init__(self):
        self.channel_msgs = {}

    async def send(self, msg):
        return msg.msg_id

    async def subscribe(self, channel, cb):
        return None

    async def unsubscribe(self, channel, cb):
        return None

    async def peek(self, channel, limit=5):
        return []

    async def receive(self, channel):
        return [] if channel not in self.channel_msgs else self.channel_msgs[channel].pop(0)


async def test_execute_multi_model_thinking_happy(monkeypatch):
    orch = _orch()
    bb = FakeBlackboard()
    tc = FakeTurnContext()
    runner_manager = MagicMock()
    runner_manager.start_listening = AsyncMock()
    runner_manager.get_active_runners = MagicMock(return_value={})

    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", FakeTurnContext)
    monkeypatch.setattr("modules.thinking.cognition.blackboard.CognitiveBlackboard", lambda **kw: bb)
    monkeypatch.setattr("modules.thinking.core.model_runner.get_runner_manager", lambda *a, **k: runner_manager)
    monkeypatch.setattr("modules.thinking.core.model_runner.remove_runner_manager", AsyncMock())
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus", lambda: FakeBus())
    composer = MagicMock()
    composer._build_supervisor_table = MagicMock(return_value="- 主管1")
    composer._build_expert_table = MagicMock(return_value="- 专家1")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.security_system.tool_security_gate.get_tool_security_gate", lambda: MagicMock())

    out = await orch._execute_multi_model_thinking(
        "你好", "sess", {"inner_thoughts": ""}, None, skill_id="", context=[], model_id="large_primary",
    )
    assert out["response"] == "思考完成回复"
    assert out["blackboard"] is bb


async def test_execute_multi_model_thinking_exception(monkeypatch):
    orch = _orch()
    def boom(*a, **k):
        raise RuntimeError("init fail")
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", boom)
    monkeypatch.setattr("modules.thinking.core.model_runner.remove_runner_manager", AsyncMock())
    out = await orch._execute_multi_model_thinking("q", "s", {}, None)
    assert "思考失败" in out["response"]


# ── _is_user_visible_response ──────────────────────────────────────────

def test_is_user_visible_response():
    assert MultiModelOrchestrator._is_user_visible_response(None) is False
    assert MultiModelOrchestrator._is_user_visible_response({}) is False
    assert MultiModelOrchestrator._is_user_visible_response({"content": "  "}) is False
    assert MultiModelOrchestrator._is_user_visible_response({"content": "hello", "metadata": {"internal_protocol": True}}) is False
    assert MultiModelOrchestrator._is_user_visible_response({"content": "hello", "metadata": {"final_visible": False}}) is False
    assert MultiModelOrchestrator._is_user_visible_response({"content": "调用 delegate_task 工具"}) is False
    assert MultiModelOrchestrator._is_user_visible_response({"content": "正常回复"}) is True


# ── _review_output ─────────────────────────────────────────────────────

async def test_review_output_clean(monkeypatch):
    orch = _orch()
    orch._output_reviewer = MagicMock()
    orch._output_reviewer.review = AsyncMock(return_value="clean")
    assert await orch._review_output("raw", "user") == "clean"


async def test_review_output_security_block():
    orch = _orch()
    orch._output_reviewer = MagicMock()
    orch._output_reviewer.review = AsyncMock(return_value="clean")
    bb = MagicMock()
    bb.has_security_block = MagicMock(return_value=True)
    bb.get_security_block = MagicMock(return_value={
        "category": "危险操作", "description": "删除生产库", "risk_level": "high",
    })
    out = await orch._review_output("raw", "user", None, bb)
    assert "安全审查拦截" in out
    assert "删除生产库" in out


# ── 反馈 / 价值观演化 ──────────────────────────────────────────────────

async def test_conscience_feedback(monkeypatch):
    orch = _orch()
    cons = MagicMock()
    cons.analyze_feedback = AsyncMock()
    monkeypatch.setattr("modules.thinking.conscience.get_conscience", lambda: cons)
    await orch._conscience_feedback("u", "r")
    cons.analyze_feedback.assert_awaited_once()


async def test_conscience_feedback_error(monkeypatch):
    orch = _orch()
    def boom():
        raise RuntimeError("no conscience")
    monkeypatch.setattr("modules.thinking.conscience.get_conscience", boom)
    await orch._conscience_feedback("u", "r")  # 不抛异常


async def test_maybe_evolve_values_short_response():
    orch = _orch()
    await orch._maybe_evolve_values("u", "短")  # len < 20 → 直接返回


async def test_maybe_evolve_values_no_risk():
    orch = _orch()
    await orch._maybe_evolve_values("普通问题", "这是一个正常长度的回复内容")  # 无风险词 → 返回


# ── get_active_sessions ────────────────────────────────────────────────

def test_get_active_sessions(monkeypatch):
    registry = {"s1": {"session_id": "s1", "state": "planning"}}
    monkeypatch.setattr(mmo, "_session_registry", registry)
    assert mmo.get_active_sessions() == [{"session_id": "s1", "state": "planning"}]
