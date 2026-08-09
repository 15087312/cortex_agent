"""perception/trigger 测试（此前 31% 覆盖）：空闲计时器与主动触发"""
import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


@pytest.fixture
def session_repo(tmp_path, monkeypatch):
    """真实临时 SQLite + 真实 SessionRepository"""
    import modules.database.connection as conn
    from modules.database.session_repo import SessionRepository
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "test_trg.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", __import__("threading").RLock())
    conn.get_db_manager().initialize()
    return SessionRepository()


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)

    async def accept(self):
        pass


class _LoopServer:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=3)
        self.loop.close()


@pytest.fixture
def server():
    sv = _LoopServer()
    yield sv
    sv.stop()


@pytest.fixture
def cm(monkeypatch, server):
    import modules.thinking.api_stream as stream_mod
    mgr = stream_mod.ConnectionManager()
    mgr._loop = server.loop
    monkeypatch.setattr(stream_mod, "connection_manager", mgr)
    return mgr


def _connect(cm, sid, server):
    asyncio.run_coroutine_threadsafe(cm.connect(sid, _FakeWS()), server.loop).result(timeout=5)


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


def test_qt_active_real(cm, server):
    tr = ProactiveTrigger()
    assert tr._qt_active() is False  # 无连接
    _connect(cm, "s1", server)
    assert tr._qt_active() is True  # 有连接


def test_get_session_outreach_config_real_db(session_repo):
    """真实 DB：会话 outreach 配置读写一致"""
    session_repo.create_session("s1")
    cfg = {"enabled": True, "cooldown_minutes": 5}
    session_repo.set_outreach_config("s1", cfg)
    tr = ProactiveTrigger()
    import modules.database.session_repo as sr
    with patch.object(sr, "get_session_repo", lambda: session_repo):
        assert tr._get_session_outreach_config("s1") == cfg


def test_get_session_conversation_real_db(session_repo):
    """真实 DB：会话历史作为搭话上下文"""
    session_repo.create_session("s1")
    session_repo.save_message("s1", "user", "你好")
    session_repo.save_message("s1", "assistant", "在的")
    tr = ProactiveTrigger()
    import modules.database.session_repo as sr
    with patch.object(sr, "get_session_repo", lambda: session_repo):
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


def test_try_outreach_cooldown_blocked(session_repo):
    """真实 DB：冷却内不触发（不调 LLM）"""
    session_repo.create_session("s1")
    session_repo.set_outreach_config("s1", {"enabled": True, "cooldown_minutes": 15})
    tr = ProactiveTrigger()
    tr._session_last_trigger["s1"] = time.time()  # 刚触发过
    import modules.database.session_repo as sr
    with patch.object(sr, "get_session_repo", lambda: session_repo):
        asyncio.run(tr._try_outreach("s1", "schedule"))
    assert tr._trigger_count == 0


def test_push_error_real(cm):
    """真实 CM 无连接：_push_error 不崩、不发"""
    tr = ProactiveTrigger()
    tr._push_error("s1", "出错了")  # 无活跃连接


def test_confirm_frontend_connection_success(cm, server):
    """真实 CM + 后台 loop：握手确认成功"""
    _connect(cm, "s1", server)
    assert mod_confirm() is True


def test_confirm_frontend_connection_no_connections(cm):
    assert mod_confirm() is False


def test_confirm_frontend_connection_send_fail(monkeypatch):
    import modules.thinking.api_stream as stream_mod
    cm = MagicMock()
    cm.active_connections = {"s1": object(), "s2": object()}
    cm.send_json_from_thread.return_value = False
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    assert mod_confirm() is False


def test_try_outreach_skips_when_frontend_down(session_repo, cm):
    """真实无连接：握手失败跳过 LLM（真实 confirm 无连接返回 False）"""
    session_repo.create_session("s1")
    session_repo.set_outreach_config("s1", {"enabled": True, "cooldown_minutes": 1})
    tr = ProactiveTrigger()
    import modules.database.session_repo as sr
    with patch.object(sr, "get_session_repo", lambda: session_repo):
        asyncio.run(tr._try_outreach("s1", "schedule"))
    assert tr._trigger_count == 0  # 无前端连接 → 未调用 LLM


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


def _outreach_sessions(monkeypatch, sessions, default=None):
    """构造触发会话集合（mock get_all_sessions + 全局默认）"""
    tr = ProactiveTrigger()
    import modules.perception.trigger as mod
    import modules.database.session_repo as sr
    import importlib
    cfg_mod = importlib.import_module("config.settings")
    from types import SimpleNamespace
    monkeypatch.setattr(cfg_mod, "settings", SimpleNamespace(PROACTIVE_OUTREACH_ENABLED=True))
    monkeypatch.setattr(sr, "get_session_repo", lambda: MagicMock(get_all_sessions=lambda limit=100: sessions))
    if default is not None:
        monkeypatch.setattr(mod.ProactiveTrigger, "_get_global_default_rules", staticmethod(lambda: default))
    return tr


def test_outreach_only_enabled_sessions(monkeypatch):
    """未在设置里单独开启的会话不触发（即使全局默认开启）"""
    sessions = [
        {"session_id": "s1", "metadata": {"outreach": {"enabled": True, "screen": {"enabled": True}}}},
        {"session_id": "s2", "metadata": {"outreach": {}}},  # 未单独开启
    ]
    tr = _outreach_sessions(monkeypatch, sessions, default={"enabled": True, "idle": {"enabled": True}})
    result = tr._get_enabled_outreach_sessions()
    assert "s1" in result  # 单独开启 + 有规则
    assert "s2" not in result  # 未单独开启 → 不触发


def test_outreach_enabled_no_rules_uses_default(monkeypatch):
    """会话单独开启但未配具体规则 → 用全局默认规则作模板"""
    sessions = [
        {"session_id": "s1", "metadata": {"outreach": {"enabled": True}}},  # 开启但无规则
    ]
    default = {"enabled": True, "idle": {"enabled": True, "idle_minutes": 15}}
    tr = _outreach_sessions(monkeypatch, sessions, default=default)
    result = tr._get_enabled_outreach_sessions()
    assert result["s1"] == default


def test_outreach_enabled_no_rules_no_default(monkeypatch):
    """会话单独开启但无规则、全局默认未开启 → 用会话自身配置（无规则则不触发）"""
    sessions = [
        {"session_id": "s1", "metadata": {"outreach": {"enabled": True}}},
    ]
    tr = _outreach_sessions(monkeypatch, sessions, default={"enabled": False})
    result = tr._get_enabled_outreach_sessions()
    assert result["s1"] == {"enabled": True}


def test_run_in_main_loop_uses_main_loop(monkeypatch):
    """run_in_main_loop：有主 loop 时提交到主 loop（不新建，避免 Event loop is closed）"""
    import asyncio
    import modules.perception.trigger as mod
    import modules.thinking.api_stream as stream_mod

    loop = asyncio.new_event_loop()
    cm = MagicMock()
    cm._loop = loop
    monkeypatch.setattr(stream_mod, "connection_manager", cm)

    result = {}
    async def coro():
        result["ran"] = True
        return "ok"
    # 提交到主 loop 执行
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(1) as ex:
        fut = ex.submit(lambda: mod.run_in_main_loop(coro()))
        # 主 loop 需驱动 coroutine_threadsafe
        import threading, time
        threading.Timer(0.2, lambda: loop.call_soon_threadsafe(loop.stop)).start()
        loop.run_forever()
        assert fut.result(timeout=5) == "ok"
    assert result["ran"] is True
    loop.close()


def test_run_in_main_loop_fallback(monkeypatch):
    """无主 loop 时回退独立线程执行（不抛错）"""
    import modules.perception.trigger as mod
    import modules.thinking.api_stream as stream_mod
    cm = MagicMock()
    cm._loop = None
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    monkeypatch.setattr(stream_mod, "_main_event_loop", None)
    result = {}
    async def coro():
        result["ran"] = True
        return "ok"
    assert mod.run_in_main_loop(coro()) == "ok"
    assert result["ran"] is True
