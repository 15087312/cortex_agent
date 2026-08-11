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


def _allow_outreach(monkeypatch):
    """默认允许主动搭话三层闸门（隔离真实用户配置）"""
    monkeypatch.setattr(
        "modules.perception.trigger.outreach_trigger_allowed",
        lambda: True,
    )


class _Diff:
    """与真实 Difference 模型字段保持一致（无 description，用 category+payload）"""

    def __init__(self, intensity=80):
        self.intensity = intensity
        self.source_type = "screen"
        self.category = "screen_changed"
        self.payload = {"target": "主窗口", "change_type": "changed"}


def test_intensity_threshold(monkeypatch):
    _reset()
    _allow_outreach(monkeypatch)
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
    _allow_outreach(monkeypatch)
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
    _allow_outreach(monkeypatch)
    fired = []

    def _fake_run(desc):
        fired.append(desc)

    monkeypatch.setattr(tt, "_run", _fake_run)
    monkeypatch.setattr(tt, "_has_active_connections", lambda: False)
    tt._trigger([_Diff(99)])  # 即使强度很高也不触发
    assert fired == []


def test_global_switch_off_blocks(monkeypatch):
    """全局主动搭话总开关关闭时，即使高强度变化也不触发（三层闸门第 1 层）"""
    _reset()
    fired = []

    def _fake_run(desc):
        fired.append(desc)

    monkeypatch.setattr(tt, "_run", _fake_run)
    monkeypatch.setattr(tt, "_has_active_connections", lambda: True)
    monkeypatch.setattr(
        "modules.perception.trigger.outreach_trigger_allowed",
        lambda: False,
    )
    tt._trigger([_Diff(99)])
    assert fired == []


def test_no_enabled_session_blocks(monkeypatch):
    """没有任何会话开启主动搭话时不触发（三层闸门第 2 层）"""
    _reset()
    fired = []

    def _fake_run(desc):
        fired.append(desc)

    monkeypatch.setattr(tt, "_run", _fake_run)
    monkeypatch.setattr(tt, "_has_active_connections", lambda: True)
    # outreach_trigger_allowed 内部读库：mock 成无会话开启 → 闸门拦下
    monkeypatch.setattr(
        "modules.perception.trigger.outreach_trigger_allowed",
        lambda: False,
    )
    tt._trigger([_Diff(99)])
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


def test_trigger_uses_category_and_payload(monkeypatch):
    """desc 应包含 category + payload 目标（有实际内容），不再固定为 source_type:"""
    _reset()
    _allow_outreach(monkeypatch)
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
    _allow_outreach(monkeypatch)
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


def test_think_forwards_real_desc_to_llm(monkeypatch):
    """真实 _think 链路：prompt 必须包含真实差异描述（§27.4 回归，防 desc 空转）"""
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

    import asyncio
    asyncio.run(tt._think("screen_changed:主窗口:亮度变化"))

    assert "screen_changed:主窗口:亮度变化" in captured["prompt"]
    assert "检测到环境高强度变化" in captured["prompt"]
    assert captured["kw"]["msg_type"] == "proactive"
    assert captured["kw"]["event"] == "trigger_think"


def test_think_empty_desc_still_formats(monkeypatch):
    """desc 为空时 prompt 仍可构造（不因空描述崩溃）"""
    captured = {}

    async def fake_generate_and_push(connection_filter, gen_fn, **kw):
        captured["prompt"] = await gen_fn()

    import modules.thinking.frontend_channel as fc_mod
    monkeypatch.setattr(fc_mod, "generate_and_push", fake_generate_and_push)
    import modules.perception.trigger as trig_mod

    async def fake_llm(prompt, ctx):
        return prompt
    monkeypatch.setattr(trig_mod, "call_outreach_llm", fake_llm)

    import asyncio
    asyncio.run(tt._think(""))
    assert "检测到环境高强度变化" in captured["prompt"]
