"""感知触发思考测试（冷却 + 强度阈值）"""
import time
from unittest.mock import MagicMock

import pytest

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


class _Diff:
    def __init__(self, intensity=80):
        self.intensity = intensity
        self.source_type = "screen"
        self.description = "变化"


def test_intensity_threshold(monkeypatch):
    _reset()
    fired = []

    def _fake_run(desc):
        fired.append(desc)

    monkeypatch.setattr(tt, "_run", _fake_run)
    monkeypatch.setattr(tt, "_has_active_connections", lambda: True)
    monkeypatch.setattr(tt.threading, "Thread", _SyncThread)
    tt._trigger([_Diff(10)])  # 低于默认阈值 50 → 不触发
    assert fired == []
    _reset()  # 重置冷却，避免上一次调用占用冷却
    tt._trigger([_Diff(80)])  # 高于阈值 → 触发
    assert len(fired) == 1


def test_cooldown_blocks(monkeypatch):
    _reset()
    fired = []

    def _fake_run(desc):
        fired.append(desc)

    monkeypatch.setattr(tt, "_run", _fake_run)
    monkeypatch.setattr(tt, "_has_active_connections", lambda: True)
    monkeypatch.setattr(tt.threading, "Thread", _SyncThread)
    tt._trigger([_Diff(80)])
    assert len(fired) == 1
    tt._trigger([_Diff(90)])  # 冷却内（默认 60s）→ 不触发
    assert len(fired) == 1
    with tt._lock:
        tt._state["last"] = time.time() - 9999  # 模拟冷却结束
    tt._trigger([_Diff(80)])
    assert len(fired) == 2


def test_no_active_connections_skips(monkeypatch):
    """前端未连接时不触发（不发 LLM，不广播）"""
    _reset()
    fired = []

    def _fake_run(desc):
        fired.append(desc)

    monkeypatch.setattr(tt, "_run", _fake_run)
    monkeypatch.setattr(tt, "_has_active_connections", lambda: False)
    tt._trigger([_Diff(99)])  # 即使强度很高也不触发
    assert fired == []


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


def test_think_broadcasts_to_connections(monkeypatch):
    """_think 全链路：LLM 返回内容 → 广播到所有活跃连接"""
    import asyncio
    import modules.perception.trigger as trg_mod
    import modules.thinking.api_stream as stream_mod

    async def fake_llm(prompt, session_id="", role=None, tier="large"):
        return "主动消息内容"
    monkeypatch.setattr(trg_mod, "call_outreach_llm", fake_llm)

    cm = MagicMock()
    cm.active_connections = {"s1": object(), "s2": object()}
    cm.send_json_from_thread.return_value = True
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    monkeypatch.setattr(stream_mod, "_build_event", lambda **kw: {"event": kw.get("event"), "content": kw.get("content")})

    asyncio.run(tt._think("屏幕变化"))
    assert cm.send_json_from_thread.call_count == 2


def test_think_no_connections_drops(monkeypatch):
    """BUG 场景：无活跃连接时 LLM 已调用但广播落空（消息丢失）"""
    import asyncio
    import modules.perception.trigger as trg_mod
    import modules.thinking.api_stream as stream_mod

    called = {"llm": 0}
    async def fake_llm(prompt, session_id="", role=None, tier="large"):
        called["llm"] += 1
        return "主动消息内容"
    monkeypatch.setattr(trg_mod, "call_outreach_llm", fake_llm)

    cm = MagicMock()
    cm.active_connections = {}  # 前端未连接
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    monkeypatch.setattr(stream_mod, "_build_event", lambda **kw: {"event": kw.get("event")})

    asyncio.run(tt._think("屏幕变化"))
    # LLM 被调了（旧行为），但消息没送达任何连接 → 前端收不到
    assert called["llm"] == 1
    assert cm.send_json_from_thread.call_count == 0


def test_think_empty_llm_no_push(monkeypatch):
    """LLM 返回空时不广播"""
    import asyncio
    import modules.perception.trigger as trg_mod
    import modules.thinking.api_stream as stream_mod

    async def fake_llm(prompt, session_id="", role=None, tier="large"):
        return ""
    monkeypatch.setattr(trg_mod, "call_outreach_llm", fake_llm)
    cm = MagicMock()
    cm.active_connections = {"s1": object()}
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    monkeypatch.setattr(stream_mod, "_build_event", lambda **kw: {"event": kw.get("event")})

    asyncio.run(tt._think("屏幕变化"))
    assert cm.send_json_from_thread.call_count == 0
