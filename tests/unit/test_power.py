"""utils/power.py — 防休眠（caffeinate）"""
import subprocess

import pytest

import utils.power as power


def _reset():
    power._proc = None


@pytest.fixture(autouse=True)
def _reset_power():
    _reset()
    yield
    _reset()


def test_apply_non_macos(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    assert power.apply(True) is False
    assert power.is_active() is False


def test_apply_enable_success(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    fake_proc = subprocess.Popen(["true"])
    fake_proc.poll = lambda: None  # 存活
    monkeypatch.setattr(power.subprocess, "Popen", lambda *a, **k: fake_proc)
    assert power.apply(True) is True
    assert power.is_active() is True


def test_apply_enable_failure(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(power.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert power.apply(True) is False
    assert power.is_active() is False


def test_apply_disable_terminates(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    proc = subprocess.Popen(["true"])
    proc.poll = lambda: None
    monkeypatch.setattr(power.subprocess, "Popen", lambda *a, **k: proc)
    power.apply(True)
    proc.terminate = lambda: None
    proc.wait = lambda timeout=0: None
    assert power.apply(False) is True
    assert power.is_active() is False


def test_apply_disable_terminate_timeout_kills(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    proc = subprocess.Popen(["true"])
    proc.poll = lambda: None
    monkeypatch.setattr(power.subprocess, "Popen", lambda *a, **k: proc)
    power.apply(True)
    killed = []
    proc.terminate = lambda: None
    proc.wait = lambda timeout=0: (_ for _ in ()).throw(subprocess.TimeoutExpired("caffeinate", 5))
    proc.kill = lambda: killed.append(True)
    assert power.apply(False) is True
    assert killed == [True]


def test_apply_enable_twice_no_dup(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    spawned = []
    real_popen = power.subprocess.Popen  # 先保存真实实现，避免 fake 内部递归

    def fake_popen(*a, **k):
        p = real_popen(["true"])
        p.poll = lambda: None
        spawned.append(p)
        return p

    monkeypatch.setattr(power.subprocess, "Popen", fake_popen)
    power.apply(True)
    power.apply(True)  # 已启用不再新建
    assert len(spawned) == 1


def test_is_active_false_by_default():
    assert power.is_active() is False
