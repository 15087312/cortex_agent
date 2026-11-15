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


# ── prevent_sleep 端口 ────────────────────────────────────────────────

def test_accessor_falls_back_to_builtin(drop_capability):
    """端口没接线上（脚本入口、没跑 bootstrap）时用内置实现，不是报错。"""
    drop_capability("prevent_sleep")
    assert power.get_prevent_sleep() is power._builtin_prevent_sleep


def test_port_takeover_replaces_builtin(take_over):
    """接管端口后装配层拿到的是插件那套——换机制时子进程登记的必须是新实现。"""
    class Other:
        name = "systemd-inhibit"

        def apply(self, enabled):
            return "applied"

        def process(self):
            return "child"

    other = Other()
    take_over("prevent_sleep", lambda: other)
    assert power.get_prevent_sleep() is other
    assert power.get_prevent_sleep().process() == "child"


def test_builtin_delegates_keep_module_patches(monkeypatch):
    """内置实现必须转调模块级函数：调用点 patch 的是 ``power.apply`` 这个名字，
    另存一份实现会让既有 mock 静默失效（表现为真去起 caffeinate 子进程）。"""
    calls = []
    monkeypatch.setattr(power, "apply", lambda enabled: calls.append(enabled) or True)
    monkeypatch.setattr(power, "_proc", "proc-handle")
    m = power.get_prevent_sleep()
    assert m.apply(True) is True
    assert calls == [True]
    assert m.process() == "proc-handle"
