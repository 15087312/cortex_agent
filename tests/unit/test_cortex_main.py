"""cortex/main.py — CLI 入口测试（mock 网络/子进程/execvp，绝不真实启动）"""
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import cortex.main as cm


# ── _wait_for_server ──

def test_wait_for_server_success(monkeypatch):
    resp = MagicMock()
    resp.status = 200

    class _FakeResp:
        def __enter__(self):
            return resp

        def __exit__(self, *a):
            return False

    fake_urlopen = MagicMock(return_value=_FakeResp())
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(cm.time, "time", lambda: 1000.0)
    assert cm._wait_for_server("http://x:1", timeout=5) is True
    fake_urlopen.assert_called_once()


def test_wait_for_server_error_then_success(monkeypatch):
    resp = MagicMock()
    resp.status = 200

    class _FakeResp:
        def __enter__(self):
            return resp

        def __exit__(self, *a):
            return False

    import urllib.error
    calls = {"n": 0}

    def fake_urlopen(req, timeout=2):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("conn refused")
        return _FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    times = iter([1000.0, 1000.5, 1001.0])
    monkeypatch.setattr(cm.time, "time", lambda: next(times))
    monkeypatch.setattr(cm.time, "sleep", lambda *a: None)
    assert cm._wait_for_server("http://x:1", timeout=5) is True


def test_wait_for_server_timeout(monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=2):
        raise urllib.error.URLError("down")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    cur = [2000.0]

    def fake_time():
        cur[0] += 0.5
        return cur[0]

    monkeypatch.setattr(cm.time, "time", fake_time)
    monkeypatch.setattr(cm.time, "sleep", lambda *a: None)
    assert cm._wait_for_server("http://x:1", timeout=5) is False


# ── _port_in_use ──

def test_port_in_use_true(monkeypatch):
    sock = MagicMock()
    sock.connect_ex.return_value = 0
    sock.__enter__.return_value = sock  # with 上下文返回自身
    monkeypatch.setattr(cm.socket, "socket", lambda *a, **k: sock)
    assert cm._port_in_use(8080) is True


def test_port_in_use_false(monkeypatch):
    sock = MagicMock()
    sock.connect_ex.return_value = 1
    sock.__enter__.return_value = sock
    monkeypatch.setattr(cm.socket, "socket", lambda *a, **k: sock)
    assert cm._port_in_use(8080) is False


# ── _get_project_root ──

def test_get_project_root_from_env(monkeypatch):
    monkeypatch.setenv("CORTEX_ROOT", "/tmp/root")
    assert cm._get_project_root() == Path("/tmp/root")


def test_get_project_root_walk(monkeypatch):
    monkeypatch.delenv("CORTEX_ROOT", raising=False)
    root = cm._get_project_root()
    assert (root / "api" / "main.py").exists()


# ── parse_args ──

def test_parse_args_defaults(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["cortex"])
    monkeypatch.setenv("MAX_WORKERS", "1")
    args = cm.parse_args()
    assert args.port == 8080
    assert args.workers == 1


def test_parse_args_custom(monkeypatch):
    monkeypatch.setattr(
        sys, "argv",
        ["cortex", "--port", "9000", "--no-tui", "--workers", "2", "--api-key", "k"],
    )
    args = cm.parse_args()
    assert args.port == 9000
    assert args.no_tui is True
    assert args.workers == 2
    assert args.api_key == "k"


# ── start_backend ──

def test_start_backend_forces_single_worker(monkeypatch):
    args = MagicMock()
    args.workers = 4
    args.port = 8080
    args.host = "127.0.0.1"
    args.api_key = None
    args.url = None
    proc = MagicMock()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(cm, "_get_project_root", lambda: Path("/tmp"))
    p = cm.start_backend(args)
    assert p is proc
    assert args.workers == 1  # 已降级


def test_start_backend_single_worker_no_downgrade(monkeypatch):
    args = MagicMock()
    args.workers = 1
    args.port = 8080
    args.host = "127.0.0.1"
    args.api_key = "secret"
    proc = MagicMock()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(cm, "_get_project_root", lambda: Path("/tmp"))
    p = cm.start_backend(args)
    assert p is proc


# ── launch_tui / main ──

def test_launch_tui_exec(monkeypatch):
    args = MagicMock()
    args.api_url = None
    args.host = "127.0.0.1"
    args.port = 8080
    args.api_key = "k"
    args.model = "gpt"
    execvp = MagicMock()
    monkeypatch.setattr(cm.os, "execvp", execvp)
    cm.launch_tui(args)
    execvp.assert_called_once()
    cmd = execvp.call_args[0][1]
    assert "--api-url" in cmd
    assert "--api-key" in cmd


def test_main_connect_existing(monkeypatch):
    args = MagicMock()
    args.api_url = "http://x:8080"
    args.api_key = "k"
    args.port = 8080
    args.host = "127.0.0.1"
    args.model = None
    args.no_tui = False
    args.workers = 1
    args.launch_at_startup = None
    monkeypatch.setattr(cm, "parse_args", lambda: args)
    monkeypatch.setattr(cm, "_wait_for_server", lambda *a, **k: True)
    launch = MagicMock()
    monkeypatch.setattr(cm, "launch_tui", launch)
    cm.main()
    launch.assert_called_once()


def test_main_start_backend_no_tui(monkeypatch):
    args = MagicMock()
    args.api_url = None
    args.api_key = "k"
    args.port = 8080
    args.host = "127.0.0.1"
    args.model = None
    args.no_tui = True
    args.workers = 1
    args.launch_at_startup = None
    args.qt = False  # 关闭真实 Qt 前端启动分支
    monkeypatch.setattr(cm, "parse_args", lambda: args)
    monkeypatch.setattr(cm, "_port_in_use", lambda port: False)
    monkeypatch.setattr(cm, "_wait_for_server", lambda *a, **k: True)
    proc = MagicMock()
    proc.stderr = None  # 关闭 stderr 读取线程
    monkeypatch.setattr(cm, "start_backend", lambda a: proc)
    launch = MagicMock()
    monkeypatch.setattr(cm, "launch_tui", launch)
    cm.main()  # no_tui=True：不 launch_tui
    launch.assert_not_called()


def test_get_project_root_fallback(monkeypatch):
    monkeypatch.delenv("CORTEX_ROOT", raising=False)

    class FakePath:
        def __init__(self, p=None):
            self._p = p

        def resolve(self):
            return self

        @property
        def parent(self):
            return FakePath("parent")

        def __truediv__(self, other):
            return FakePath(self._p)

        def exists(self):
            return False

    monkeypatch.setattr(cm, "Path", lambda *a, **k: FakePath())
    root = cm._get_project_root()
    assert isinstance(root, FakePath)


def test_main_connect_no_tui_sleep_interrupt(monkeypatch):
    args = MagicMock()
    args.api_url = "http://x:8080"
    args.api_key = "k"
    args.port = 8080
    args.host = "127.0.0.1"
    args.model = None
    args.no_tui = True
    args.workers = 1
    args.qt = False
    monkeypatch.setattr(cm, "parse_args", lambda: args)

    def fake_sleep(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(cm.time, "sleep", fake_sleep)
    cm.main()  # 不应抛异常


def test_main_port_in_use_posix_kill(monkeypatch):
    args = MagicMock()
    args.api_url = None
    args.api_key = None
    args.port = 8080
    args.host = "127.0.0.1"
    args.model = None
    args.no_tui = True
    args.workers = 1
    args.qt = False
    monkeypatch.setattr(cm, "parse_args", lambda: args)
    monkeypatch.setattr(cm, "_get_project_root", lambda: Path("/tmp"))
    monkeypatch.setattr(cm.sys, "platform", "darwin")
    monkeypatch.setattr(cm, "_port_in_use", lambda port: True)
    monkeypatch.setattr("subprocess.check_output", lambda *a, **k: b"999\n")
    proc = MagicMock()
    proc.stderr = []
    monkeypatch.setattr(cm, "start_backend", lambda a: proc)
    monkeypatch.setattr(cm, "_wait_for_server", lambda *a, **k: True)
    monkeypatch.setattr("utils.port_discovery.save_backend_port", lambda _: None)
    killed = []
    monkeypatch.setattr(cm.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(cm.os, "getpid", lambda: 123)
    monkeypatch.setattr(cm.time, "sleep", lambda *a: None)
    cm.main()
    assert killed and killed[0][0] == 999


def test_main_wait_timeout_exit(monkeypatch):
    args = MagicMock()
    args.api_url = None
    args.api_key = None
    args.port = 8080
    args.host = "127.0.0.1"
    args.model = None
    args.no_tui = True
    args.workers = 1
    args.qt = False
    monkeypatch.setattr(cm, "parse_args", lambda: args)
    monkeypatch.setattr(cm, "_port_in_use", lambda port: False)
    monkeypatch.setattr(cm, "_wait_for_server", lambda *a, **k: False)
    proc = MagicMock()
    proc.stderr = None
    monkeypatch.setattr(cm, "start_backend", lambda a: proc)
    exited = []
    monkeypatch.setattr(cm.sys, "exit", lambda code: exited.append(code) or (_ for _ in ()).throw(SystemExit(code)))
    with pytest.raises(SystemExit):
        cm.main()
    assert exited and exited[0] == 1
    proc.terminate.assert_called_once()


def test_start_backend_save_port_exception(monkeypatch):
    args = MagicMock()
    args.api_url = None
    args.api_key = None
    args.port = 8080
    args.host = "127.0.0.1"
    args.model = None
    args.no_tui = True
    args.workers = 1
    args.qt = False
    monkeypatch.setattr(cm, "parse_args", lambda: args)
    monkeypatch.setattr(cm, "_port_in_use", lambda port: False)
    monkeypatch.setattr(cm, "_wait_for_server", lambda *a, **k: True)
    proc = MagicMock()
    proc.stderr = ["line\n".encode()]
    proc.__enter__ = lambda: proc
    monkeypatch.setattr(cm, "start_backend", lambda a: proc)
    # 触发 save_backend_port 抛异常的 except 分支
    monkeypatch.setattr(cm, "sys", sys)
    monkeypatch.setattr("utils.port_discovery.save_backend_port", lambda _: (_ for _ in ()).throw(ValueError("no")))
    launch = MagicMock()
    monkeypatch.setattr(cm, "launch_tui", launch)
    cm.main()
    launch.assert_not_called()


def test_cleanup_kill_on_timeout(monkeypatch):
    args = MagicMock()
    args.api_url = None
    args.api_key = None
    args.port = 8080
    args.host = "127.0.0.1"
    args.model = None
    args.no_tui = True
    args.workers = 1
    args.qt = False
    monkeypatch.setattr(cm, "parse_args", lambda: args)
    monkeypatch.setattr(cm, "_port_in_use", lambda port: False)
    monkeypatch.setattr(cm, "_wait_for_server", lambda *a, **k: True)
    proc = MagicMock()
    proc.stderr = None
    proc.terminate = MagicMock()
    # no_tui 分支的 backend_proc.wait() 抛 KeyboardInterrupt → cleanup()
    # cleanup 内 proc.wait(timeout=5) 抛 TimeoutExpired → 走 kill() 分支
    proc.wait = MagicMock(side_effect=[KeyboardInterrupt, subprocess.TimeoutExpired("uvicorn", 5)])
    proc.kill = MagicMock()
    monkeypatch.setattr(cm, "start_backend", lambda a: proc)
    exited = []
    monkeypatch.setattr(cm.sys, "exit", lambda code: exited.append(code) or (_ for _ in ()).throw(SystemExit(code)))
    with pytest.raises(SystemExit):
        cm.main()
    proc.kill.assert_called_once()  # 超时后走 kill 分支
