"""感知触发思考测试（冷却 + 强度阈值）"""
import time
from unittest.mock import AsyncMock, MagicMock

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
    """_think 走统一出口：LLM 返回内容 → 握手通过 → push_content 推送"""
    import asyncio
    import modules.perception.trigger as trg_mod
    import modules.thinking.frontend_channel as fc

    async def fake_llm(prompt, session_id="", role=None, tier="large"):
        return "主动消息内容"
    monkeypatch.setattr(trg_mod, "call_outreach_llm", fake_llm)

    pushed = []
    async def fake_push(sid, *, msg_type, event, content, role="assistant", data=None, persist=True):
        pushed.append((sid, msg_type, event, content))
        return True
    monkeypatch.setattr(fc, "_confirm_async", AsyncMock(return_value=True))
    monkeypatch.setattr(fc, "push_content", fake_push)

    asyncio.run(tt._think("屏幕变化"))
    assert pushed == [(None, "proactive", "trigger_think", "主动消息内容")]


def test_think_no_connections_drops(monkeypatch):
    """前端不可达（握手失败）时不调用 LLM——修复原"LLM 已调但广播落空"bug"""
    import asyncio
    import modules.perception.trigger as trg_mod
    import modules.thinking.frontend_channel as fc

    called = {"llm": 0}
    async def fake_llm(prompt, session_id="", role=None, tier="large"):
        called["llm"] += 1
        return "主动消息内容"
    monkeypatch.setattr(trg_mod, "call_outreach_llm", fake_llm)

    pushed = []
    async def fake_push(sid, *, msg_type, event, content, role="assistant", data=None, persist=True):
        pushed.append(content)
        return False
    monkeypatch.setattr(fc, "_confirm_async", AsyncMock(return_value=False))
    monkeypatch.setattr(fc, "push_content", fake_push)

    asyncio.run(tt._think("屏幕变化"))
    # 握手失败 → 跳过 LLM，不产生无处送达的内容
    assert called["llm"] == 0
    assert pushed == []


def test_think_empty_llm_no_push(monkeypatch):
    """LLM 返回空时不推送"""
    import asyncio
    import modules.perception.trigger as trg_mod
    import modules.thinking.frontend_channel as fc

    async def fake_llm(prompt, session_id="", role=None, tier="large"):
        return ""
    monkeypatch.setattr(trg_mod, "call_outreach_llm", fake_llm)
    pushed = []
    async def fake_push(sid, *, msg_type, event, content, role="assistant", data=None, persist=True):
        pushed.append(content)
        return True
    monkeypatch.setattr(fc, "_confirm_async", AsyncMock(return_value=True))
    monkeypatch.setattr(fc, "push_content", fake_push)

    asyncio.run(tt._think("屏幕变化"))
    assert pushed == []


def test_trigger_uses_category_and_payload(monkeypatch):
    """desc 应包含 category + payload 目标（有实际内容），不再固定为 source_type:"""
    _reset()
    fired = []

    def _fake_run(desc):
        fired.append(desc)

    monkeypatch.setattr(tt, "_run", _fake_run)
    monkeypatch.setattr(tt, "_has_active_connections", lambda: True)
    monkeypatch.setattr(tt.threading, "Thread", _SyncThread)

    class D:
        source_type = "perception"
        category = "screen_changed"
        intensity = 80
        payload = {"target_type": "screen", "change_type": "changed", "target": "主窗口"}

    tt._trigger([D()])
    assert fired == ["screen_changed:主窗口"]


def test_trigger_diff_no_payload(monkeypatch):
    """无 payload 时回退 category，不产生空描述"""
    _reset()
    fired = []

    def _fake_run(desc):
        fired.append(desc)

    monkeypatch.setattr(tt, "_run", _fake_run)
    monkeypatch.setattr(tt, "_has_active_connections", lambda: True)
    monkeypatch.setattr(tt.threading, "Thread", _SyncThread)

    class D:
        source_type = "perception"
        category = "file_modified"
        intensity = 90
        payload = {}

    tt._trigger([D()])
    assert fired == ["file_modified"]
