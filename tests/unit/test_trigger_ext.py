"""perception/trigger 扩展测试：补齐未覆盖路径（异常分支 / 定时循环 / LLM / 推送）"""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import modules.perception.trigger as mod
from modules.perception.trigger import ProactiveTrigger


def _make_event(ratio=0.5, regions=()):
    e = MagicMock()
    e.payload = {"change_ratio": ratio, "changed_regions": list(regions)}
    return e


# ── 生命周期与闸门异常分支 ──

def test_outreach_trigger_allowed_exception(monkeypatch):
    monkeypatch.setattr(
        mod.ProactiveTrigger, "_get_enabled_outreach_sessions",
        lambda self: (_ for _ in ()).throw(RuntimeError()),
    )
    assert mod.outreach_trigger_allowed() is False


def test_start_timer_failure(monkeypatch):
    tr = ProactiveTrigger()
    bus = MagicMock()
    bus.subscribe.return_value = "sub-1"
    monkeypatch.setattr(mod.asyncio, "get_event_loop", lambda: (_ for _ in ()).throw(RuntimeError()))
    with patch.object(mod.logger, "warning") as w:
        tr.start(bus)
    w.assert_called_once()
    assert tr._sub_id == "sub-1"


def test_stop_no_sub_id():
    tr = ProactiveTrigger()
    tr._event_bus = MagicMock()
    tr._sub_id = ""
    tr.stop()


def test_stop_no_event_bus():
    tr = ProactiveTrigger()
    tr._timer_task = None
    tr.stop()


def test_stop_timer_cancel_raises():
    tr = ProactiveTrigger()
    tr._event_bus = MagicMock()
    tr._sub_id = "sub"
    task = MagicMock()
    task.cancel.side_effect = RuntimeError()
    tr._timer_task = task
    tr.stop()
    assert tr._timer_task is None


# ── SCREEN_DIFF 事件判定 ──

def test_on_screen_diff_not_active(monkeypatch):
    tr = ProactiveTrigger()
    monkeypatch.setattr(tr, "_qt_active", lambda: False)
    tr._on_screen_diff(_make_event(0.5))


def test_on_screen_diff_guards(monkeypatch):
    """逐层闸门：screen 关闭 / 幅度不足 / 概率 / 综合冷却 / screen 冷却 / 间隔 → 最后成功调度"""
    tr = ProactiveTrigger()
    monkeypatch.setattr(tr, "_qt_active", lambda: True)
    run = MagicMock()
    monkeypatch.setattr(tr, "_run_in_main_loop", run)
    cooldown = MagicMock(side_effect=[False, True, True, True])
    screen_cd = MagicMock(side_effect=[False, True, True])
    rule = MagicMock(side_effect=[False, True])
    monkeypatch.setattr(tr, "_cooldown_ok", cooldown)
    monkeypatch.setattr(tr, "_screen_cooldown_ok", screen_cd)
    monkeypatch.setattr(tr, "_rule_ready", rule)
    monkeypatch.setattr(mod.random, "random", lambda: 0.9)

    sessions = {
        "s_a": {"screen": {"enabled": False}},
        "s_b": {"screen": {"enabled": True, "change_ratio": 1.0}},
        "s_c": {"screen": {"enabled": True, "change_ratio": 0.1, "probability": 0.5}},
        "s_d": {"screen": {"enabled": True, "change_ratio": 0.1, "probability": 1.0}},
        "s_e": {"screen": {"enabled": True, "change_ratio": 0.1, "probability": 1.0}},
        "s_f": {"screen": {"enabled": True, "change_ratio": 0.1, "probability": 1.0}},
        "s_g": {"screen": {"enabled": True, "change_ratio": 0.1, "probability": 1.0}},
    }
    monkeypatch.setattr(tr, "_get_enabled_outreach_sessions", lambda: sessions)
    tr._on_screen_diff(_make_event(0.5))
    run.assert_called_once()


def test_on_screen_diff_exception(monkeypatch):
    tr = ProactiveTrigger()
    monkeypatch.setattr(tr, "_qt_active", lambda: True)
    monkeypatch.setattr(tr, "_get_enabled_outreach_sessions", lambda: {"s1": None})
    with patch.object(mod.logger, "debug") as dbg:
        tr._on_screen_diff(_make_event(0.5))
    dbg.assert_called_once()


# ── 定时循环 ──

def test_check_loop_handles_error_and_cancel(monkeypatch):
    tr = ProactiveTrigger()
    sleep = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    monkeypatch.setattr(mod.asyncio, "sleep", sleep)
    monkeypatch.setattr(tr, "_run_periodic_check", AsyncMock(side_effect=[RuntimeError(), None]))
    with patch.object(mod.logger, "debug"):
        asyncio.run(tr._check_loop())


def test_run_periodic_check_not_active(monkeypatch):
    tr = ProactiveTrigger()
    monkeypatch.setattr(tr, "_qt_active", lambda: False)
    asyncio.run(tr._run_periodic_check())


def test_run_periodic_check_rules(monkeypatch):
    """schedule / idle / time_windows 各自命中，冷却拦截，单会话异常不中断"""
    tr = ProactiveTrigger()
    monkeypatch.setattr(tr, "_qt_active", lambda: True)
    sessions = {
        "s1": {"_mode": "schedule"},
        "s2": {"idle": {"_mode": "idle"}},
        "s3": {"_mode": "time_window"},
        "s4": {"_mode": "cooldown"},
        "s5": {"_mode": "raise"},
    }
    monkeypatch.setattr(tr, "_get_enabled_outreach_sessions", lambda: sessions)
    monkeypatch.setattr(tr, "_cooldown_ok", lambda sid, cfg: cfg.get("_mode") != "cooldown")
    monkeypatch.setattr(tr, "_rule_ready", lambda sid, key, iv: True)

    def _schedule(cfg):
        if cfg.get("_mode") == "raise":
            raise RuntimeError("boom")
        return cfg.get("_mode") == "schedule"

    monkeypatch.setattr(tr, "_check_schedule", _schedule)
    monkeypatch.setattr(tr, "_check_idle_rule", lambda cfg: cfg.get("_mode") == "idle")
    monkeypatch.setattr(tr, "_check_time_windows", lambda cfg: cfg.get("_mode") == "time_window")

    calls = []

    async def _try(sid, reason, **kw):
        calls.append((sid, reason))

    monkeypatch.setattr(tr, "_try_outreach", _try)
    asyncio.run(tr._run_periodic_check())
    assert ("s1", "schedule") in calls
    assert ("s2", "idle") in calls
    assert ("s3", "time_window") in calls


def test_run_periodic_check_fallthrough(monkeypatch):
    """无任何规则命中 → 正常落到下个会话"""
    tr = ProactiveTrigger()
    monkeypatch.setattr(tr, "_qt_active", lambda: True)
    monkeypatch.setattr(tr, "_get_enabled_outreach_sessions", lambda: {"s6": {"_mode": "none"}})
    monkeypatch.setattr(tr, "_cooldown_ok", lambda sid, cfg: True)
    monkeypatch.setattr(tr, "_rule_ready", lambda sid, key, iv: True)
    monkeypatch.setattr(tr, "_check_schedule", lambda cfg: False)
    monkeypatch.setattr(tr, "_check_idle_rule", lambda cfg: False)
    monkeypatch.setattr(tr, "_check_time_windows", lambda cfg: False)

    async def _try(sid, reason, **kw):
        raise AssertionError("不应触发")

    monkeypatch.setattr(tr, "_try_outreach", _try)
    asyncio.run(tr._run_periodic_check())


# ── _try_outreach 成功 / 空响应 / 异常 ──

def _try_outreach_setup(monkeypatch):
    tr = ProactiveTrigger()
    monkeypatch.setattr(tr, "_get_session_outreach_config", lambda sid: {"cooldown_minutes": 15})
    monkeypatch.setattr(tr, "_cooldown_ok", lambda sid, cfg: True)
    monkeypatch.setattr(tr, "_get_session_conversation", lambda sid: "hist")
    monkeypatch.setattr(tr, "_get_current_window", lambda: ("Chrome", "页面"))
    monkeypatch.setattr(tr, "_build_prompt", lambda **kw: "PROMPT")
    import modules.thinking.frontend_channel as fc
    gnp = AsyncMock()
    monkeypatch.setattr(fc, "generate_and_push", gnp)
    import modules.database.proactive_repo as pr
    monkeypatch.setattr(pr, "save_proactive_log", MagicMock())
    return tr, gnp


def test_try_outreach_success(monkeypatch):
    tr, gnp = _try_outreach_setup(monkeypatch)
    gnp.return_value = "hello world"
    asyncio.run(tr._try_outreach("s1", "screen", change_ratio=0.2, changed_regions=[1]))
    assert tr._trigger_count == 1
    assert tr._session_last_trigger["s1"] > 0
    assert tr._screen_last_trigger["s1"] > 0


def test_try_outreach_success_schedule_reason(monkeypatch):
    tr, gnp = _try_outreach_setup(monkeypatch)
    gnp.return_value = "hello"
    asyncio.run(tr._try_outreach("s1", "schedule"))
    assert tr._trigger_count == 1
    assert "s1" not in tr._screen_last_trigger


def test_try_outreach_empty_response(monkeypatch):
    tr, gnp = _try_outreach_setup(monkeypatch)
    gnp.return_value = ""
    asyncio.run(tr._try_outreach("s1", "schedule"))
    assert tr._trigger_count == 0


def test_try_outreach_log_failure_ignored(monkeypatch):
    tr, gnp = _try_outreach_setup(monkeypatch)
    gnp.return_value = "hello"
    import modules.database.proactive_repo as pr
    monkeypatch.setattr(pr, "save_proactive_log", MagicMock(side_effect=RuntimeError()))
    asyncio.run(tr._try_outreach("s1", "schedule"))
    assert tr._trigger_count == 1


def test_try_outreach_error(monkeypatch):
    tr, gnp = _try_outreach_setup(monkeypatch)
    gnp.side_effect = RuntimeError("boom")
    push_err = MagicMock(side_effect=RuntimeError())
    monkeypatch.setattr(tr, "_push_error", push_err)
    with patch.object(mod.logger, "error") as err:
        asyncio.run(tr._try_outreach("s1", "schedule"))
    err.assert_called()
    push_err.assert_called_once()


# ── _push_error ──

def _push_error_cm(monkeypatch, send_result):
    import modules.thinking.api_stream as stream_mod
    cm = MagicMock()
    cm.active_connections = {"s1": object(), "s2": object()}
    cm.send_json_from_thread.return_value = send_result
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    monkeypatch.setattr(stream_mod, "_build_event", lambda **kw: {"event": kw.get("event")})
    return cm


def test_push_error_sent(monkeypatch):
    tr = ProactiveTrigger()
    _push_error_cm(monkeypatch, True)
    with patch.object(mod.logger, "warning") as w:
        tr._push_error("s1", "出错了")
    w.assert_not_called()


def test_push_error_no_connection(monkeypatch):
    tr = ProactiveTrigger()
    _push_error_cm(monkeypatch, False)
    with patch.object(mod.logger, "warning") as w:
        tr._push_error("s1", "出错了")
    w.assert_called_once()


def test_push_error_exception(monkeypatch):
    tr = ProactiveTrigger()
    import modules.thinking.api_stream as stream_mod
    monkeypatch.setattr(stream_mod, "connection_manager", MagicMock(active_connections={}))
    monkeypatch.setattr(stream_mod, "_build_event", lambda **kw: (_ for _ in ()).throw(RuntimeError()))
    with patch.object(mod.logger, "error") as err:
        tr._push_error("s1", "出错了")
    err.assert_called_once()


# ── _run_in_main_loop（方法版）─ ─

def _no_loop(monkeypatch):
    import modules.thinking.api_stream as stream_mod
    cm = MagicMock()
    cm._loop = None
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    monkeypatch.setattr(stream_mod, "_main_event_loop", None)
    return cm


def test_method_run_in_main_loop_fallback(monkeypatch):
    tr = ProactiveTrigger()
    _no_loop(monkeypatch)
    result = {}

    async def coro():
        result["ran"] = True
        return "ok"

    assert tr._run_in_main_loop(coro()) == "ok"
    assert result["ran"] is True


def test_method_run_in_main_loop_exception(monkeypatch):
    tr = ProactiveTrigger()
    loop = MagicMock()
    loop.is_closed.return_value = False
    cm = MagicMock()
    cm._loop = loop
    import modules.thinking.api_stream as stream_mod
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    monkeypatch.setattr(mod.asyncio, "run_coroutine_threadsafe", MagicMock(side_effect=RuntimeError()))
    result = {}

    async def coro():
        result["ran"] = True
        return "ok"

    assert tr._run_in_main_loop(coro()) == "ok"
    assert result["ran"] is True


def test_module_run_in_main_loop_exception(monkeypatch):
    loop = MagicMock()
    loop.is_closed.return_value = False
    cm = MagicMock()
    cm._loop = loop
    import modules.thinking.api_stream as stream_mod
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    monkeypatch.setattr(mod.asyncio, "run_coroutine_threadsafe", MagicMock(side_effect=RuntimeError()))
    result = {}

    async def coro():
        result["ran"] = True
        return "ok"

    assert mod.run_in_main_loop(coro()) == "ok"
    assert result["ran"] is True


# ── 各配置/记忆读取异常分支 ──

def test_get_session_outreach_config_exception(monkeypatch):
    import modules.database.session_repo as sr
    monkeypatch.setattr(sr, "get_session_repo", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert ProactiveTrigger()._get_session_outreach_config("s1") == {}


def test_qt_active_exception(monkeypatch):
    import modules.thinking.api_stream as stream_mod
    monkeypatch.setattr(stream_mod, "connection_manager", None)
    assert ProactiveTrigger()._qt_active() is False


def test_get_global_default_rules_bad_json(monkeypatch):
    import importlib
    cfg_mod = importlib.import_module("config.settings")
    old = cfg_mod.settings
    cfg_mod.settings = SimpleNamespace(PROACTIVE_OUTREACH_DEFAULT="{bad json")
    try:
        assert ProactiveTrigger._get_global_default_rules() == {}
    finally:
        cfg_mod.settings = old


def test_get_global_default_rules_non_dict(monkeypatch):
    import importlib
    cfg_mod = importlib.import_module("config.settings")
    old = cfg_mod.settings
    cfg_mod.settings = SimpleNamespace(PROACTIVE_OUTREACH_DEFAULT="[1, 2]")
    try:
        assert ProactiveTrigger._get_global_default_rules() == {}
    finally:
        cfg_mod.settings = old


def test_get_session_conversation_exception(monkeypatch):
    import modules.database.session_repo as sr
    monkeypatch.setattr(sr, "get_session_repo", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert ProactiveTrigger()._get_session_conversation("s1") == ""


def test_check_time_windows_disabled():
    tr = ProactiveTrigger()
    assert tr._check_time_windows({"time_windows_enabled": False}) is False


def test_check_time_windows_invalid_entries():
    tr = ProactiveTrigger()
    cfg = {"time_windows_enabled": True, "time_windows": [{"start": "bad", "end": "bad", "probability": 1.0}]}
    assert tr._check_time_windows(cfg) is False


# ── _call_llm ──

def test_call_llm(monkeypatch):
    monkeypatch.setattr(mod, "call_outreach_llm", AsyncMock(return_value="hi"))
    assert asyncio.run(ProactiveTrigger()._call_llm("prompt", "s1")) == "hi"


# ── _push 各分支 ──

def _push_setup(monkeypatch, system_sessions, cm):
    import modules.thinking.api_stream as stream_mod
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    monkeypatch.setattr(stream_mod, "_build_event", lambda **kw: {"event": kw.get("event")})
    system = MagicMock()
    system.sessions = system_sessions
    monkeypatch.setattr(stream_mod, "get_thinking_system", lambda: system)
    import modules.database.session_repo as sr
    repo = MagicMock()
    repo.save_message.return_value = "mid"
    monkeypatch.setattr(sr, "get_session_repo", lambda: repo)
    return system, repo


def test_push_agent_session_main_loop(monkeypatch):
    """agent 会话 + 主 loop → run_coroutine_threadsafe 提交 _append_message"""
    tr = ProactiveTrigger()
    cm = MagicMock()
    cm.active_connections = {}
    cm.send_json_from_thread.return_value = False
    loop = MagicMock()
    loop.is_closed.return_value = False
    cm._loop = loop
    system, repo = _push_setup(monkeypatch, {"s1": object()}, cm)
    system._append_message = AsyncMock(return_value="mid")
    fut = MagicMock()
    fut.result.return_value = "mid"
    monkeypatch.setattr(mod.asyncio, "run_coroutine_threadsafe", MagicMock(return_value=fut))
    tr._push("s1", "内容")
    system._append_message.assert_called_once_with("s1", "assistant", "内容")
    fut.result.assert_called_once_with(timeout=10)


def test_push_agent_session_no_loop(monkeypatch):
    """agent 会话但无可用 loop → 回退 _run_async"""
    tr = ProactiveTrigger()
    cm = _no_loop(monkeypatch)
    cm.active_connections = {}
    cm.send_json_from_thread.return_value = False
    system, repo = _push_setup(monkeypatch, {"s1": object()}, cm)
    system._append_message = AsyncMock(return_value="mid")
    tr._push("s1", "内容")
    system._append_message.assert_called_once_with("s1", "assistant", "内容")


def test_push_chatonly_save_fails(monkeypatch):
    """chatonly 会话落 DB 失败 → 静默忽略，不中断推送"""
    tr = ProactiveTrigger()
    cm = _no_loop(monkeypatch)
    cm.active_connections = {}
    cm.send_json_from_thread.return_value = False
    system, repo = _push_setup(monkeypatch, {}, cm)
    repo.save_message.side_effect = RuntimeError()
    tr._push("s1", "内容")


def test_push_broadcast_mixed(monkeypatch):
    """多连接部分失败：至少一个成功则不告警"""
    tr = ProactiveTrigger()
    cm = _no_loop(monkeypatch)
    cm.active_connections = {"s1": object(), "s2": object()}
    cm.send_json_from_thread.side_effect = [False, True]
    system, repo = _push_setup(monkeypatch, {}, cm)
    with patch.object(mod.logger, "warning") as w:
        tr._push("s1", "内容")
    w.assert_not_called()


def test_push_outer_exception(monkeypatch):
    """事件构造失败 → 外层 except 记录错误"""
    tr = ProactiveTrigger()
    import modules.thinking.api_stream as stream_mod
    cm = MagicMock()
    cm.active_connections = {}
    cm._loop = None
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    monkeypatch.setattr(stream_mod, "_build_event", lambda **kw: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(stream_mod, "get_thinking_system", lambda: MagicMock(sessions={}))
    with patch.object(mod.logger, "error") as err:
        tr._push("s1", "内容")
    err.assert_called_once()


def test_push_persist_failure(monkeypatch):
    """持久化阶段（get_thinking_system 异常）→ 外层 except 记录错误，推送仍继续"""
    tr = ProactiveTrigger()
    import modules.thinking.api_stream as stream_mod
    cm = MagicMock()
    cm.active_connections = {"s1": object()}
    cm.send_json_from_thread.return_value = True
    cm._loop = None
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    monkeypatch.setattr(stream_mod, "_build_event", lambda **kw: {"event": kw.get("event")})
    monkeypatch.setattr(stream_mod, "get_thinking_system", lambda: (_ for _ in ()).throw(RuntimeError()))
    with patch.object(mod.logger, "error") as err:
        tr._push("s1", "内容")
    err.assert_called_once()
    cm.send_json_from_thread.assert_called_once()


# ── get_stats ──

def test_get_stats(monkeypatch):
    tr = ProactiveTrigger()
    tr._trigger_count = 3
    tr._idle_timer._last_activity = time.time() - 42
    monkeypatch.setattr(tr, "_get_enabled_outreach_sessions", lambda: {"s1": {}})
    stats = tr.get_stats()
    assert stats["trigger_count"] == 3
    assert stats["session_last_trigger_count"] == 0
    assert stats["active_sessions"] == 1
    assert 41 <= stats["idle_seconds"] <= 43


# ── call_outreach_llm ──

def _llm_factory(monkeypatch, client=None):
    import modules.thinking.model_factory as mf
    import modules.thinking.probes.probe_tools as pt
    import modules.thinking.context.sources.perception_source as ps
    import modules.memory.event_retrieval as er
    if client is None:
        client = MagicMock()
        client.chat = AsyncMock(return_value=None)
    factory = MagicMock()
    factory.get_client.return_value = client
    monkeypatch.setattr(mf, "get_model_factory", lambda: factory)
    monkeypatch.setattr(mod, "_build_outreach_system_prompt", lambda role=None, tier="large": "SYSTEM")
    monkeypatch.setattr(pt, "_session_guidance", {})
    monkeypatch.setattr(ps, "PerceptionSource", lambda: MagicMock(collect=AsyncMock(return_value=None)))
    monkeypatch.setattr(er, "get_event_retrieval", lambda: MagicMock(retrieve=AsyncMock(return_value=[])))
    return client


def test_call_outreach_llm_full_extras(monkeypatch):
    client = MagicMock()
    resp = MagicMock()
    resp.message.content = "  result  "
    client.chat = AsyncMock(return_value=resp)
    _llm_factory(monkeypatch, client)
    import modules.thinking.probes.probe_tools as pt
    import modules.thinking.context.sources.perception_source as ps
    import modules.memory.event_retrieval as er
    monkeypatch.setattr(pt, "_session_guidance", {("large_primary", "s1"): {"inner_thoughts": "过往经验"}})
    frag = MagicMock()
    frag.content = "感知信息"
    monkeypatch.setattr(ps, "PerceptionSource", lambda: MagicMock(collect=AsyncMock(return_value=frag)))
    ev1 = SimpleNamespace(time="2024-01-02 03:04:05", fact="发生过的事")
    ev2 = SimpleNamespace(time=None, fact="无日期事件")
    monkeypatch.setattr(er, "get_event_retrieval", lambda: MagicMock(retrieve=AsyncMock(return_value=[ev1, ev2])))

    import importlib
    cfg_mod = importlib.import_module("config.settings")
    old = cfg_mod.settings
    cfg_mod.settings = SimpleNamespace(USER_NAME="测试用户")
    try:
        out = asyncio.run(mod.call_outreach_llm("你好", "s1"))
    finally:
        cfg_mod.settings = old
    assert out == "result"
    msgs = client.chat.call_args.kwargs["messages"]
    assert len(msgs) == 2
    assert "过往经验" in msgs[0].content
    assert "无日期事件" in msgs[0].content


def test_call_outreach_llm_no_extras(monkeypatch):
    client = _llm_factory(monkeypatch)
    resp = MagicMock()
    resp.message.content = "  plain  "
    client.chat = AsyncMock(return_value=resp)
    assert asyncio.run(mod.call_outreach_llm("你好", "s1")) == "plain"
    msgs = client.chat.call_args.kwargs["messages"]
    content = msgs[0].content
    assert content.startswith("SYSTEM")
    assert "过往经验" not in content
    assert "感知信息" not in content
    assert "曾经发生的事" not in content


def test_call_outreach_llm_extras_exceptions(monkeypatch):
    """三个 extras 采集器异常时静默跳过"""
    client = _llm_factory(monkeypatch)
    resp = MagicMock()
    resp.message.content = "ok"
    client.chat = AsyncMock(return_value=resp)
    import modules.thinking.probes.probe_tools as pt
    import modules.thinking.context.sources.perception_source as ps
    import modules.memory.event_retrieval as er
    guidance = MagicMock()
    guidance.get.side_effect = RuntimeError()
    monkeypatch.setattr(pt, "_session_guidance", guidance)
    monkeypatch.setattr(ps, "PerceptionSource", lambda: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(er, "get_event_retrieval", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert asyncio.run(mod.call_outreach_llm("你好", "s1")) == "ok"


def test_call_outreach_llm_chat_fails(monkeypatch):
    client = MagicMock()
    client.chat = AsyncMock(side_effect=RuntimeError("llm boom"))
    _llm_factory(monkeypatch, client)
    with patch.object(mod.logger, "error"):
        assert asyncio.run(mod.call_outreach_llm("你好", "s1")) == ""


def test_call_outreach_llm_empty_response(monkeypatch):
    client = _llm_factory(monkeypatch)
    assert asyncio.run(mod.call_outreach_llm("你好", "s1")) == ""
