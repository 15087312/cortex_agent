"""轮次状态机的真实接线：TurnContext.state 必须随回合推进，会话表必须跟着走。

背景（docs/CHECKER_AUDIT.md F5）：`TurnState` 枚举 + `transition_to()` 早就存在，
但生产代码从未调用过 —— 状态永远停在 IDLE，`_session_registry[...]["state"]` 也永远
是写死的 "planning"，管理 API `/sessions` 因此一直显示"规划中"。

L4（modules/thinking/orchestrator.link 的 `state turn`）现在会校验声明与真实调用点
一致；这里验证代码侧确实在推进。
"""
import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import modules.thinking.multi_model_orchestrator as mmo
from modules.thinking.context.pool import TurnContext, TurnState
from modules.thinking.multi_model_orchestrator import MultiModelOrchestrator


def _orch():
    return MultiModelOrchestrator.__new__(MultiModelOrchestrator)


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


class FakeBus:
    """订阅时立刻投递 thinking_complete，让等待循环不空转。"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.cb = None

    async def send(self, msg):
        return msg.msg_id

    async def subscribe(self, channel, cb):
        self.cb = cb
        await cb(object())

    async def unsubscribe(self, channel, cb):
        return None

    async def peek(self, channel, limit=5):
        return [
            type(
                "M",
                (),
                {
                    "content": {
                        "action": "thinking_complete",
                        "tier": "large",
                        "session_id": self.session_id,
                    }
                },
            )()
        ]

    async def receive(self, channel):
        return []


async def test_turn_state_advances_through_the_turn(monkeypatch):
    session = "lifecycle-sess"
    orch = _orch()
    bb = FakeBlackboard()
    bus = FakeBus(session)
    runner_manager = MagicMock()
    runner_manager.start_listening = AsyncMock()
    runner_manager.get_active_runners = MagicMock(return_value={})

    monkeypatch.setattr(mmo, "_session_registry", {})
    monkeypatch.setattr(mmo, "_session_registry_lock", threading.Lock())
    monkeypatch.setattr("modules.thinking.cognition.blackboard.CognitiveBlackboard", lambda **kw: bb)
    monkeypatch.setattr("modules.thinking.core.model_runner.get_runner_manager", lambda *a, **k: runner_manager)
    monkeypatch.setattr("modules.thinking.core.model_runner.remove_runner_manager", AsyncMock())
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus", lambda: bus)
    composer = MagicMock()
    composer._build_supervisor_table = MagicMock(return_value="")
    composer._build_expert_table = MagicMock(return_value="")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr(
        "modules.security_system.tool_security_gate.get_tool_security_gate", lambda: MagicMock()
    )

    out = await asyncio.wait_for(
        orch._execute_multi_model_thinking(
            "你好", session, {"inner_thoughts": ""}, None, skill_id="", context=[],
            model_id="large_primary",
        ),
        timeout=20,
    )

    tc = out["turn_context"]
    # 回复已拿到、输出审查尚未完成 → 停在 integrating（complete 由 process() 收尾）
    assert tc.state == TurnState.INTEGRATING, tc.state
    assert mmo._session_registry[session]["state"] == "integrating"
    assert mmo._session_registry[session]["is_active"] is True


async def test_advance_to_complete_closes_the_turn(monkeypatch):
    session = "close-sess"
    tc = TurnContext()
    monkeypatch.setattr(mmo, "_session_registry", {session: {"session_id": session, "state": "idle"}})
    monkeypatch.setattr(mmo, "_session_registry_lock", threading.Lock())

    mmo._advance_turn_state(tc, TurnState.PLANNING, session)
    mmo._advance_turn_state(tc, TurnState.EXECUTING, session)
    mmo._advance_turn_state(tc, TurnState.INTEGRATING, session)
    mmo._advance_turn_state(tc, TurnState.COMPLETE, session)

    assert tc.state == TurnState.COMPLETE
    assert tc.is_complete is True
    assert tc.elapsed_seconds > 0  # transition_to 在终态记录耗时
    assert mmo._session_registry[session]["state"] == "complete"
    assert mmo._session_registry[session]["is_active"] is False


def test_advance_tolerates_context_without_transition_to(monkeypatch):
    """TurnContext 的测试替身缺 transition_to 时不得打断回合。"""
    monkeypatch.setattr(mmo, "_session_registry", {"s": {"session_id": "s"}})
    monkeypatch.setattr(mmo, "_session_registry_lock", threading.Lock())

    class Bare:
        turn_id = "t"

    mmo._advance_turn_state(Bare(), TurnState.PLANNING, "s")  # 不抛异常
    assert mmo._session_registry["s"] == {"session_id": "s"}
    mmo._advance_turn_state(None, TurnState.ERROR, "s")
