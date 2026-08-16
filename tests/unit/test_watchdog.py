"""cortex/watchdog 测试：父进程存活检测 / enable 各分支"""
import os

import cortex.watchdog as wd


def test_parent_alive_low_pid():
    assert wd._parent_alive(0) is False
    assert wd._parent_alive(1) is False


def test_parent_alive_process_lookup_error(monkeypatch):
    monkeypatch.setattr(wd.os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()))
    assert wd._parent_alive(100) is False


def test_parent_alive_permission_error(monkeypatch):
    monkeypatch.setattr(wd.os, "kill", lambda pid, sig: (_ for _ in ()).throw(PermissionError()))
    assert wd._parent_alive(100) is True


def test_parent_alive_oserror(monkeypatch):
    monkeypatch.setattr(wd.os, "kill", lambda pid, sig: (_ for _ in ()).throw(OSError()))
    assert wd._parent_alive(100) is True


def test_parent_alive_ok(monkeypatch):
    monkeypatch.setattr(wd.os, "kill", lambda pid, sig: None)
    assert wd._parent_alive(100) is True


def test_enable_no_env(monkeypatch):
    monkeypatch.setattr(wd.os.environ, "get", lambda k, d="": "")
    wd._started = False
    assert wd.enable() is False


def test_enable_invalid_env(monkeypatch):
    monkeypatch.setattr(wd.os.environ, "get", lambda k, d="": "not-a-pid")
    wd._started = False
    assert wd.enable() is False


def test_enable_self_pid(monkeypatch):
    monkeypatch.setattr(wd.os.environ, "get", lambda k, d="": str(os.getpid()))
    wd._started = False
    assert wd.enable() is False


def test_enable_low_pid(monkeypatch):
    monkeypatch.setattr(wd.os.environ, "get", lambda k, d="": "1")
    wd._started = False
    assert wd.enable() is False


def test_enable_starts_thread(monkeypatch):
    monkeypatch.setattr(wd.os.environ, "get", lambda k, d="": "99999")
    started = []
    monkeypatch.setattr(wd.threading.Thread, "start", lambda self: started.append(True))
    wd._started = False
    assert wd.enable() is True
    assert started
    assert wd.enable() is True  # 已启动 → 直接返回 True，不重复起线程
    assert len(started) == 1
    wd._started = False
