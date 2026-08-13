"""scripts/start_all.py — 一键启动脚本"""
import os
import subprocess
import sys

import pytest


def test_graceful_shutdown_stops_scheduler(monkeypatch, capsys):
    import scripts.start_all as sa
    stopped = []

    class FakeScheduler:
        def stop(self):
            stopped.append(True)

    sa._memory_scheduler = FakeScheduler()
    monkeypatch.setattr(sa.sys, "exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    with pytest.raises(SystemExit):
        sa._graceful_shutdown(2, None)
    assert stopped == [True]
    assert "记忆调度器已停止" in capsys.readouterr().out
    sa._memory_scheduler = None


def test_graceful_shutdown_no_scheduler(monkeypatch, capsys):
    import scripts.start_all as sa
    sa._memory_scheduler = None
    monkeypatch.setattr(sa.sys, "exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    with pytest.raises(SystemExit):
        sa._graceful_shutdown(2, None)
    out = capsys.readouterr().out
    assert "正在关闭" in out


def test_main_builds_correct_command(monkeypatch, capsys):
    import scripts.start_all as sa
    captured = {}

    class FakeArgs:
        debug = False

    class FakeParser:
        def __init__(self, *a, **k):
            pass

        def add_argument(self, *a, **k):
            pass

        def parse_args(self):
            return FakeArgs()

    class FakeArgparse:
        ArgumentParser = FakeParser

    # main() 内部是局部 `import argparse`，需 patch sys.modules
    monkeypatch.setitem(__import__("sys").modules, "argparse", FakeArgparse)
    monkeypatch.setattr(sa.signal, "signal", lambda *a, **k: None)
    monkeypatch.setattr(sa.sys, "exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    monkeypatch.setenv("SERVER_PORT", "9090")
    monkeypatch.setenv("SERVER_HOST", "0.0.0.0")

    def fake_run(cmd, cwd=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return subprocess.CompletedProcess(cmd, 3)

    monkeypatch.setattr(sa.subprocess, "run", fake_run)
    with pytest.raises(SystemExit) as e:
        sa.main()
    assert e.value.code == 3
    assert captured["cmd"][1] == "-m"
    assert captured["cmd"][3] == "api.main:app"
    assert "--port" in captured["cmd"]
    assert "9090" in captured["cmd"]
    assert "--host" in captured["cmd"]
    assert "0.0.0.0" in captured["cmd"]
