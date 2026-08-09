"""perception/trigger 测试（此前 31% 覆盖）：空闲计时器与主动触发"""
import time
from unittest.mock import MagicMock, patch

from modules.perception.trigger import confirm_frontend_connection as mod_confirm

from modules.perception.trigger import IdleTimer, ProactiveTrigger, _build_outreach_system_prompt


def test_build_outreach_system_prompt():
    p = _build_outreach_system_prompt(role="orchestrator", tier="large")
    assert "主模型" in p
    assert isinstance(p, str)


def test_idle_timer():
    t = IdleTimer()
    t.notify_activity()
    assert t.idle_minutes >= 0
    assert t.idle_seconds >= 0


def test_proactive_trigger_init_and_notify():
    tr = ProactiveTrigger()
    tr.notify_activity()
    assert tr._trigger_count == 0


def test_proactive_trigger_start_subscribes():
    tr = ProactiveTrigger()
    bus = MagicMock()
    bus.subscribe.return_value = "sub-1"
    tr.start(bus)
    assert tr._sub_id == "sub-1"
    assert bus.subscribe.called


def test_proactive_trigger_stop():
    tr = ProactiveTrigger()
    bus = MagicMock()
    bus.subscribe.return_value = "sub-1"
    tr.start(bus)
    tr._event_bus = bus
    tr.stop()
    assert tr._sub_id == ""


def test_cooldown_ok():
    tr = ProactiveTrigger()
    assert tr._cooldown_ok("s1", {}) is True  # 无历史记录
    tr._session_last_trigger["s1"] = time.time()
    assert tr._cooldown_ok("s1", {"cooldown_minutes": 15}) is False


def test_cooldown_ok_with_config():
    tr = ProactiveTrigger()
    tr._session_last_trigger["s1"] = time.time() - 3600
    assert tr._cooldown_ok("s1", {"cooldown_minutes": 15}) is True


def test_screen_cooldown_ok():
    tr = ProactiveTrigger()
    assert tr._screen_cooldown_ok("s1", {}) is True
    tr._screen_last_trigger["s1"] = time.time()
    assert tr._screen_cooldown_ok("s1", {}) is False


def test_rule_ready():
    tr = ProactiveTrigger()
    assert tr._rule_ready("s1", "screen", 30) is True
    assert tr._rule_ready("s1", "screen", 30) is False  # 间隔内


def test_check_schedule():
    tr = ProactiveTrigger()
    assert tr._check_schedule({}) is False
    from datetime import datetime, timedelta
    now = datetime.now()
    t = (now + timedelta(minutes=1)).strftime("%H:%M")
    cfg = {"schedule": {"enabled": True, "time": t, "jitter_minutes": 5}}
    assert tr._check_schedule(cfg) is True
    assert tr._check_schedule({"schedule": {"time": "not-a-time"}}) is False


def test_check_idle_rule():
    tr = ProactiveTrigger()
    tr._idle_timer._last_activity = time.time() - 7200  # 空闲 2 小时
    assert tr._check_idle_rule({}) is False  # 无配置
    cfg = {"enabled": True, "idle_minutes": 30, "probability": 1.0}
    assert tr._check_idle_rule(cfg) is True
    tr._idle_timer._last_activity = time.time()
    assert tr._check_idle_rule(cfg) is False  # 空闲不足


def test_check_time_windows():
    tr = ProactiveTrigger()
    from datetime import datetime
    cur_h, cur_m = datetime.now().hour, datetime.now().minute
    # 用当前分钟附近构建窗口
    start = f"{cur_h:02d}:00"
    end = f"{cur_h:02d}:59"
    cfg = {"time_windows_enabled": True, "time_windows": [{"start": start, "end": end, "probability": 1.0}]}
    assert tr._check_time_windows(cfg) is True
    # 跨午夜窗口
    cfg2 = {"time_windows_enabled": True, "time_windows": [{"start": "23:00", "end": "01:00", "probability": 1.0}]}
    assert isinstance(tr._check_time_windows(cfg2), bool)
    assert tr._check_time_windows({}) is False


def test_get_global_default_rules(monkeypatch):
    import modules.perception.trigger as mod
    import importlib
    cfg_mod = importlib.import_module("config.settings")
    from types import SimpleNamespace
    monkeypatch.setattr(cfg_mod, "settings", SimpleNamespace(PROACTIVE_OUTREACH_DEFAULT='{"enabled": true}'))
    rules = mod.ProactiveTrigger._get_global_default_rules()
    assert rules == {"enabled": True}


def test_get_enabled_outreach_sessions(monkeypatch):
    tr = ProactiveTrigger()
    import modules.perception.trigger as mod
    import modules.database.session_repo as sr
    import importlib
    cfg_mod = importlib.import_module("config.settings")
    from types import SimpleNamespace
    monkeypatch.setattr(cfg_mod, "settings", SimpleNamespace(PROACTIVE_OUTREACH_ENABLED=True))
    import modules.database.session_repo as sr
    monkeypatch.setattr(sr, "get_session_repo", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert tr._get_enabled_outreach_sessions() == {}


def test_qt_active(monkeypatch):
    tr = ProactiveTrigger()
    import modules.thinking.api_stream as stream_mod
    cm = MagicMock()
    cm.active_connections = {}
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    assert tr._qt_active() is False
    cm.active_connections = {"s1": object()}
    assert tr._qt_active() is True


def test_get_session_outreach_config(monkeypatch):
    tr = ProactiveTrigger()
    import modules.database.session_repo as sr
    repo = MagicMock()
    repo.get_outreach_config.return_value = {"enabled": True}
    monkeypatch.setattr(sr, "get_session_repo", lambda: repo)
    assert tr._get_session_outreach_config("s1") == {"enabled": True}


def test_get_session_conversation(monkeypatch):
    tr = ProactiveTrigger()
    import modules.database.session_repo as sr
    repo = MagicMock()
    repo.get_recent_messages.return_value = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "在的"},
    ]
    monkeypatch.setattr(sr, "get_session_repo", lambda: repo)
    out = tr._get_session_conversation("s1")
    assert "user" in out and "你好" in out


def test_build_prompt_default():
    tr = ProactiveTrigger()
    import modules.perception.trigger as mod
    import importlib
    cfg_mod = importlib.import_module("config.settings")
    from types import SimpleNamespace
    old = cfg_mod.settings
    cfg_mod.settings = SimpleNamespace(PROACTIVE_OUTREACH_WORK_PROMPT="", USER_NAME="用户")
    try:
        p = tr._build_prompt(idle_minutes=5, change_ratio=0.2, changed_regions=[1, 2], current_app="Chrome", current_window="页面", conversation="最近对话")
        assert "20%" in p
        assert "Chrome" in p
        assert "最近对话" in p
    finally:
        cfg_mod.settings = old


def test_build_prompt_custom(monkeypatch):
    tr = ProactiveTrigger()
    import modules.perception.trigger as mod
    import importlib
    cfg_mod = importlib.import_module("config.settings")
    from types import SimpleNamespace
    old = cfg_mod.settings
    cfg_mod.settings = SimpleNamespace(PROACTIVE_OUTREACH_WORK_PROMPT="用户空闲{idle_minutes}分钟，应用{current_app}")
    try:
        p = tr._build_prompt(idle_minutes=5, change_ratio=0, changed_regions=[], current_app="Chrome", current_window="", conversation="")
        assert "用户空闲5分钟" in p
    finally:
        cfg_mod.settings = old


def test_build_time_text(monkeypatch):
    tr = ProactiveTrigger()
    import modules.perception.trigger as mod
    import importlib
    cfg_mod = importlib.import_module("config.settings")
    from types import SimpleNamespace
    old = cfg_mod.settings
    cfg_mod.settings = SimpleNamespace(USER_NAME="用户")
    try:
        t = tr._build_time_text()
        assert "当前时间" in t
        assert "用户" in t
    finally:
        cfg_mod.settings = old


def test_try_outreach_success(monkeypatch):
    tr = ProactiveTrigger()
    import modules.perception.trigger as mod
    import modules.database.proactive_repo as pr
    import modules.thinking.frontend_channel as fc
    async def fake_llm(prompt, session_id):
        return "需要帮忙吗"
    monkeypatch.setattr(mod, "call_outreach_llm", fake_llm)
    pushed = []
    async def fake_push(sid, *, msg_type, event, content, role="assistant", data=None, persist=True):
        pushed.append(content)
        return True
    monkeypatch.setattr(fc, "confirm_frontend_connection", lambda session_id=None: True)
    monkeypatch.setattr(fc, "push_content", fake_push)
    tr._get_session_outreach_config = lambda sid: {"cooldown_minutes": 1}
    tr._get_session_conversation = lambda sid: ""
    tr._get_current_window = lambda: ("", "")
    tr._build_prompt = lambda **kw: "prompt"
    monkeypatch.setattr(pr, "save_proactive_log", lambda *a: None)
    import asyncio
    asyncio.run(tr._try_outreach("s1", "schedule"))
    assert pushed == ["需要帮忙吗"]
    assert tr._trigger_count == 1


def test_try_outreach_cooldown_blocked():
    tr = ProactiveTrigger()
    tr._get_session_outreach_config = lambda sid: {"cooldown_minutes": 15}
    tr._session_last_trigger["s1"] = __import__("time").time()
    import asyncio
    asyncio.run(tr._try_outreach("s1", "schedule"))
    assert tr._trigger_count == 0


def test_try_outreach_empty_response(monkeypatch):
    tr = ProactiveTrigger()
    import modules.perception.trigger as mod
    import modules.thinking.frontend_channel as fc
    async def fake_llm(prompt, session_id):
        return ""
    monkeypatch.setattr(mod, "call_outreach_llm", fake_llm)
    pushed = []
    async def fake_push(sid, *, msg_type, event, content, role="assistant", data=None, persist=True):
        pushed.append(content)
        return True
    monkeypatch.setattr(fc, "confirm_frontend_connection", lambda session_id=None: True)
    monkeypatch.setattr(fc, "push_content", fake_push)
    tr._get_session_outreach_config = lambda sid: {"cooldown_minutes": 1}
    tr._get_session_conversation = lambda sid: ""
    tr._get_current_window = lambda: ("", "")
    tr._build_prompt = lambda **kw: "prompt"
    import asyncio
    asyncio.run(tr._try_outreach("s1", "schedule"))
    assert pushed == []


def test_push_error(monkeypatch):
    tr = ProactiveTrigger()
    import modules.thinking.api_stream as stream_mod
    cm = MagicMock()
    cm.active_connections = {}
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    monkeypatch.setattr(stream_mod, "_build_event", lambda **kw: {"event": "proactive_error"})
    tr._push_error("s1", "出错了")
    cm.send_json_from_thread.assert_not_called()  # 无活跃连接


def test_confirm_frontend_connection_success(monkeypatch):
    import modules.thinking.api_stream as stream_mod
    cm = MagicMock()
    cm.active_connections = {"s1": object()}
    cm.send_json_from_thread.return_value = True
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    monkeypatch.setattr(stream_mod, "_build_event", lambda **kw: {"event": kw.get("event")})
    assert mod_confirm() is True
    cm.send_json_from_thread.assert_called_once()


def test_confirm_frontend_connection_no_connections(monkeypatch):
    import modules.thinking.api_stream as stream_mod
    cm = MagicMock()
    cm.active_connections = {}
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    assert mod_confirm() is False
    cm.send_json_from_thread.assert_not_called()


def test_confirm_frontend_connection_send_fail(monkeypatch):
    import modules.thinking.api_stream as stream_mod
    cm = MagicMock()
    cm.active_connections = {"s1": object(), "s2": object()}
    cm.send_json_from_thread.return_value = False
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    assert mod_confirm() is False


def test_try_outreach_skips_when_frontend_down(monkeypatch):
    tr = ProactiveTrigger()
    import modules.perception.trigger as mod
    async def fake_llm(prompt, session_id):
        raise AssertionError("前端不可达时不应调用 LLM")
    monkeypatch.setattr(mod, "call_outreach_llm", fake_llm)
    monkeypatch.setattr(mod, "confirm_frontend_connection", lambda: False)
    tr._get_session_outreach_config = lambda sid: {"cooldown_minutes": 1}
    import asyncio
    asyncio.run(tr._try_outreach("s1", "schedule"))
    assert tr._trigger_count == 0  # 未调用 LLM，未计数


def test_push_real_impl_no_connections(monkeypatch):
    """_push 真实实现：无活跃连接时消息只落历史，不推 WS（并告警）"""
    tr = ProactiveTrigger()
    import modules.thinking.api_stream as stream_mod
    cm = MagicMock()
    cm.active_connections = {}
    cm.send_json_from_thread.return_value = False
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    monkeypatch.setattr(stream_mod, "_build_event", lambda **kw: {"event": kw.get("event")})
    import modules.database.session_repo as sr
    repo = MagicMock()
    repo.save_message.return_value = "mid"
    monkeypatch.setattr(sr, "get_session_repo", lambda: repo)
    import modules.thinking.api_stream as stream_mod2
    system = MagicMock()
    system.sessions = {}  # chatonly 分支 → 直接落 DB
    monkeypatch.setattr(stream_mod2, "get_thinking_system", lambda: system)
    import modules.perception.trigger as mod
    with patch.object(mod.logger, "warning") as warn:
        tr._push("s1", "内容")
        warn.assert_called_once()
        assert "无活跃 WebSocket" in warn.call_args[0][0]
    assert cm.send_json_from_thread.call_count == 0
    repo.save_message.assert_called_once_with("s1", "assistant", "内容")


def test_push_real_impl_broadcast(monkeypatch):
    """_push 真实实现：有连接时广播成功"""
    tr = ProactiveTrigger()
    import modules.thinking.api_stream as stream_mod
    cm = MagicMock()
    cm.active_connections = {"s1": object(), "s2": object()}
    cm.send_json_from_thread.return_value = True
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    monkeypatch.setattr(stream_mod, "_build_event", lambda **kw: {"event": kw.get("event")})
    import modules.database.session_repo as sr
    repo = MagicMock()
    repo.save_message.return_value = "mid"
    monkeypatch.setattr(sr, "get_session_repo", lambda: repo)
    import modules.thinking.api_stream as stream_mod2
    system = MagicMock()
    system.sessions = {}
    monkeypatch.setattr(stream_mod2, "get_thinking_system", lambda: system)
    tr._push("s1", "内容")
    assert cm.send_json_from_thread.call_count == 2


def test_get_current_window_real_impl(monkeypatch):
    """_get_current_window 真实实现：读 world_state 的 active_app/active_window"""
    tr = ProactiveTrigger()
    import modules.perception.state.world_state as ws_mod
    state = MagicMock()
    state.active_app = "Chrome"
    state.active_window = "页面标题"
    monkeypatch.setattr(ws_mod, "get_world_state", lambda: state)
    app, win = tr._get_current_window()
    assert app == "Chrome"
    assert win == "页面标题"


def test_get_current_window_real_impl_exception(monkeypatch):
    """world_state 异常时回退空"""
    tr = ProactiveTrigger()
    import modules.perception.state.world_state as ws_mod
    monkeypatch.setattr(ws_mod, "get_world_state", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert tr._get_current_window() == ("", "")
