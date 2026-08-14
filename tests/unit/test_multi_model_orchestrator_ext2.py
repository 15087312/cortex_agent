"""multi_model_orchestrator 补测 — 队列超时 / 消费者异常 / 思考循环收尾 / 匹配技能异常

不真实调用模型：全程 mock bus / runner_manager / blackboard / 各 adapter。
"""
import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

import modules.thinking.multi_model_orchestrator as mmo
from modules.thinking.multi_model_orchestrator import MultiModelOrchestrator


def _orch():
    return MultiModelOrchestrator.__new__(MultiModelOrchestrator)


# ── __init__ 默认值 ─────────────────────────────────────────────────────

def test_init_defaults(monkeypatch):
    monkeypatch.setattr("modules.security_system.audit_logger.SecurityAuditLogger", lambda: MagicMock())
    o = MultiModelOrchestrator()
    assert o._gcm_pool is None
    assert o._request_queues == {}
    assert o._queue_consumers == {}


def test_get_guidance_service_lazy(monkeypatch):
    orch = _orch()
    orch._guidance_service = None
    fake = MagicMock()
    monkeypatch.setattr("modules.thinking.adapters.PreGenExpertGuidanceAdapter", lambda: fake)
    assert orch._get_guidance_service() is fake


# ── 队列消费者：process 抛异常 / 空闲超时 ───────────────────────────────

async def test_consumer_puts_error(monkeypatch):
    orch = _orch()
    orch._request_queues = {}
    orch._queue_consumers = {}
    orch.process = AsyncMock(side_effect=RuntimeError("处理异常"))
    q = orch._get_or_create_queue("sess")
    result_q = asyncio.Queue()
    await q.put(("你好", {}, result_q))
    rtype, rdata = await asyncio.wait_for(result_q.get(), timeout=5)
    assert rtype == "error"
    assert "处理异常" in rdata


async def test_consumer_idle_timeout(monkeypatch):
    orch = _orch()
    orch._request_queues = {}
    orch._queue_consumers = {}
    orch.process = AsyncMock(return_value={})
    real_wait_for = asyncio.wait_for
    state = {"exhausted": False}

    async def fake_wait_for(awaitable, timeout=None):
        if timeout == 300 and orch._request_queues.get("sess") and not state["exhausted"]:
            state["exhausted"] = True
            raise asyncio.TimeoutError()
        return await real_wait_for(awaitable, timeout=timeout)

    monkeypatch.setattr(mmo.asyncio, "wait_for", fake_wait_for)
    orch._get_or_create_queue("sess")
    await asyncio.sleep(0.05)
    assert "sess" not in orch._request_queues
    assert "sess" not in orch._queue_consumers


# ── process_async：自动 session_id / 超时 ───────────────────────────────

async def test_process_async_auto_session_id(monkeypatch):
    orch = _orch()
    q = asyncio.Queue()
    orch._request_queues = {}
    orch._queue_consumers = {}

    async def fake_put(item):
        user_input, kwargs, rq = item
        await rq.put(("success", {"session": kwargs.get("session_id")}))

    q.put = fake_put
    orch._get_or_create_queue = MagicMock(return_value=q)
    out = await orch.process_async("hi")
    assert out["session"] is None


async def test_process_async_timeout(monkeypatch):
    orch = _orch()
    q = asyncio.Queue()
    orch._request_queues = {"s": q}
    orch._queue_consumers = {}
    orch.process = AsyncMock(return_value={})

    async def fake_put(item):
        pass  # 永不返回结果 → wait_for 超时

    q.put = fake_put
    real_wait_for = asyncio.wait_for

    async def fake_wait_for(awaitable, timeout=None):
        if timeout == 300.0:
            raise asyncio.TimeoutError()
        return await real_wait_for(awaitable, timeout=timeout)

    monkeypatch.setattr(mmo.asyncio, "wait_for", fake_wait_for)
    with pytest.raises(RuntimeError):
        await orch.process_async("hi", session_id="s")


# ── process：context 为 None / short_term_memory 为 None ────────────────

async def test_process_none_lists(monkeypatch):
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
    result = await orch.process("hi", context=None, short_term_memory=None)
    assert result["response"] == "r"


async def test_process_with_lists(monkeypatch):
    """context/short_term_memory 非 None → 直接使用（221->227 分支）"""
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
    result = await orch.process("hi", context=[{"role": "user", "content": "c"}], short_term_memory=["m"])
    assert result["response"] == "r"
    orch._execute_multi_model_thinking.assert_awaited_once()


# ── _match_skill 强制技能异常 ───────────────────────────────────────────

def test_match_skill_forced_exception(monkeypatch):
    orch = _orch()
    cfg = sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(
        get_forced_skill=lambda: (_ for _ in ()).throw(RuntimeError("no cfg"))))
    assert orch._match_skill("hi") == ""


# ── _execute_multi_model_thinking 收尾分支 ──────────────────────────────

class FakeBlackboard:
    def __init__(self, final=""):
        self.runtime_state = {}
        self.final_response = final
        self.observations = []

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
    def __init__(self, stale_msgs=None, done_on_subscribe=False, session_id="sess", auto_done=True):
        self.sent = []
        self._stale = stale_msgs or []
        self._done_on = done_on_subscribe
        self._sid = session_id
        self._subscribed = False
        self._peek_store = {}
        self._auto_done = auto_done

    async def send(self, msg):
        self.sent.append(msg)
        return msg.msg_id

    async def subscribe(self, channel, cb):
        self._subscribed = True
        if (self._auto_done or self._done_on) and (
            channel.startswith("orchestrator_") or self._done_on
        ):
            m = type("M", (), {"content": {
                "action": "thinking_complete", "tier": "large", "session_id": self._sid,
            }})()
            self._peek_store[channel] = [m]
            try:
                await cb(m)
            except Exception:
                pass

    async def unsubscribe(self, channel, cb):
        self._subscribed = False

    async def peek(self, channel, limit=5):
        return self._peek_store.get(channel, [])

    async def receive(self, channel):
        if self._stale:
            return self._stale.pop(0)
        return []


def _patch_thinking(monkeypatch, bb=None, bus=None, session_id="s"):
    bb = bb or FakeBlackboard()
    bus = bus or FakeBus(session_id=session_id)
    runner_manager = MagicMock()
    runner_manager.start_listening = AsyncMock()
    runner_manager.get_active_runners = MagicMock(return_value={})
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", FakeTurnContext)
    monkeypatch.setattr("modules.thinking.cognition.blackboard.CognitiveBlackboard", lambda **kw: bb)
    monkeypatch.setattr("modules.thinking.core.model_runner.get_runner_manager", lambda *a, **k: runner_manager)
    monkeypatch.setattr("modules.thinking.core.model_runner.remove_runner_manager", AsyncMock())
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus", lambda: bus)
    composer = MagicMock()
    composer._build_supervisor_table = MagicMock(return_value="- 主管")
    composer._build_expert_table = MagicMock(return_value="- 专家")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.security_system.tool_security_gate.get_tool_security_gate", lambda: MagicMock())
    return bb, bus


async def test_execute_init_failure_sets_none(monkeypatch):
    orch = _orch()
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no ctx")))
    monkeypatch.setattr("modules.thinking.core.model_runner.remove_runner_manager", AsyncMock())
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus",
                        lambda: FakeBus(session_id="s"))
    out = await orch._execute_multi_model_thinking("q", "s", {}, None, context=None)
    assert out["blackboard"] is None


async def test_execute_with_prev_user_time_no_ctx(monkeypatch):
    orch = _orch()
    bb, bus = _patch_thinking(monkeypatch)
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no ctx")))
    context = [
        {"role": "user", "content": "旧", "timestamp": 42.0},
        {"role": "user", "content": "新"},
    ]
    out = await orch._execute_multi_model_thinking("q", "s", {}, None, context=context)
    assert out["blackboard"] is None  # turn_context 为 None → 跳过注入


async def test_execute_history_content_not_str(monkeypatch):
    """history 中有非字符串 content → 跳过（542->561）"""
    orch = _orch()
    bb, bus = _patch_thinking(monkeypatch)
    context = [
        {"role": "user", "content": None},
        {"role": "user", "content": 123},
        {"role": "assistant", "content": "正常文本"},
    ]
    out = await orch._execute_multi_model_thinking("q", "s", {}, None, context=context)
    assert out["blackboard"] is bb
    assert any("正常文本" in c for _, c in bb.observations)


async def test_execute_guidance_injection_failure(monkeypatch):
    """set_session_guidance 抛异常 → 非致命（572-573）"""
    orch = _orch()
    bb, bus = _patch_thinking(monkeypatch)
    monkeypatch.setattr("modules.thinking.probes.probe_tools.set_session_guidance",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no guidance")))
    push = AsyncMock()
    monkeypatch.setattr("modules.thinking.frontend_channel.push_content", push)
    from modules.thinking import api_stream as api_mod
    monkeypatch.setattr(api_mod.connection_manager, "active_connections", {})
    monkeypatch.setattr(api_mod, "_build_event", lambda **kw: {"event": kw.get("event")})
    out = await orch._execute_multi_model_thinking(
        "q", "s", {"inner_thoughts": "独白"}, None,
    )
    assert out["blackboard"] is bb


async def test_execute_mental_push_failure(monkeypatch):
    """push_content 抛异常 → 非致命（596-597）"""
    orch = _orch()
    bb, bus = _patch_thinking(monkeypatch)
    from modules.thinking import api_stream as api_mod
    monkeypatch.setattr(api_mod.connection_manager, "active_connections", {})
    monkeypatch.setattr(api_mod, "_build_event", lambda **kw: {"event": kw.get("event")})
    monkeypatch.setattr("modules.thinking.frontend_channel.push_content",
                        AsyncMock(side_effect=RuntimeError("push down")))
    out = await orch._execute_multi_model_thinking(
        "q", "s", {"inner_thoughts": "独白"}, None,
    )
    assert out["blackboard"] is bb


async def test_execute_activation_failure(monkeypatch):
    """bus.send 抛异常 → 直接激活失败非致命（627-628）"""
    orch = _orch()

    class BoomBus(FakeBus):
        async def send(self, msg):
            raise RuntimeError("bus down")

    bb, bus = _patch_thinking(monkeypatch, bus=BoomBus(session_id="s"))
    out = await orch._execute_multi_model_thinking("q", "s", {}, None)
    assert out["blackboard"] is bb


async def test_execute_stale_messages_and_final_response(monkeypatch):
    """已有 stale thinking_complete → done；blackboard.final_response 兜底（686-740）"""
    orch = _orch()
    stale = [type("M", (), {"content": {
        "action": "thinking_complete", "tier": "large", "session_id": "s",
    }})()]
    bus = FakeBus(stale_msgs=stale)
    bb = FakeBlackboard(final="黑板回复")
    bb, bus = _patch_thinking(monkeypatch, bb=bb, bus=bus)
    out = await orch._execute_multi_model_thinking("q", "s", {}, None)
    assert out["response"] == "黑板回复"


async def test_execute_done_but_no_response(monkeypatch):
    """完成信号但无 final_response → 系统通知（742-744）"""
    orch = _orch()
    bus = FakeBus(done_on_subscribe=True, session_id="s")
    bb = FakeBlackboard(final="")
    bb, bus = _patch_thinking(monkeypatch, bb=bb, bus=bus)
    out = await orch._execute_multi_model_thinking("q", "s", {}, None)
    assert "系统通知" in out["response"]


async def test_execute_timeout_no_response(monkeypatch):
    """等待超时且无回复 → 超时提示（746-751）"""
    orch = _orch()

    class WaitBus(FakeBus):
        def __init__(self, *a, **k):
            super().__init__(auto_done=False, *a, **k)

    bb = FakeBlackboard(final="")
    bb, bus = _patch_thinking(monkeypatch, bb=bb, bus=WaitBus(session_id="s"))
    # 时间单调快速推进：wait_for 抛超时后，elapsed 立刻超过 MAX_WALL_TIME 退出循环
    n = {"v": 0}

    def fake_time():
        n["v"] += 1
        return n["v"] * 90000.0

    def fake_wait_for(awaitable, timeout=None):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(mmo.time, "time", fake_time)
    monkeypatch.setattr(mmo.asyncio, "wait_for", fake_wait_for)
    out = await orch._execute_multi_model_thinking("q", "s", {}, None)
    assert "超时" in out["response"]


async def test_execute_subscribe_unsubscribe_errors(monkeypatch):
    """订阅回调 peek 异常 / unsubscribe 异常 → 非致命"""
    orch = _orch()

    class FussyBus(FakeBus):
        async def subscribe(self, channel, cb):
            self._subscribed = True

        async def unsubscribe(self, channel, cb):
            raise RuntimeError("unsub down")

    bb = FakeBlackboard(final="回复")
    bb, bus = _patch_thinking(monkeypatch, bb=bb, bus=FussyBus())
    out = await orch._execute_multi_model_thinking("q", "s", {}, None)
    assert out["blackboard"] is bb


# ── _review_output 额外分支 ─────────────────────────────────────────────

async def test_review_output_security_block_none():
    orch = _orch()
    orch._output_reviewer = MagicMock()
    orch._output_reviewer.review = AsyncMock(return_value="clean")
    bb = MagicMock()
    bb.has_security_block = MagicMock(return_value=True)
    bb.get_security_block = MagicMock(return_value=None)
    assert await orch._review_output("raw", "user", None, bb) == "clean"


# ── _maybe_evolve_values 异常 ───────────────────────────────────────────

async def test_maybe_evolve_values_exception(monkeypatch):
    orch = _orch()
    monkeypatch.setattr("modules.thinking.conscience.get_conscience",
                        lambda: (_ for _ in ()).throw(RuntimeError("no conscience")))
    await orch._maybe_evolve_values("删除生产库", "这是一段超过二十个字符的响应内容内容内容")  # 不抛异常


async def test_execute_blackboard_inject_failure(monkeypatch):
    """Blackboard 注入 SecurityGate 失败 → 非致命（479-480）"""
    orch = _orch()
    bb, bus = _patch_thinking(monkeypatch)
    monkeypatch.setattr("modules.security_system.tool_security_gate.get_tool_security_gate",
                        lambda: (_ for _ in ()).throw(RuntimeError("no gate")))
    out = await orch._execute_multi_model_thinking("q", "s", {}, None)
    assert out["blackboard"] is bb


async def test_execute_history_all_invalid(monkeypatch):
    """context 全无效 content → history_lines 空 → else 分支（542->561）"""
    orch = _orch()
    bb, bus = _patch_thinking(monkeypatch)
    context = [
        {"role": "user", "content": None},
        {"role": "assistant", "content": 123},
    ]
    out = await orch._execute_multi_model_thinking("q", "s", {}, None, context=context)
    assert out["blackboard"] is bb


async def test_execute_dialog_history_write_error(monkeypatch):
    """_session_dialog_history 写入抛异常 → pass（554-555）"""
    orch = _orch()

    class BoomLock:
        def __enter__(self):
            raise RuntimeError("lock down")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mmo, "_session_dialog_history_lock", BoomLock())
    bb, bus = _patch_thinking(monkeypatch)
    context = [{"role": "user", "content": "有效文本"}]
    out = await orch._execute_multi_model_thinking("q", "s", {}, None, context=context)
    assert out["blackboard"] is bb


async def test_execute_inner_thoughts_no_blackboard(monkeypatch):
    """inner_thoughts 存在但 blackboard 为 None → 只走 set_session_guidance（563->569）"""
    orch = _orch()
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no ctx")))
    monkeypatch.setattr("modules.thinking.core.model_runner.remove_runner_manager", AsyncMock())
    set_guidance = MagicMock()
    monkeypatch.setattr("modules.thinking.probes.probe_tools.set_session_guidance", set_guidance)
    out = await orch._execute_multi_model_thinking(
        "q", "s", {"inner_thoughts": "独白"}, None,
    )
    assert out["blackboard"] is None
    set_guidance.assert_called_once()


async def test_execute_callback_peek_error(monkeypatch):
    """订阅回调 bus.peek 抛异常 → 消息回调异常非致命（660-661）"""
    orch = _orch()

    class FussyBus(FakeBus):
        async def peek(self, channel, limit=5):
            raise RuntimeError("peek down")

    bb = FakeBlackboard(final="回复")
    bb, bus = _patch_thinking(monkeypatch, bb=bb, bus=FussyBus(session_id="s"))
    out = await orch._execute_multi_model_thinking("q", "s", {}, None)
    assert out["blackboard"] is bb


async def test_execute_pending_delegations(monkeypatch):
    """_has_pending_delegations 有未完成委托 → 延长等待（665-713）"""
    orch = _orch()

    bb = FakeBlackboard(final="")
    runner_manager = MagicMock()
    runner_manager.start_listening = AsyncMock()
    thinker = MagicMock()
    thinker._pending_delegations = {"d1": True}
    runner_obj = MagicMock()
    runner_obj.tier = "large"
    runner_obj._thinker = thinker
    runner_manager.get_active_runners = MagicMock(return_value={"r1": runner_obj})
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", FakeTurnContext)
    monkeypatch.setattr("modules.thinking.cognition.blackboard.CognitiveBlackboard", lambda **kw: bb)
    monkeypatch.setattr("modules.thinking.core.model_runner.get_runner_manager", lambda *a, **k: runner_manager)
    monkeypatch.setattr("modules.thinking.core.model_runner.remove_runner_manager", AsyncMock())
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus",
                        lambda: FakeBus(session_id="s", auto_done=False))
    composer = MagicMock()
    composer._build_supervisor_table = MagicMock(return_value="")
    composer._build_expert_table = MagicMock(return_value="")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.security_system.tool_security_gate.get_tool_security_gate", lambda: MagicMock())
    # wait_for：第 1 次超时（触发 pending 检查），第 2 次超时并设置黑板回复 → 循环退出
    state = {"n": 0}

    async def fake_wait_for(awaitable, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            raise asyncio.TimeoutError()
        bb.final_response = "委托完成回复"
        raise asyncio.TimeoutError()

    monkeypatch.setattr(mmo.asyncio, "wait_for", fake_wait_for)
    t = {"n": 0}

    def fake_time():
        t["n"] += 1
        return t["n"] * 0.1  # 缓慢推进，保证循环内 elapsed 远小于 MAX_WALL_TIME

    monkeypatch.setattr(mmo.time, "time", fake_time)
    out = await orch._execute_multi_model_thinking("q", "s", {}, None)
    assert out["response"] == "委托完成回复"


async def test_has_pending_delegations_missing_info(monkeypatch):
    """会话不在 registry（初始化失败未注册）→ _has_pending_delegations 返回 False（668->677）"""
    orch = _orch()
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no ctx")))
    monkeypatch.setattr("modules.thinking.core.model_runner.get_runner_manager",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no mgr")))
    monkeypatch.setattr("modules.thinking.core.model_runner.remove_runner_manager", AsyncMock())
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus",
                        lambda: FakeBus(session_id="ghost", auto_done=False))

    def fake_wait_for(awaitable, timeout=None):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(mmo.asyncio, "wait_for", fake_wait_for)
    # 时间序：t_wait_start 之前返回 0；第 1 次 while 条件 100（进入）；
    # elapsed 100；第 2 次 while 条件 100000（退出）
    t = {"n": 0}

    def fake_time():
        t["n"] += 1
        if t["n"] <= 6:
            return 0.0
        if t["n"] in (7, 8):
            return 100.0
        return 100000.0

    monkeypatch.setattr(mmo.time, "time", fake_time)
    out = await orch._execute_multi_model_thinking("q", "ghost", {}, None)
    assert out["response"]  # 无回复 → 超时提示


async def test_has_pending_delegations_info_no_manager(monkeypatch):
    """registry 有会话但 manager 未注册（start_listening 失败）→ False（670->677）"""
    orch = _orch()
    bb = FakeBlackboard(final="")
    runner_manager = MagicMock()
    runner_manager.start_listening = AsyncMock(side_effect=RuntimeError("listen fail"))
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", FakeTurnContext)
    monkeypatch.setattr("modules.thinking.cognition.blackboard.CognitiveBlackboard", lambda **kw: bb)
    monkeypatch.setattr("modules.thinking.core.model_runner.get_runner_manager", lambda *a, **k: runner_manager)
    monkeypatch.setattr("modules.thinking.core.model_runner.remove_runner_manager", AsyncMock())
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus",
                        lambda: FakeBus(session_id="s", auto_done=False))
    composer = MagicMock()
    composer._build_supervisor_table = MagicMock(return_value="")
    composer._build_expert_table = MagicMock(return_value="")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.security_system.tool_security_gate.get_tool_security_gate", lambda: MagicMock())
    state = {"n": 0}

    async def fake_wait_for(awaitable, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            raise asyncio.TimeoutError()
        bb.final_response = "回复"
        raise asyncio.TimeoutError()

    monkeypatch.setattr(mmo.asyncio, "wait_for", fake_wait_for)
    t = {"n": 0}

    def fake_time():
        t["n"] += 1
        return t["n"] * 0.1

    monkeypatch.setattr(mmo.time, "time", fake_time)
    out = await orch._execute_multi_model_thinking("q", "s", {}, None)
    assert out["response"] == "回复"


async def test_has_pending_delegations_no_thinker(monkeypatch):
    """runner 存在但无 _thinker / 无 pending → False（673->672, 675-676）"""
    orch = _orch()
    bb = FakeBlackboard(final="")
    runner_manager = MagicMock()
    runner_manager.start_listening = AsyncMock()
    # 一个无 _thinker 的 runner + 一个 _thinker 无 pending_delegations 的 runner
    r1 = MagicMock()
    r1.tier = "expert"
    r2 = MagicMock()
    r2.tier = "large"
    r2._thinker = MagicMock()
    r2._thinker._pending_delegations = {}
    runner_manager.get_active_runners = MagicMock(return_value={"a": r1, "b": r2})
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", FakeTurnContext)
    monkeypatch.setattr("modules.thinking.cognition.blackboard.CognitiveBlackboard", lambda **kw: bb)
    monkeypatch.setattr("modules.thinking.core.model_runner.get_runner_manager", lambda *a, **k: runner_manager)
    monkeypatch.setattr("modules.thinking.core.model_runner.remove_runner_manager", AsyncMock())
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus",
                        lambda: FakeBus(session_id="s", auto_done=False))
    composer = MagicMock()
    composer._build_supervisor_table = MagicMock(return_value="")
    composer._build_expert_table = MagicMock(return_value="")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.security_system.tool_security_gate.get_tool_security_gate", lambda: MagicMock())
    state = {"n": 0}

    async def fake_wait_for(awaitable, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            raise asyncio.TimeoutError()
        bb.final_response = "回复"
        raise asyncio.TimeoutError()

    monkeypatch.setattr(mmo.asyncio, "wait_for", fake_wait_for)
    t = {"n": 0}

    def fake_time():
        t["n"] += 1
        return t["n"] * 0.1

    monkeypatch.setattr(mmo.time, "time", fake_time)
    out = await orch._execute_multi_model_thinking("q", "s", {}, None)
    assert out["response"] == "回复"


async def test_has_pending_delegations_exception(monkeypatch):
    """get_active_runners 抛异常 → _has_pending_delegations except 分支（675-676）"""
    orch = _orch()
    bb = FakeBlackboard(final="")
    runner_manager = MagicMock()
    runner_manager.start_listening = AsyncMock()
    runner_manager.get_active_runners = MagicMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", FakeTurnContext)
    monkeypatch.setattr("modules.thinking.cognition.blackboard.CognitiveBlackboard", lambda **kw: bb)
    monkeypatch.setattr("modules.thinking.core.model_runner.get_runner_manager", lambda *a, **k: runner_manager)
    monkeypatch.setattr("modules.thinking.core.model_runner.remove_runner_manager", AsyncMock())
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus",
                        lambda: FakeBus(session_id="s", auto_done=False))
    composer = MagicMock()
    composer._build_supervisor_table = MagicMock(return_value="")
    composer._build_expert_table = MagicMock(return_value="")
    monkeypatch.setattr("config.prompts.composer.PromptComposer", lambda: composer)
    monkeypatch.setattr("modules.security_system.tool_security_gate.get_tool_security_gate", lambda: MagicMock())
    state = {"n": 0}

    async def fake_wait_for(awaitable, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            raise asyncio.TimeoutError()
        bb.final_response = "回复"
        raise asyncio.TimeoutError()

    monkeypatch.setattr(mmo.asyncio, "wait_for", fake_wait_for)
    t = {"n": 0}

    def fake_time():
        t["n"] += 1
        return t["n"] * 0.1

    monkeypatch.setattr(mmo.time, "time", fake_time)
    out = await orch._execute_multi_model_thinking("q", "s", {}, None)
    assert out["response"] == "回复"


async def test_execute_stale_nonmatching_then_match(monkeypatch):
    """stale 消息不匹配 → 继续循环 receive，再匹配 → done（695）"""
    orch = _orch()
    stale_msgs = [
        type("M", (), {"content": {"action": "other", "tier": "large", "session_id": "s"}})(),
        type("M", (), {"content": {
            "action": "thinking_complete", "tier": "large", "session_id": "s",
        }})(),
    ]
    bus = FakeBus(stale_msgs=stale_msgs, session_id="s")
    bb, bus = _patch_thinking(monkeypatch, bus=bus)
    out = await orch._execute_multi_model_thinking("q", "s", {}, None)
    assert out["blackboard"] is bb


async def test_execute_recover_final_response_after_loop(monkeypatch):
    """循环后从黑板恢复 final_response（740）"""
    orch = _orch()

    class LateBus(FakeBus):
        def __init__(self, *a, **k):
            super().__init__(auto_done=False, *a, **k)

    bb = FakeBlackboard(final="")
    bb, bus = _patch_thinking(monkeypatch, bb=bb, bus=LateBus(session_id="s"))
    real_wait_for = asyncio.wait_for
    state = {"n": 0}

    async def fake_wait_for(awaitable, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            raise asyncio.TimeoutError()
        bb.final_response = "迟到的黑板回复"
        return await real_wait_for(awaitable, timeout=0.001)

    monkeypatch.setattr(mmo.asyncio, "wait_for", fake_wait_for)
    out = await orch._execute_multi_model_thinking("q", "s", {}, None)
    assert out["response"] == "迟到的黑板回复"


async def test_maybe_evolve_values_no_risk_long_response():
    orch = _orch()
    await orch._maybe_evolve_values("普通", "这是一段超过二十个字符的正常响应内容内容内容内容内容")  # 860 return


async def test_maybe_evolve_values_full_path(monkeypatch):
    """风险关键词命中 + 完整演化路径（855-871）"""
    orch = _orch()
    cons = MagicMock()
    cons.review_and_evolve = AsyncMock()
    monkeypatch.setattr("modules.thinking.conscience.get_conscience", lambda: cons)
    client = MagicMock()
    monkeypatch.setattr("infra.model.small_model_client.SmallModelClient", lambda **k: client)
    cfg = sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(
        SMALL_MODEL_API_KEY="k", LARGE_MODEL_API_KEY="lk",
        SMALL_MODEL_API_URL="u", LARGE_MODEL_API_URL="lu",
    ))
    await orch._maybe_evolve_values("删除生产库", "这是一段超过二十个字符的响应内容内容内容")
    cons.review_and_evolve.assert_awaited_once()
    assert cons._model_client is client
