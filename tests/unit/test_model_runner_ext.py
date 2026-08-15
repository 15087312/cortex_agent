"""model_runner 扩展测试：Runner 生命周期 / Manager 监听 / 消息处理"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import modules.thinking.core.model_runner as mr_mod
from modules.thinking.core.model_runner import (
    ModelRunner,
    ModelRunnerManager,
    get_runner_manager,
    remove_runner_manager,
    reject_session_user_responses,
    _runner_managers,
    _runner_managers_lock,
)


def _runner(**kw):
    inst = MagicMock()
    ident = MagicMock()
    ident.model_id = kw.get("model_id", "large_primary")
    ident.tier = kw.get("tier", "large")
    ident.name = "总指挥"
    ident.role = "orchestrator"
    inst.identity = ident
    r = ModelRunner.__new__(ModelRunner)
    r.instance = inst
    r.identity = ident
    r.model_id = ident.model_id
    r.tier = ident.tier
    r.blackboard = kw.get("blackboard", MagicMock())
    r.turn_context = None
    r.session_id = "s1"
    r.manager = kw.get("manager", None)
    r._running = kw.get("running", False)
    r._task = None
    r._task_description = kw.get("task_description", "")
    r._return_to_model_id = ""
    r._status = "idle"
    r._status_detail = ""
    r._react_loop = None
    r._think_loop_state = None
    r._pending_guidance = []
    r._thinker = kw.get("thinker", None)
    r._started_at = 0.0
    r._active_skill = None
    r.MAX_CHAT_TOOL_TURNS = 25
    r.logger = MagicMock()
    return r


# ── ModelRunner 生命周期 ───────────────────────────────────────────────

async def test_start_running_returns(monkeypatch):
    r = _runner(running=True)
    await r.start("任务")  # 已在运行 → 直接返回
    assert r._task_description == ""


async def test_start_cleans_old_thinker(monkeypatch):
    r = _runner()
    thinker = MagicMock()
    thinker.close = AsyncMock()
    r._thinker = thinker
    r._run_task = AsyncMock()
    await r.start("新任务")
    thinker.close.assert_awaited_once()
    assert r._running is True


async def test_stop_not_running():
    r = _runner(running=False)
    await r.stop()  # 不抛异常


async def test_stop_cancels_task(monkeypatch):
    r = _runner(running=True)
    task = asyncio.create_task(asyncio.sleep(30))
    r._task = task
    await r.stop()
    assert r._running is False


def test_properties():
    thinker = MagicMock()
    thinker._context_tokens = 123
    thinker._context_window_size = 65536
    r = _runner(thinker=thinker)
    assert r.context_tokens == 123
    assert r.context_window_size == 65536
    r2 = _runner()
    assert r2.context_tokens == 0
    assert r2.context_window_size == 128000


def test_update_loop_state_and_inject_guidance():
    r = _runner()
    r._update_loop_state(think_round=2, think_max=5, think_wait=3.5)
    assert r._think_loop_state == {"round": 2, "max": 5, "wait": 3.5}
    assert r._react_loop is None
    r.inject_guidance("引导")
    assert r._pending_guidance == ["引导"]


def test_get_runtime_expert_class_error(monkeypatch):
    def boom(role):
        raise RuntimeError("no")
    monkeypatch.setattr("modules.thinking.runtime_expert.get_runtime_expert_class", boom)
    assert ModelRunner._get_runtime_expert_class("x") is None


# ── ModelRunnerManager ─────────────────────────────────────────────────

def _mgr(monkeypatch=None):
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    bus.receive = AsyncMock(return_value=[])
    if monkeypatch is not None:
        import modules.thinking.communication.interface as iface_mod
        monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)
    m = ModelRunnerManager.__new__(ModelRunnerManager)
    m.blackboard = MagicMock()
    m.turn_context = None
    m.session_id = "s1"
    m._channel = "model_runner_manager_s1"
    m._runners = {}
    m._count_by_tier = {"large": 0, "supervisor": 0, "expert": 0}
    m._lock = MagicMock()
    m._lock.__enter__ = MagicMock(return_value=None)
    m._lock.__exit__ = MagicMock(return_value=False)
    m._probe_map = {}
    m._bus = bus
    m._listen_task = None
    m._running = False
    m._message_event = asyncio.Event()
    m._orphan_event = asyncio.Event()
    return m


async def test_start_stop_listening(monkeypatch):
    m = _mgr(monkeypatch)
    monkeypatch.setattr("modules.thinking.identity.load_external_identities", lambda: None)
    await m.start_listening()
    assert m._running is True
    m._bus.subscribe.assert_awaited_once()
    await m.start_listening()  # 已运行直接返回
    await m.stop_listening()
    assert m._running is False
    m._bus.unsubscribe.assert_awaited_once()


def test_on_runner_message():
    m = _mgr()
    m._message_event = asyncio.Event()
    m._on_runner_message(None)
    assert m._message_event.is_set()


def test_inject_to_runner():
    m = _mgr()
    r = _runner()
    r.inject_guidance = MagicMock()
    m._runners = {"m1": r}
    assert m.inject_to_runner("m1", "persona_inject", "内容") is True
    r.inject_guidance.assert_called_once_with("内容")
    assert m.inject_to_runner("none", "persona_inject", "x") is False
    assert m.inject_to_runner("m1", "bogus_action", "x") is False


def test_get_active_runners_and_list():
    m = _mgr()
    r = _runner()
    m._runners = {"m1": r}
    assert m.get_active_runners() == {"m1": r}
    listing = m.list_runners()
    assert listing[0]["model_id"] == "large_primary"


async def test_stop_runner_missing():
    m = _mgr()
    assert await m.stop_runner("none") is False


async def test_handle_probe_started_session_mismatch(monkeypatch):
    m = _mgr(monkeypatch)
    m.start_runner = AsyncMock(return_value="m1")
    await m._handle_probe_started({
        "identity_key": "expert_1", "task_description": "任务", "return_to_session_id": "other",
    })
    m.start_runner.assert_not_awaited()


async def test_handle_probe_started_incomplete(monkeypatch):
    m = _mgr(monkeypatch)
    m.start_runner = AsyncMock()
    await m._handle_probe_started({"identity_key": "", "task_description": ""})
    m.start_runner.assert_not_awaited()


async def test_handle_probe_started_success(monkeypatch):
    m = _mgr(monkeypatch)
    m.start_runner = AsyncMock(return_value="m1")
    await m._handle_probe_started({
        "identity_key": "expert_1", "task_description": "任务", "return_to_session_id": "s1",
    })
    m.start_runner.assert_awaited_once()


async def test_handle_probe_stopped(monkeypatch):
    m = _mgr(monkeypatch)
    r = _runner()
    r.stop = AsyncMock()
    m._runners = {"m1": r}
    m._probe_map = {"p1": "m1"}
    m.stop_runner = AsyncMock(return_value=True)
    await m._handle_probe_stopped({"probe_id": "p1"})
    m.stop_runner.assert_awaited_once_with("m1")
    await m._handle_probe_stopped({"probe_id": "none"})  # 找不到


async def test_handle_terminate_session(monkeypatch):
    m = _mgr(monkeypatch)
    r1 = _runner()
    r1.stop = AsyncMock()
    m._runners = {"m1": r1}
    m._running = True
    await m._handle_terminate_session({"reason": "风险", "risk_level": "high"})
    r1.stop.assert_awaited_once()
    assert m._running is False


def test_sweep_orphaned_runners():
    m = _mgr()
    done_task = MagicMock()
    done_task.done = MagicMock(return_value=True)
    r_done = _runner(model_id="a", tier="expert")
    r_done._task = done_task
    pending_task = MagicMock()
    pending_task.done = MagicMock(return_value=False)
    r_pending = _runner(model_id="b", tier="large")
    r_pending._task = pending_task
    m._runners = {"a": r_done, "b": r_pending}
    m._count_by_tier = {"large": 1, "supervisor": 0, "expert": 1}
    m._probe_map = {"p": "a"}
    m._sweep_orphaned_runners()
    assert "a" not in m._runners
    assert "b" in m._runners


def test_manager_resolve_user_response():
    m = _mgr()
    r = _runner()
    r._pending_user_responses = {"rid1": MagicMock()}
    r.resolve_user_response = MagicMock(return_value=True)
    m._runners = {"m1": r}
    assert m.resolve_user_response("rid1", {}) is True
    assert m.resolve_user_response("none", {}) is False


async def test_manager_shutdown(monkeypatch):
    m = _mgr(monkeypatch)
    m.stop_listening = AsyncMock()
    m.stop_runner = AsyncMock(return_value=True)
    r = _runner()
    m._runners = {"m1": r}
    await m.shutdown()
    m.stop_runner.assert_awaited_once()


async def test_drain_runner_messages(monkeypatch):
    m = _mgr(monkeypatch)
    m._running = True
    messages = [
        type("M", (), {"content": {"action": "probe_started", "identity_key": "e", "task_description": "t", "return_to_session_id": "s1"}})(),
        type("M", (), {"content": '{"action": "probe_stopped", "probe_id": "p1"}'})(),
        type("M", (), {"content": {"action": "terminate_session", "reason": "x"}})(),
    ]
    m._bus.receive = AsyncMock(side_effect=[messages, []])
    m._handle_probe_started = AsyncMock()
    m._handle_probe_stopped = AsyncMock()
    m._handle_terminate_session = AsyncMock()
    await m._drain_runner_messages()
    m._handle_probe_started.assert_awaited_once()
    m._handle_probe_stopped.assert_awaited_once()
    m._handle_terminate_session.assert_awaited_once()


# ── 模块级函数 ─────────────────────────────────────────────────────────

def test_get_runner_manager_create_and_reuse(monkeypatch):
    monkeypatch.setattr(mr_mod, "_runner_managers", {})
    mgr1 = get_runner_manager("s1")
    assert mgr1.session_id == "s1"
    bb = MagicMock()
    mgr2 = get_runner_manager("s1", blackboard=bb, turn_context=MagicMock())
    assert mgr2 is mgr1
    assert mgr2.blackboard is bb


async def test_remove_runner_manager(monkeypatch):
    monkeypatch.setattr(mr_mod, "_runner_managers", {})
    mgr = MagicMock()
    mgr.shutdown = AsyncMock()
    monkeypatch.setattr(mr_mod, "_runner_managers", {"s1": mgr})
    await remove_runner_manager("s1")
    mgr.shutdown.assert_awaited_once()
    await remove_runner_manager("none")  # 不存在


def test_reject_session_user_responses():
    r = _runner()
    # 显式创建事件循环：CI(3.11) 同步测试线程无当前 loop，asyncio.Future() 会 RuntimeError；
    # 保持 loop 活跃到测试结束（fut.set_result 需要 loop 调度）
    _loop = asyncio.new_event_loop()
    try:
        old_loop = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        old_loop = None  # 3.13：无当前 loop 属正常（py3.13 get_event_loop 不再隐式创建）
    asyncio.set_event_loop(_loop)
    try:
        fut = asyncio.Future()
        r._pending_user_responses = {"rid": fut}
        import modules.thinking.core.model_runner as mr
        orig = dict(mr._runner_managers)
        try:
            mr._runner_managers = {"s1": type("M", (), {"_runners": {"m1": r}})()}
            assert reject_session_user_responses("s1") == 1
            assert fut.done()
            assert reject_session_user_responses("") == 0
        finally:
            mr._runner_managers = orig
    finally:
        _loop.close()
        # 还原之前的 loop（而不是 set_event_loop(None)）：
        # 若残留 None，后续测试调用 get_event_loop_policy().get_event_loop() 会 RuntimeError
        asyncio.set_event_loop(old_loop)
