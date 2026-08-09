"""perception/trigger 测试（此前 31% 覆盖）：空闲计时器与主动触发"""
import time
from unittest.mock import MagicMock

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
