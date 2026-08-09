"""感知触发思考测试（冷却 + 强度阈值）"""
import time

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
