"""open_app 补充测试：win32 / linux / 异常分支 / CDP 配置"""
import sys
import types
from unittest.mock import patch

import pytest

from infra.tool_manager.tools import open_app as oa


class _Win:
    def __init__(self, app):
        self.app = app


def _install_touchpoint(monkeypatch, windows=None, configure=None):
    tp = types.ModuleType("touchpoint")
    tp.windows = windows or (lambda: [])
    tp.activate_window = lambda w: None
    if configure is not None:
        tp.configure = configure
    monkeypatch.setitem(sys.modules, "touchpoint", tp)
    return tp


class _Detector:
    @staticmethod
    def _find_app_path(name):
        return "/Applications/Safari.app"


def _register_detector(monkeypatch):
    monkeypatch.setattr(oa, "get_capability",
                        lambda name: (lambda: _Detector) if name == "touchpoint_detector" else None)


def test_touchpoint_no_matching_window(monkeypatch):
    # 窗口存在但名称不匹配 → 跳过激活，落入后续流程
    tp = types.ModuleType("touchpoint")
    tp.windows = lambda: [_Win("Slack")]
    tp.activate_window = lambda w: None
    monkeypatch.setitem(sys.modules, "touchpoint", tp)
    monkeypatch.setattr(oa, "get_capability", lambda n: None)  # factory 为 None 分支
    monkeypatch.setattr(sys, "platform", "linux")
    with patch("subprocess.run") as m:
        m.return_value = types.SimpleNamespace(returncode=1)
        r = oa.open_app("Safari")
    assert r["success"] is False


def test_touchpoint_windows_empty_app_getattr():
    class _WinNone:
        pass
    tp = types.ModuleType("touchpoint")
    tp.windows = lambda: [_WinNone()]
    tp.activate_window = lambda w: None
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setitem(sys.modules, "touchpoint", tp)
    try:
        # w_app 缺省用 ""（getattr 回退），空串是任意字符串子串 → 命中激活分支
        r = oa.open_app("Safari")
        assert r["success"] is True
        assert r["action"] == "activated"
    finally:
        monkeypatch.undo()


def test_touchpoint_raises_is_silent(monkeypatch):
    def boom():
        raise RuntimeError("touchpoint broken")
    tp = types.ModuleType("touchpoint")
    tp.windows = boom
    monkeypatch.setitem(sys.modules, "touchpoint", tp)
    monkeypatch.setattr(sys, "platform", "linux")
    with patch("subprocess.run") as m:
        m.return_value = types.SimpleNamespace(returncode=1)
        r = oa.open_app("AppX")
    assert r["success"] is False


def test_detector_factory_raises_is_silent(monkeypatch):
    tp = types.ModuleType("touchpoint")
    tp.windows = lambda: []
    monkeypatch.setitem(sys.modules, "touchpoint", tp)
    def boom():
        raise RuntimeError("factory broken")
    monkeypatch.setattr(oa, "get_capability", lambda n: boom if n == "touchpoint_detector" else None)
    monkeypatch.setattr(sys, "platform", "linux")
    with patch("subprocess.run") as m:
        m.return_value = types.SimpleNamespace(returncode=1)
        r = oa.open_app("AppX")
    assert r["success"] is False


def test_win32_with_path(monkeypatch):
    _install_touchpoint(monkeypatch)
    monkeypatch.setattr(oa, "get_capability",
                        lambda n: (lambda: _Detector) if n == "touchpoint_detector" else None)
    monkeypatch.setattr(sys, "platform", "win32")
    startfile = pytest.MonkeyPatch()
    startfile.setattr(oa.os, "startfile", lambda p: None, raising=False)
    try:
        r = oa.open_app("AppX")
    finally:
        startfile.undo()
    assert r["success"] is True


def test_win32_without_path(monkeypatch):
    _install_touchpoint(monkeypatch)
    monkeypatch.setattr(oa, "get_capability",
                        lambda n: (lambda: type("_D", (), {"_find_app_path": lambda _n: ""})) if n == "touchpoint_detector" else None)
    monkeypatch.setattr(sys, "platform", "win32")
    with patch("subprocess.run") as m:
        r = oa.open_app("AppX")
    assert r["success"] is True
    m.assert_called_once()
    assert m.call_args[0][0] == ["start", "AppX"]


def test_darwin_path_then_open_a_fallback(monkeypatch):
    _install_touchpoint(monkeypatch)
    monkeypatch.setattr(oa, "get_capability",
                        lambda n: (lambda: _Detector) if n == "touchpoint_detector" else None)
    monkeypatch.setattr(sys, "platform", "darwin")
    with patch("subprocess.run") as m:
        m.side_effect = [
            types.SimpleNamespace(returncode=1, stderr="fail"),
            types.SimpleNamespace(returncode=0, stderr=""),
        ]
        r = oa.open_app("Safari")
    assert r["success"] is True
    assert m.call_count == 2


def test_linux_xdg_success(monkeypatch):
    _install_touchpoint(monkeypatch)
    monkeypatch.setattr(oa, "get_capability",
                        lambda n: (lambda: _Detector) if n == "touchpoint_detector" else None)
    monkeypatch.setattr(sys, "platform", "linux")
    with patch("subprocess.run") as m:
        m.return_value = types.SimpleNamespace(returncode=0)
        r = oa.open_app("Safari")
    assert r["success"] is True
    assert m.call_args[0][0][0] == "xdg-open"


def test_linux_xdg_not_found(monkeypatch):
    _install_touchpoint(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    import subprocess as sp
    with patch("subprocess.run", side_effect=FileNotFoundError("no xdg-open")):
        r = oa.open_app("AppX")
    assert r["success"] is False
    assert "当前系统不支持自动打开应用" in r["error"]


def test_outer_file_not_found(monkeypatch):
    _install_touchpoint(monkeypatch)
    monkeypatch.setattr(sys, "platform", "darwin")
    import subprocess as sp
    with patch("subprocess.run", side_effect=FileNotFoundError("no open")):
        r = oa.open_app("AppX")
    assert r["success"] is False
    assert "系统不支持打开命令" in r["error"]


def test_cdp_configure_for_chrome(monkeypatch):
    calls = []
    _install_touchpoint(monkeypatch, configure=lambda **k: calls.append(k))
    monkeypatch.setattr(sys, "platform", "darwin")
    with patch("subprocess.run") as m:
        m.return_value = types.SimpleNamespace(returncode=1, stderr="no")
        r = oa.open_app("Chrome")
    assert r["success"] is False
    assert "Chrome" in r["error"]
    assert calls == [{"cdp_discover": True}]


def test_cdp_configure_raises_is_silent(monkeypatch):
    def boom(**k):
        raise RuntimeError("cdp broken")
    _install_touchpoint(monkeypatch, configure=boom)
    monkeypatch.setattr(sys, "platform", "darwin")
    with patch("subprocess.run") as m:
        m.return_value = types.SimpleNamespace(returncode=1, stderr="no")
        r = oa.open_app("Chrome")
    assert r["success"] is False
    assert "无法打开" in r["error"]


def test_win32_subprocess_timeout(monkeypatch):
    _install_touchpoint(monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")
    import subprocess as sp
    with patch("subprocess.run", side_effect=sp.TimeoutExpired("start", 15)):
        r = oa.open_app("AppX")
    assert "超时" in r["error"]


def test_linux_hint_fallback(monkeypatch):
    _install_touchpoint(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    with patch("subprocess.run") as m:
        m.return_value = types.SimpleNamespace(returncode=1)
        r = oa.open_app("AppX")
    assert r["success"] is False
    assert "xdg-open AppX" in r["error"]


def test_whitespace_name():
    r = oa.open_app("   ")
    assert r["success"] is False
    assert "app_name 不能为空" in r["error"]
