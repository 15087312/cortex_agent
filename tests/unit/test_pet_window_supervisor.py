"""桌宠窗口监管器测试：拉起/停止/状态/收编/崩溃重启（全 mock，不起真进程）"""
import json
import os
from pathlib import Path

import pytest

from modules.desktop_pet import window_supervisor as sup_mod
from modules.desktop_pet.window_supervisor import PetWindowSupervisor


class FakePopen:
    """最小 Popen 替身：可控存活/退出码"""

    instances = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.pid = 90000 + len(FakePopen.instances)
        self._alive = True
        self._exit_code = None
        self.terminated = False
        self.killed = False
        FakePopen.instances.append(self)

    def poll(self):
        return None if self._alive else self._exit_code

    def terminate(self):
        self.terminated = True
        self._alive = False
        self._exit_code = -15

    def kill(self):
        self.killed = True
        self._alive = False
        self._exit_code = -9

    def wait(self, timeout=None):
        self._alive = False
        if self._exit_code is None:
            self._exit_code = -15
        return self._exit_code

    def die(self, code=1):
        """模拟崩溃：进程已死（监管器未调用 terminate）"""
        self._alive = False
        self._exit_code = code


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """隔离 HOME（pid/log 文件）+ mock Popen/ps 探测/进程登记"""
    FakePopen.instances.clear()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CORTEX_DISABLE_PET", "0")
    monkeypatch.setattr(sup_mod.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(sup_mod, "_pid_is_pet", lambda pid: True)
    import cortex.process_registry as pr
    monkeypatch.setattr(pr, "register", lambda name, proc: None)
    monkeypatch.setattr(pr, "unregister", lambda name: None)
    yield tmp_path


def _sup(monkeypatch):
    s = PetWindowSupervisor()
    # 不起真监控线程，tick 手动触发
    monkeypatch.setattr(s, "_ensure_monitor", lambda: None)
    return s


def _pid_file(tmp_path):
    return Path(tmp_path) / ".cortex" / "pet_window_pid.json"


def test_start_spawns_and_status_running(monkeypatch, tmp_path):
    s = _sup(monkeypatch)
    r = s.start()
    assert r["ok"] is True and r["already_running"] is False
    assert len(FakePopen.instances) == 1
    st = s.status()
    assert st["running"] is True and st["pid"] == r["pid"]
    # PID 文件落盘
    data = json.loads(_pid_file(tmp_path).read_text(encoding="utf-8"))
    assert data["pid"] == r["pid"]


def test_start_already_running_no_duplicate(monkeypatch):
    s = _sup(monkeypatch)
    r1 = s.start()
    r2 = s.start()
    assert r2["ok"] is True and r2["already_running"] is True
    assert r2["pid"] == r1["pid"]
    assert len(FakePopen.instances) == 1


def test_stop_terminates_and_clears(monkeypatch, tmp_path):
    s = _sup(monkeypatch)
    r = s.start()
    proc = FakePopen.instances[0]
    out = s.stop()
    assert out["ok"] is True
    assert proc.terminated is True
    assert not _pid_file(tmp_path).exists()
    st = s.status()
    assert st["running"] is False and st["expected"] is False


def test_stop_when_not_running(monkeypatch):
    s = _sup(monkeypatch)
    out = s.stop()
    assert out["ok"] is True and out["already_stopped"] is True
    assert len(FakePopen.instances) == 0


def test_start_refused_when_disabled(monkeypatch):
    monkeypatch.setenv("CORTEX_DISABLE_PET", "1")
    s = _sup(monkeypatch)
    r = s.start()
    assert r["ok"] is False
    assert len(FakePopen.instances) == 0


def test_adopt_live_pet_via_pidfile(monkeypatch, tmp_path):
    s = _sup(monkeypatch)
    _pid_file(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _pid_file(tmp_path).write_text(json.dumps({"pid": 4242, "started_at": 1.0}), encoding="utf-8")
    monkeypatch.setattr(sup_mod, "_pid_alive", lambda pid: pid == 4242)
    st = s.status()
    assert st["running"] is True and st["pid"] == 4242
    # 已收编不再拉新进程
    r = s.start()
    assert r["already_running"] is True and r["pid"] == 4242
    assert len(FakePopen.instances) == 0


def test_stale_pidfile_does_not_kill(monkeypatch, tmp_path):
    """PID 文件指向已死进程：只清文件、不 kill、不收编，直接拉新进程"""
    s = _sup(monkeypatch)
    _pid_file(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _pid_file(tmp_path).write_text(json.dumps({"pid": 4243, "started_at": 1.0}), encoding="utf-8")
    monkeypatch.setattr(sup_mod, "_pid_alive", lambda pid: False)
    killed = []
    monkeypatch.setattr(sup_mod, "_terminate_pid", lambda pid, timeout=3.0: killed.append(pid) or True)
    r = s.start()
    assert r["ok"] is True and r["pid"] != 4243
    assert killed == []
    assert len(FakePopen.instances) == 1


def test_monitor_restarts_on_unexpected_exit(monkeypatch):
    s = _sup(monkeypatch)
    s.start()
    assert len(FakePopen.instances) == 1
    FakePopen.instances[0].die(code=139)  # 模拟段错误崩溃
    s._monitor_tick()
    assert len(FakePopen.instances) == 2  # 已重启
    st = s.status()
    assert st["running"] is True and st["restarts"] == 1
    assert st["exit_code"] is None  # 新进程覆盖


def test_monitor_gives_up_after_streak(monkeypatch):
    s = _sup(monkeypatch)
    s.start()
    # 连续崩溃超限
    for _ in range(sup_mod._MAX_DEATH_STREAK + 1):
        FakePopen.instances[-1].die(code=1)
        s._monitor_tick()
    st = s.status()
    assert st["gave_up_auto_restart"] is True
    n = len(FakePopen.instances)
    # 放弃后不再拉起
    s._monitor_tick()
    assert len(FakePopen.instances) == n
    # 用户手动 start 重置并恢复
    r = s.start()
    assert r["ok"] is True
    assert s.status()["gave_up_auto_restart"] is False


def test_user_stop_disables_auto_restart(monkeypatch):
    s = _sup(monkeypatch)
    s.start()
    s.stop()
    n = len(FakePopen.instances)
    s._monitor_tick()
    assert len(FakePopen.instances) == n  # 用户主动关闭后不复活
