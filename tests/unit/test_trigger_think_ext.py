"""perception/trigger_think 补充测试：连接握手、描述拼接、后台执行"""
import asyncio
import time

import modules.perception.trigger_think as tt


class _SyncThread:
    def __init__(self, target=None, args=(), kwargs=None, **kw):
        self._t = target
        self._a = args
        self._k = kwargs or {}

    def start(self):
        self._t(*self._a, **self._k)

    daemon = False


def _reset():
    with tt._lock:
        tt._state["last"] = 0.0


def _allow_outreach(monkeypatch):
    monkeypatch.setattr(
        "modules.perception.trigger.outreach_trigger_allowed",
        lambda: True,
    )


def test_has_active_connections_true(monkeypatch):
    monkeypatch.setattr(
        "modules.perception.trigger.confirm_frontend_connection", lambda: True
    )
    assert tt._has_active_connections() is True


def test_has_active_connections_exception(monkeypatch):
    def _boom():
        raise RuntimeError("frontend down")

    monkeypatch.setattr(
        "modules.perception.trigger.confirm_frontend_connection", _boom
    )
    assert tt._has_active_connections() is False


def test_trigger_change_already_in_category(monkeypatch):
    """change_type 已是 category 子串 → 不重复追加"""
    _reset()
    _allow_outreach(monkeypatch)
    fired = []

    monkeypatch.setattr(tt, "_run", fired.append)
    monkeypatch.setattr(tt, "_has_active_connections", lambda: True)
    monkeypatch.setattr(tt.threading, "Thread", _SyncThread)

    class D:
        source_type = "screen"
        category = "screen_changed"
        intensity = 90
        payload = {"target": "主窗口", "change_type": "screen_changed"}

    tt._trigger([D()])
    assert fired == ["screen_changed:主窗口"]


def test_trigger_change_appended(monkeypatch):
    """change_type 不在 category 中 → 追加进描述"""
    _reset()
    _allow_outreach(monkeypatch)
    fired = []

    monkeypatch.setattr(tt, "_run", fired.append)
    monkeypatch.setattr(tt, "_has_active_connections", lambda: True)
    monkeypatch.setattr(tt.threading, "Thread", _SyncThread)

    class D:
        source_type = "screen"
        category = "file_modified"
        intensity = 90
        payload = {"target": "笔记.md", "change_type": "content_changed"}

    tt._trigger([D()])
    assert fired == ["file_modified:笔记.md:content_changed"]


def test_run_success(monkeypatch):
    seen = []

    async def fake_think(desc):
        seen.append(desc)

    monkeypatch.setattr(tt, "_think", fake_think)
    monkeypatch.setattr(
        "modules.perception.trigger.run_in_main_loop", lambda coro: asyncio.run(coro)
    )
    tt._run("desc")
    assert seen == ["desc"]


def test_run_exception_swallowed(monkeypatch):
    def _raise(coro):
        raise RuntimeError("event loop closed")

    monkeypatch.setattr(tt, "_think", lambda desc: "not-a-coroutine")
    monkeypatch.setattr("modules.perception.trigger.run_in_main_loop", _raise)
    tt._run("x")  # 异常被吞掉，不向上抛


def test_trigger_settings_zero_cooldown(monkeypatch):
    """冷却/强度配置为 0 时回退默认值"""
    _reset()
    _allow_outreach(monkeypatch)
    fired = []

    monkeypatch.setattr(tt, "_run", fired.append)
    monkeypatch.setattr(tt, "_has_active_connections", lambda: True)
    monkeypatch.setattr(tt.threading, "Thread", _SyncThread)

    import importlib
    settings_mod = importlib.import_module("config.settings")
    monkeypatch.setattr(
        settings_mod, "settings",
        type("Settings", (), {"PERCEPTION_TRIGGER_COOLDOWN": 0,
                               "PERCEPTION_TRIGGER_MIN_INTENSITY": 0})(),
    )

    class D:
        source_type = "screen"
        category = "screen_changed"
        intensity = 99
        payload = {}

    tt._trigger([D()])
    assert fired == ["screen_changed"]


class _Diff:
    def __init__(self, intensity=80):
        self.intensity = intensity
        self.source_type = "screen"
        self.category = "screen_changed"
        self.payload = {"target": "主窗口", "change_type": "changed"}


def test_register_uses_detector(monkeypatch):
    class FakeDetector:
        def __init__(self):
            self.cbs = []

        def on_high_intensity(self, cb):
            self.cbs.append(cb)

    fake = FakeDetector()
    monkeypatch.setattr("modules.perception.difference.get_detector", lambda: fake)
    tt.register()
    assert len(fake.cbs) == 1


def test_trigger_no_active_connections(monkeypatch):
    _reset()
    _allow_outreach(monkeypatch)
    fired = []
    monkeypatch.setattr(tt, "_run", fired.append)
    monkeypatch.setattr(tt, "_has_active_connections", lambda: False)
    tt._trigger([_Diff(99)])
    assert fired == []


def test_trigger_outreach_disallowed(monkeypatch):
    _reset()
    fired = []
    monkeypatch.setattr(tt, "_run", fired.append)
    monkeypatch.setattr(tt, "_has_active_connections", lambda: True)
    monkeypatch.setattr(
        "modules.perception.trigger.outreach_trigger_allowed", lambda: False
    )
    tt._trigger([_Diff(99)])
    assert fired == []


def test_trigger_cooldown_active(monkeypatch):
    _reset()
    _allow_outreach(monkeypatch)
    fired = []
    monkeypatch.setattr(tt, "_run", fired.append)
    monkeypatch.setattr(tt, "_has_active_connections", lambda: True)
    monkeypatch.setattr(tt.threading, "Thread", _SyncThread)
    tt._trigger([_Diff(80)])
    tt._trigger([_Diff(90)])  # 冷却内 → 第二次不触发
    assert len(fired) == 1


def test_trigger_no_strong_diff(monkeypatch):
    _reset()
    _allow_outreach(monkeypatch)
    fired = []
    monkeypatch.setattr(tt, "_run", fired.append)
    monkeypatch.setattr(tt, "_has_active_connections", lambda: True)
    monkeypatch.setattr(tt.threading, "Thread", _SyncThread)
    tt._trigger([_Diff(10)])  # 低于阈值 → 无强差异 → 不触发
    assert fired == []


def test_think_calls_generate_and_push(monkeypatch):
    captured = {}

    async def fake_generate_and_push(connection_filter, gen_fn, **kw):
        captured["prompt"] = await gen_fn()
        captured["kw"] = kw

    import modules.thinking.frontend_channel as fc_mod
    monkeypatch.setattr(fc_mod, "generate_and_push", fake_generate_and_push)
    import modules.perception.trigger as trig_mod

    async def fake_llm(prompt, ctx):
        return prompt

    monkeypatch.setattr(trig_mod, "call_outreach_llm", fake_llm)
    asyncio.run(tt._think("screen_changed:主窗口"))
    assert "screen_changed:主窗口" in captured["prompt"]
    assert captured["kw"]["msg_type"] == "proactive"
    assert captured["kw"]["event"] == "trigger_think"
