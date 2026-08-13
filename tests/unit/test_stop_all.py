"""scripts/stop_all.py — 一键停止脚本（跨平台）"""
import os
import subprocess

import pytest


def _set_win(monkeypatch, val):
    import scripts.stop_all as sa
    monkeypatch.setattr(sa, "IS_WIN", val)


def test_find_pids_unix(monkeypatch):
    import scripts.stop_all as sa
    _set_win(monkeypatch, False)

    class R:
        stdout = "1234\n5678\n"

    monkeypatch.setattr(sa.subprocess, "run", lambda *a, **k: R())
    assert sa._find_pids_on_port(8080) == ["1234", "5678"]


def test_find_pids_unix_empty(monkeypatch):
    import scripts.stop_all as sa
    _set_win(monkeypatch, False)

    class R:
        stdout = ""

    monkeypatch.setattr(sa.subprocess, "run", lambda *a, **k: R())
    assert sa._find_pids_on_port(8080) == []


def test_find_pids_win(monkeypatch):
    import scripts.stop_all as sa
    _set_win(monkeypatch, True)

    class R:
        stdout = "  TCP  0.0.0.0:8080  0.0.0.0:0  LISTENING  4321\n"

    monkeypatch.setattr(sa.subprocess, "run", lambda *a, **k: R())
    assert sa._find_pids_on_port(8080) == ["4321"]


def test_find_pids_error(monkeypatch, capsys):
    import scripts.stop_all as sa
    _set_win(monkeypatch, False)
    monkeypatch.setattr(sa.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no lsof")))
    assert sa._find_pids_on_port(8080) == []
    assert "Error" in capsys.readouterr().out


def test_kill_pid_unix(monkeypatch):
    import scripts.stop_all as sa
    _set_win(monkeypatch, False)
    killed = []
    monkeypatch.setattr(sa.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    assert sa._kill_pid("1234") is True
    assert killed == [(1234, sa.signal.SIGTERM)]


def test_kill_pid_unix_process_gone(monkeypatch):
    import scripts.stop_all as sa
    _set_win(monkeypatch, False)
    monkeypatch.setattr(sa.os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()))
    assert sa._kill_pid("1234") is False


def test_kill_pid_win(monkeypatch):
    import scripts.stop_all as sa
    _set_win(monkeypatch, True)

    class R:
        returncode = 0

    monkeypatch.setattr(sa.subprocess, "run", lambda *a, **k: R())
    assert sa._kill_pid("4321", force=True) is True


def test_pid_alive_unix(monkeypatch):
    import scripts.stop_all as sa
    _set_win(monkeypatch, False)
    monkeypatch.setattr(sa.os, "kill", lambda pid, sig: None)
    assert sa._pid_alive("1234") is True
    monkeypatch.setattr(sa.os, "kill", lambda pid, sig: (_ for _ in ()).throw(OSError()))
    assert sa._pid_alive("1234") is False


def test_pid_alive_win(monkeypatch):
    import scripts.stop_all as sa
    _set_win(monkeypatch, True)

    class R:
        stdout = "PID 1234"

    monkeypatch.setattr(sa.subprocess, "run", lambda *a, **k: R())
    assert sa._pid_alive("1234") is True


def test_main_no_pids(monkeypatch, capsys):
    import scripts.stop_all as sa
    monkeypatch.setattr(sa, "_find_pids_on_port", lambda port: [])
    monkeypatch.setenv("SERVER_PORT", "8080")
    sa.main()
    assert "not in use" in capsys.readouterr().out


def test_main_kill_flow(monkeypatch, capsys):
    import scripts.stop_all as sa
    monkeypatch.setattr(sa, "_find_pids_on_port", lambda port: ["111", "222"])
    kill_log = []

    def fake_kill(pid, force=False):
        kill_log.append((pid, force))
        return True  # 优雅与 force 都成功

    def fake_alive(pid):
        return True  # 第一次后仍活着 → 触发 force

    monkeypatch.setattr(sa, "_kill_pid", fake_kill)
    monkeypatch.setattr(sa, "_pid_alive", fake_alive)
    monkeypatch.setattr(sa.time, "sleep", lambda *a, **k: None)
    monkeypatch.setenv("SERVER_PORT", "8080")
    sa.main()
    out = capsys.readouterr().out
    assert "Sent graceful stop" in out
    assert "Force-killed" in out
    assert ("111", False) in kill_log
    assert ("111", True) in kill_log
