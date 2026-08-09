"""系统级设置真实生效测试：开机启动（LaunchAgent）/ 防休眠（caffeinate）/ 自启启动器"""
import sys
from unittest.mock import MagicMock, patch

import utils.autostart as autostart
import utils.power as power
import scripts.autostart_launcher as launcher


# ── 开机启动（LaunchAgent）──

def test_autostart_non_mac():
    with patch.object(autostart.sys, "platform", "linux"):
        assert autostart.apply(True) is False
        assert autostart.is_enabled() is False


def test_autostart_enable_writes_plist(monkeypatch, tmp_path):
    with patch.object(autostart.sys, "platform", "darwin"):
        launch_dir = tmp_path / "LaunchAgents"
        monkeypatch.setattr(autostart.os.path, "expanduser", lambda p: str(launch_dir))
        run = MagicMock()
        monkeypatch.setattr(autostart.subprocess, "run", run)
        assert autostart.apply(True) is True
        assert (launch_dir / "com.cortex.agent.plist").exists()  # 真实写入 plist
        assert run.called  # launchctl load
        assert "load" in run.call_args[0][0]


def test_autostart_disable_removes(monkeypatch, tmp_path):
    with patch.object(autostart.sys, "platform", "darwin"):
        launch_dir = tmp_path / "LaunchAgents"
        launch_dir.mkdir(parents=True)
        plist = launch_dir / "com.cortex.agent.plist"
        plist.write_text("x")
        monkeypatch.setattr(autostart.os.path, "expanduser", lambda p: str(launch_dir))
        run = MagicMock()
        monkeypatch.setattr(autostart.subprocess, "run", run)
        assert autostart.apply(False) is True
        assert run.called  # launchctl unload
        assert "unload" in run.call_args[0][0]
        assert not plist.exists()  # plist 已删除


# ── 防休眠（caffeinate）──

def test_power_non_mac():
    with patch.object(power.sys, "platform", "linux"):
        assert power.apply(True) is False


def test_power_enable_starts_caffeinate(monkeypatch):
    with patch.object(power.sys, "platform", "darwin"):
        power._proc = None
        proc = MagicMock()
        proc.poll.return_value = None
        monkeypatch.setattr(power.subprocess, "Popen", lambda *a, **k: proc)
        assert power.apply(True) is True
        assert power._proc is proc
        assert power.is_active() is True
        power._proc = None


def test_power_disable_terminates(monkeypatch):
    with patch.object(power.sys, "platform", "darwin"):
        proc = MagicMock()
        power._proc = proc
        assert power.apply(False) is True
        proc.terminate.assert_called_once()
        assert power._proc is None


def test_power_enable_already_active():
    with patch.object(power.sys, "platform", "darwin"):
        proc = MagicMock()
        power._proc = proc
        power.apply(True)  # 已激活 → 不再重复启动
        power.subprocess.Popen  # noop
        assert power._proc is proc
        power._proc = None


# ── 自启启动器 ──

def test_launcher_starts_backend_and_frontend(monkeypatch):
    procs = []
    popen = MagicMock(side_effect=lambda *a, **k: procs.append(a) or MagicMock())
    monkeypatch.setattr(launcher.subprocess, "Popen", popen)
    launcher.main()
    assert len(procs) == 2  # 后端 + 前端
    # 第一个是 uvicorn 后端
    assert "uvicorn" in procs[0][0]
    # 第二个是 frontend/main.py
    assert procs[1][0][-1].endswith("frontend/main.py")
