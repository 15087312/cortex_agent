"""open_app 测试（此前 8% 覆盖）：触摸点切换/subprocess 启动/失败提示"""
import sys
from unittest.mock import patch

from infra.tool_manager.tools import open_app as oa


class _Win:
    def __init__(self, app):
        self.app = app


def test_empty_name():
    r = oa.open_app("")
    assert r["success"] is False
    assert "不能为空" in r["error"]


def test_touchpoint_activated(monkeypatch):
    import types
    tp = types.ModuleType("touchpoint")
    tp.windows = lambda: [_Win("Safari")]
    tp.activate_window = lambda w: None
    monkeypatch.setitem(sys.modules, "touchpoint", tp)
    r = oa.open_app("safari")
    assert r["success"] is True
    assert r["action"] == "activated"


def test_activate_only_not_running(monkeypatch):
    import types
    tp = types.ModuleType("touchpoint")
    tp.windows = lambda: []
    monkeypatch.setitem(sys.modules, "touchpoint", tp)
    monkeypatch.setattr("modules.perception.detectors.touchpoint_detector.TouchpointDetector._find_app_path", lambda n: "")
    r = oa.open_app("NotRunning", activate_only=True)
    assert r["success"] is False
    assert "activate_only" in r["error"]


def test_darwin_open_path_success(monkeypatch):
    import types
    tp = types.ModuleType("touchpoint")
    tp.windows = lambda: []
    monkeypatch.setitem(sys.modules, "touchpoint", tp)
    monkeypatch.setattr("modules.perception.detectors.touchpoint_detector.TouchpointDetector._find_app_path", lambda n: "/Applications/Safari.app")
    monkeypatch.setattr(sys, "platform", "darwin")
    with patch("subprocess.run") as m:
        m.return_value = __import__("types").SimpleNamespace(returncode=0, stderr="")
        r = oa.open_app("Safari")
    assert r["success"] is True
    assert r["action"] == "launched"


def test_darwin_open_a_fallback(monkeypatch):
    import types
    tp = types.ModuleType("touchpoint")
    tp.windows = lambda: []
    monkeypatch.setitem(sys.modules, "touchpoint", tp)
    monkeypatch.setattr("modules.perception.detectors.touchpoint_detector.TouchpointDetector._find_app_path", lambda n: "")
    monkeypatch.setattr(sys, "platform", "darwin")
    with patch("subprocess.run") as m:
        # app_path 为空 → 只走一次 open -a
        m.return_value = types.SimpleNamespace(returncode=0, stderr="")
        r = oa.open_app("Terminal")
    assert r["success"] is True


def test_darwin_fail_hint(monkeypatch):
    import types
    tp = types.ModuleType("touchpoint")
    tp.windows = lambda: []
    monkeypatch.setitem(sys.modules, "touchpoint", tp)
    monkeypatch.setattr("modules.perception.detectors.touchpoint_detector.TouchpointDetector._find_app_path", lambda n: "")
    monkeypatch.setattr(sys, "platform", "darwin")
    with patch("subprocess.run") as m:
        m.return_value = types.SimpleNamespace(returncode=1, stderr="no")
        r = oa.open_app("UnknownApp")
    assert r["success"] is False
    assert "open /Applications/UnknownApp.app" in r["error"]


def test_timeout(monkeypatch):
    import types
    tp = types.ModuleType("touchpoint")
    tp.windows = lambda: []
    monkeypatch.setitem(sys.modules, "touchpoint", tp)
    monkeypatch.setattr("modules.perception.detectors.touchpoint_detector.TouchpointDetector._find_app_path", lambda n: "")
    monkeypatch.setattr(sys, "platform", "darwin")
    import subprocess as sp
    with patch("subprocess.run", side_effect=sp.TimeoutExpired("open", 15)):
        r = oa.open_app("Slow")
    assert "超时" in r["error"]
