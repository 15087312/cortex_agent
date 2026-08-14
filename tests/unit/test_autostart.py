"""utils/autostart — 开机启动（LaunchAgent）防御分支补测"""
from unittest.mock import patch

import utils.autostart as autostart


def test_apply_non_mac_returns_false():
    with patch.object(autostart.sys, "platform", "linux"):
        assert autostart.apply(True) is False


def test_apply_disable_when_no_plist(monkeypatch):
    """禁用时 plist 不存在 → 跳过 unload/unlink（57->63）"""
    with patch.object(autostart.sys, "platform", "darwin"):
        monkeypatch.setattr(autostart.os.path, "exists", lambda p: False)
        run = []
        monkeypatch.setattr(autostart.subprocess, "run", lambda *a, **k: run.append(a))
        assert autostart.apply(False) is True
        assert run == []  # 未调用 launchctl unload


def test_apply_disable_unlink_oserror_ignored(monkeypatch):
    """禁用时 plist 存在但删除失败 → 不中断，返回 True（61-62）"""
    with patch.object(autostart.sys, "platform", "darwin"):
        monkeypatch.setattr(autostart.os.path, "exists", lambda p: True)
        monkeypatch.setattr(autostart.os, "unlink", lambda p: (_ for _ in ()).throw(OSError("busy")))
        monkeypatch.setattr(autostart.subprocess, "run", lambda *a, **k: None)
        assert autostart.apply(False) is True


def test_apply_exception_returns_false(monkeypatch):
    """makedirs 等异常 → 捕获并返回 False（65-67）"""
    with patch.object(autostart.sys, "platform", "darwin"):
        monkeypatch.setattr(autostart.os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(PermissionError("denied")))
        assert autostart.apply(True) is False


def test_apply_enable_writes_plist_and_loads(monkeypatch, tmp_path):
    """启用 → 写 plist + launchctl load（51-55）"""
    launch_dir = tmp_path / "LaunchAgents"
    launch_dir.mkdir(parents=True)
    with patch.object(autostart.sys, "platform", "darwin"):
        monkeypatch.setattr(autostart.os.path, "expanduser", lambda p: str(launch_dir))
        run = []
        monkeypatch.setattr(autostart.subprocess, "run", lambda *a, **k: run.append(a))
        assert autostart.apply(True) is True
        plist = launch_dir / "com.cortex.agent.plist"
        assert plist.exists()
        assert run and "load" in run[0][0]


def test_apply_disable_removes_plist(monkeypatch, tmp_path):
    launch_dir = tmp_path / "LaunchAgents"
    launch_dir.mkdir(parents=True)
    plist = launch_dir / "com.cortex.agent.plist"
    plist.write_text("{}")
    with patch.object(autostart.sys, "platform", "darwin"):
        monkeypatch.setattr(autostart.os.path, "expanduser", lambda p: str(launch_dir))
        monkeypatch.setattr(autostart.subprocess, "run", lambda *a, **k: None)
        assert autostart.apply(False) is True
        assert not plist.exists()


def test_is_enabled():
    assert autostart.is_enabled() in (True, False)


def test_launcher_script_and_build_plist():
    script = autostart._launcher_script()
    assert script.endswith("autostart_launcher.py")
    plist = autostart._build_plist()
    assert plist["Label"] == "com.cortex.agent"
    assert plist["RunAtLoad"] is True
