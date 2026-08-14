"""multi_model_orchestrator 深分支测试：历史注入 / 引导推送 / 上下文时间 / 失败路径"""
import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.thinking.multi_model_orchestrator import MultiModelOrchestrator


def _orch():
    return MultiModelOrchestrator.__new__(MultiModelOrchestrator)


class FakeBlackboard:
    def __init__(self):
        self.runtime_state = {}
        self.final_response = ""
        self.observations = []
        self.entries = []

    def set_goal(self, g):
        pass

    def add_observation(self, tier, content, metadata=None):
        self.observations.append((tier, content))

    def write_user_input(self, u):
        return type("E", (), {"timestamp": 1.0})()


class FakeTurnContext:
    def __init__(self, *a, **k):
        self.turn_id = "turn-1"
        self.last_user_message_time = 0.0


class FakeBus:
    def __init__(self, done_for_session=None):
        self.sent = []
        self._done_for = done_for_session
        self._callbacks = {}
        self._peek_store = {}

    async def send(self, msg):
        self.sent.append(msg)
        return msg.msg_id

    async def subscribe(self, channel, cb):
        self._callbacks[channel] = cb
        if self._done_for and channel == f"orchestrator_{self._done_for[:12]}":
            # 模拟 ModelRunnerManager 已回发 thinking_complete → 触发订阅回调
            self._peek_store[channel] = [type("M", (), {"content": {
                "action": "thinking_complete", "tier": "large", "session_id": self._done_for,
            }})()]
            try:
                await cb(self._peek_store[channel][0])
            except Exception:
                pass

    async def unsubscribe(self, channel, cb):
        self._callbacks.pop(channel, None)

    async def peek(self, channel, limit=5):
        return self._peek_store.get(channel, [])

    async def receive(self, channel):
        return []


def _patch_base(monkeypatch, bb=None, bus=None, session_id="sess"):
    bb = bb or FakeBlackboard()
    bus = bus or FakeBus(done_for_session=session_id)
    runner_manager = MagicMock()
    runner_manager.start_listening = AsyncMock()
    runner_manager.get_active_runners = MagicMock(return_value={})
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", FakeTurnContext)
    monkeypatch.setattr("modules.thinking.cognition.blackboard.CognitiveBlackboard", lambda **kw: bb)
    monkeypatch.setattr("modules.thinking.core.model_runner.get_runner_manager", lambda *a, **k: runner_manager)
    monkeypatch.setattr("modules.thinking.core.model_runner.remove_runner_manager", AsyncMock())
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus", lambda: bus)
    composer = MagicMock()
    composer._build_supervisor_table = MagicMock(return_value="- 主管1")
    composer._build_expert_table = MagicMock(return_value="- 专家1")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.security_system.tool_security_gate.get_tool_security_gate", lambda: MagicMock())
    return bb, bus


async def test_execute_with_history_and_prev_user_time(monkeypatch):
    orch = _orch()
    bb, bus = _patch_base(monkeypatch)
    context = [
        {"role": "user", "content": "旧消息", "timestamp": 100.0},
        {"role": "user", "content": "当前"},
    ]
    out = await orch._execute_multi_model_thinking(
        "问题", "sess", {}, None, skill_id="", context=context, model_id="large_primary",
    )
    assert out["blackboard"] is bb
    assert bb.runtime_state.get("last_user_message_time") == 100.0
    # 对话历史已注入黑板
    assert any("对话历史" in c for _, c in bb.observations)


async def test_execute_no_context_and_guidance(monkeypatch):
    orch = _orch()
    bb, bus = _patch_base(monkeypatch)
    set_guidance = MagicMock()
    monkeypatch.setattr("modules.thinking.probes.probe_tools.set_session_guidance", set_guidance)
    push = AsyncMock()
    monkeypatch.setattr("modules.thinking.frontend_channel.push_content", push)
    from modules.thinking import api_stream as api_mod
    monkeypatch.setattr(api_mod.connection_manager, "active_connections", {})
    monkeypatch.setattr(api_mod, "_build_event", lambda **kw: {"event": kw.get("event")})
    guidance = {"inner_thoughts": "内心独白内容"}
    out = await orch._execute_multi_model_thinking(
        "问题", "sess", guidance, None, skill_id="", context=None, model_id="large_primary",
    )
    assert out["response"]  # 无最终回复时给系统通知
    assert any("内心独白内容" in c for _, c in bb.observations)
    set_guidance.assert_called_once()
    push.assert_awaited_once()


async def test_execute_runner_manager_failure(monkeypatch):
    orch = _orch()
    bb, bus = _patch_base(monkeypatch)
    mgr = MagicMock()
    mgr.start_listening = AsyncMock(side_effect=RuntimeError("start fail"))
    monkeypatch.setattr("modules.thinking.core.model_runner.get_runner_manager", lambda *a, **k: mgr)
    out = await orch._execute_multi_model_thinking("q", "sess", {}, None)
    assert out["blackboard"] is bb  # 非致命


async def test_execute_prev_user_time_no_turn_context(monkeypatch):
    orch = _orch()
    bb, bus = _patch_base(monkeypatch)
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no ctx")))
    context = [
        {"role": "user", "content": "旧", "timestamp": 50.0},
        {"role": "user", "content": "新"},
    ]
    out = await orch._execute_multi_model_thinking("q", "s", {}, None, context=context)
    assert out["blackboard"] is None  # 初始化失败


async def test_execute_remove_manager_failure(monkeypatch):
    orch = _orch()
    bb, bus = _patch_base(monkeypatch)
    monkeypatch.setattr("modules.thinking.core.model_runner.remove_runner_manager", AsyncMock(side_effect=RuntimeError("rm fail")))
    out = await orch._execute_multi_model_thinking("q", "sess", {}, None)
    assert out["blackboard"] is bb


# ── process 更多分支 ───────────────────────────────────────────────────

async def test_process_perception_error(monkeypatch):
    orch = _orch()
    orch._security = MagicMock()
    orch._security.validate_input = MagicMock(return_value=(True, ""))
    orch._guidance_service = MagicMock()
    orch._guidance_service.run = AsyncMock(return_value={})
    orch._match_skill = MagicMock(return_value="skill_x")
    orch._execute_multi_model_thinking = AsyncMock(return_value={
        "response": "r", "thinking_history": [], "thinking_turns": 0,
        "probe_signals": [], "blackboard": None,
    })
    orch._output_reviewer = MagicMock()
    orch._output_reviewer.review = AsyncMock(return_value="r")
    orch._conscience_feedback = AsyncMock()
    orch._maybe_evolve_values = AsyncMock()
    monkeypatch.setattr("modules.perception.setup.get_perception_system", lambda: (_ for _ in ()).throw(RuntimeError("no")))
    import sys as _sys
    cfg = _sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(effective_execution_mode="edit"))
    monkeypatch.setattr("modules.thinking.context.controller.get_context_controller", lambda: (_ for _ in ()).throw(RuntimeError("no")))
    result = await orch.process("你好", session_id="s1")
    assert result["response"] == "r"


async def test_review_output_security_error(monkeypatch):
    orch = _orch()
    orch._output_reviewer = MagicMock()
    orch._output_reviewer.review = AsyncMock(return_value="clean")
    bb = MagicMock()
    bb.has_security_block = MagicMock(side_effect=RuntimeError("boom"))
    out = await orch._review_output("raw", "user", None, bb)
    assert out == "clean"


async def test_maybe_evolve_values_risk_keywords(monkeypatch):
    orch = _orch()
    cons = MagicMock()
    cons.review_and_evolve = AsyncMock()
    client = MagicMock()
    monkeypatch.setattr("modules.thinking.conscience.get_conscience", lambda: cons)
    monkeypatch.setattr("infra.model.small_model_client.SmallModelClient", lambda **k: client)
    import sys as _sys
    cfg = _sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(
        SMALL_MODEL_API_KEY="k", LARGE_MODEL_API_KEY="lk",
        SMALL_MODEL_API_URL="u", LARGE_MODEL_API_URL="lu",
    ))
    await orch._maybe_evolve_values("删除生产库", "这是超过二十个字符的响应内容内容内容内容内容内容")
    cons.review_and_evolve.assert_awaited_once()
