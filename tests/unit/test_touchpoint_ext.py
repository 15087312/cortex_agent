"""perception/detectors/touchpoint_detector 测试：macOS 应用路径查找 + Electron 检测"""
import sys
from unittest.mock import MagicMock

from modules.perception.detectors import touchpoint_detector as td
from modules.perception.detectors.touchpoint_detector import TouchpointDetector


class _RunResult:
    def __init__(self, rc=0, stdout=""):
        self.returncode = rc
        self.stdout = stdout


def _fake_touchpoint(windows):
    class Tp:
        @staticmethod
        def windows():
            return windows

    return Tp()


def _window(app, pid):
    w = MagicMock()
    w.app = app
    w.pid = pid
    return w


def test_find_app_path_via_lsappinfo(monkeypatch):
    """方法1：touchpoint 找到窗口 → lsappinfo 拿 bundle path"""
    def _run(args, **kw):
        assert args[0] == "lsappinfo"
        return _RunResult(0, '    "bundle path" = "/Applications/TestApp.app"\n')

    monkeypatch.setattr("subprocess.run", _run)
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_touchpoint([_window("TestApp", 123)]))
    monkeypatch.setattr("os.path.exists", lambda p: p == "/Applications/TestApp.app")
    assert TouchpointDetector._find_app_path("TestApp") == "/Applications/TestApp.app"


def test_find_app_path_lsappinfo_fail_falls_to_mdfind(monkeypatch):
    """lsappinfo 非 0 退出 → 落到 mdfind"""
    calls = []

    def _run(args, **kw):
        calls.append(args[0])
        if args[0] == "lsappinfo":
            return _RunResult(1, "")
        return _RunResult(0, "/Applications/TextEdit.app\n")

    monkeypatch.setattr("subprocess.run", _run)
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_touchpoint([_window("TextEdit", 1)]))
    monkeypatch.setattr("os.path.isdir", lambda p: True)
    monkeypatch.setattr("plistlib.load", lambda f: {"CFBundleDisplayName": "TextEdit"})
    monkeypatch.setattr("builtins.open", lambda *a, **k: MagicMock())
    assert TouchpointDetector._find_app_path("TextEdit") == "/Applications/TextEdit.app"
    assert calls == ["lsappinfo", "mdfind"]


def test_find_app_path_mdfind_name_fallback(monkeypatch):
    """plist 无 CFBundleDisplayName 时回退 CFBundleName 匹配路径"""
    def _run(args, **kw):
        return _RunResult(0, "/Applications/Chrome.app\n")

    monkeypatch.setattr("subprocess.run", _run)
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_touchpoint([]))
    monkeypatch.setattr("os.path.isdir", lambda p: True)
    monkeypatch.setattr("plistlib.load", lambda f: {"CFBundleName": "Chrome"})
    monkeypatch.setattr("builtins.open", lambda *a, **k: MagicMock())
    assert TouchpointDetector._find_app_path("Chrome") == "/Applications/Chrome.app"


def test_find_app_path_touchpoint_import_error(monkeypatch):
    """touchpoint import 失败 → 直接走 mdfind"""
    def _run(args, **kw):
        return _RunResult(0, "")

    monkeypatch.setattr("subprocess.run", _run)
    monkeypatch.setitem(sys.modules, "touchpoint", None)
    monkeypatch.setattr("os.path.isdir", lambda p: False)
    monkeypatch.setattr("os.path.expanduser", lambda p: "/fake/Applications")
    assert TouchpointDetector._find_app_path("NopeApp") is None


def test_find_app_path_mdfind_raises_falls_to_apps_scan(monkeypatch):
    """mdfind 抛异常 → 落到 /Applications 扫描"""
    calls = []

    def _run(args, **kw):
        calls.append(args[0])
        if args[0] == "mdfind":
            raise OSError("mdfind missing")
        return _RunResult(1, "")

    monkeypatch.setattr("subprocess.run", _run)
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_touchpoint([]))
    monkeypatch.setattr("os.path.isdir", lambda p: True)
    monkeypatch.setattr("os.listdir", lambda d: ["Chrome.app", "README.txt"])
    monkeypatch.setattr("os.path.expanduser", lambda p: "/fake/Applications")
    assert TouchpointDetector._find_app_path("chrome") == "/Applications/Chrome.app"
    assert calls == ["mdfind"]


def test_find_app_path_apps_scan_permission_denied(monkeypatch):
    """/Applications 扫描遇 PermissionError → 继续下一个根目录 → None"""
    def _run(args, **kw):
        return _RunResult(0, "")

    monkeypatch.setattr("subprocess.run", _run)
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_touchpoint([]))
    isdir = {"dirs": []}
    isdir["dirs"] = ["/Applications", "/fake/Applications"]

    def _isdir(p):
        return p in isdir["dirs"]

    monkeypatch.setattr("os.path.isdir", _isdir)

    def _listdir(d):
        raise PermissionError(d)

    monkeypatch.setattr("os.listdir", _listdir)
    monkeypatch.setattr("os.path.expanduser", lambda p: "/fake/Applications")
    assert TouchpointDetector._find_app_path("X") is None


def test_find_app_path_lsappinfo_raises(monkeypatch):
    """lsappinfo subprocess 抛异常 → 落入 mdfind 再落入扫描 → None"""
    def _run(args, **kw):
        if args[0] == "lsappinfo":
            raise OSError("lsappinfo timeout")
        return _RunResult(0, "")

    monkeypatch.setattr("subprocess.run", _run)
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_touchpoint([_window("A", 1)]))
    monkeypatch.setattr("os.path.isdir", lambda p: False)
    monkeypatch.setattr("os.path.expanduser", lambda p: "/fake/Applications")
    assert TouchpointDetector._find_app_path("A") is None


def test_find_app_path_not_mac(monkeypatch):
    """非 mac 平台直接返回 None，不触碰 subprocess"""
    monkeypatch.setattr(td, "_IS_MAC", False)
    assert TouchpointDetector._find_app_path("AnyApp") is None


def test_is_electron_app_no_frameworks(monkeypatch):
    monkeypatch.setattr("os.path.isdir", lambda p: False)
    assert TouchpointDetector._is_electron_app("/fake/App.app") is False


def test_is_electron_app_renderer_helper(monkeypatch):
    monkeypatch.setattr("os.path.isdir", lambda p: True)
    monkeypatch.setattr("os.listdir", lambda d: ["App Helper (Renderer).app", "App.app"])
    assert TouchpointDetector._is_electron_app("/fake/App.app") is True


def test_is_electron_app_helper_suffix(monkeypatch):
    monkeypatch.setattr("os.path.isdir", lambda p: True)
    monkeypatch.setattr("os.listdir", lambda d: ["App Helper.app"])
    assert TouchpointDetector._is_electron_app("/fake/App.app") is True


def test_is_electron_app_no_match(monkeypatch):
    monkeypatch.setattr("os.path.isdir", lambda p: True)
    monkeypatch.setattr("os.listdir", lambda d: ["App.app", "Libraries"])
    assert TouchpointDetector._is_electron_app("/fake/App.app") is False


def test_is_electron_app_permission_denied(monkeypatch):
    monkeypatch.setattr("os.path.isdir", lambda p: True)

    def _listdir(d):
        raise PermissionError(d)

    monkeypatch.setattr("os.listdir", _listdir)
    assert TouchpointDetector._is_electron_app("/fake/App.app") is False


def test_find_app_path_window_app_mismatch_and_pid_zero(monkeypatch):
    """窗口 app 不匹配或 pid=0 → 跳过 lsappinfo，落入 mdfind"""
    def _run(args, **kw):
        return _RunResult(0, "")

    monkeypatch.setattr("subprocess.run", _run)
    monkeypatch.setitem(sys.modules, "touchpoint",
                        _fake_touchpoint([_window("OtherApp", 5), _window("X", 0)]))
    monkeypatch.setattr("os.path.isdir", lambda p: False)
    monkeypatch.setattr("os.path.expanduser", lambda p: "/fake/Applications")
    assert TouchpointDetector._find_app_path("X") is None


def test_find_app_path_lsappinfo_no_bundle_line(monkeypatch):
    """lsappinfo rc=0 但 stdout 无 bundle path 行 → 继续"""
    def _run(args, **kw):
        if args[0] == "lsappinfo":
            return _RunResult(0, "some key = value\n")
        return _RunResult(0, "")

    monkeypatch.setattr("subprocess.run", _run)
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_touchpoint([_window("X", 1)]))
    monkeypatch.setattr("os.path.isdir", lambda p: False)
    monkeypatch.setattr("os.path.expanduser", lambda p: "/fake/Applications")
    assert TouchpointDetector._find_app_path("X") is None


def test_find_app_path_bundle_path_missing_on_disk(monkeypatch):
    """bundle path 行存在但路径不存在 → 继续"""
    def _run(args, **kw):
        if args[0] == "lsappinfo":
            return _RunResult(0, '    "bundle path" = "/Applications/Missing.app"\n')
        return _RunResult(0, "")

    monkeypatch.setattr("subprocess.run", _run)
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_touchpoint([_window("X", 1)]))
    monkeypatch.setattr("os.path.exists", lambda p: False)
    monkeypatch.setattr("os.path.isdir", lambda p: False)
    monkeypatch.setattr("os.path.expanduser", lambda p: "/fake/Applications")
    assert TouchpointDetector._find_app_path("X") is None


def test_find_app_path_mdfind_non_app_line(monkeypatch):
    """mdfind 输出行不以 .app 结尾 → 跳过"""
    def _run(args, **kw):
        return _RunResult(0, "/Applications/notes.txt\n")

    monkeypatch.setattr("subprocess.run", _run)
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_touchpoint([]))
    monkeypatch.setattr("os.path.isdir", lambda p: False)
    monkeypatch.setattr("os.path.expanduser", lambda p: "/fake/Applications")
    assert TouchpointDetector._find_app_path("X") is None


def test_find_app_path_mdfind_name_mismatch(monkeypatch):
    """mdfind 命中 .app 但 plist 名称与路径都不匹配 → 继续"""
    def _run(args, **kw):
        return _RunResult(0, "/Applications/TextEdit.app\n")

    monkeypatch.setattr("subprocess.run", _run)
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_touchpoint([]))
    monkeypatch.setattr("os.path.isdir", lambda p: True)
    monkeypatch.setattr("plistlib.load", lambda f: {"CFBundleDisplayName": "TextEdit"})
    monkeypatch.setattr("builtins.open", lambda *a, **k: MagicMock())
    monkeypatch.setattr("os.listdir", lambda d: [])
    monkeypatch.setattr("os.path.expanduser", lambda p: "/fake/Applications")
    assert TouchpointDetector._find_app_path("FooApp") is None


def test_find_app_path_mdfind_plist_error(monkeypatch):
    """mdfind 命中 .app 但 Info.plist 读取失败 → 捕获继续"""
    def _run(args, **kw):
        return _RunResult(0, "/Applications/TextEdit.app\n")

    def _open(*a, **k):
        raise OSError("denied")

    monkeypatch.setattr("subprocess.run", _run)
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_touchpoint([]))
    monkeypatch.setattr("os.path.isdir", lambda p: True)
    monkeypatch.setattr("builtins.open", _open)
    monkeypatch.setattr("os.listdir", lambda d: [])
    monkeypatch.setattr("os.path.expanduser", lambda p: "/fake/Applications")
    assert TouchpointDetector._find_app_path("X") is None


def test_find_app_path_apps_scan_no_match(monkeypatch):
    """/Applications 扫描：空目录、非 .app、名称不匹配均不命中"""
    def _run(args, **kw):
        return _RunResult(0, "")

    monkeypatch.setattr("subprocess.run", _run)
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_touchpoint([]))
    monkeypatch.setattr(
        "os.path.isdir",
        lambda p: p in ("/Applications", "/fake/Applications") or p.endswith(".app"),
    )
    monkeypatch.setattr(
        "os.listdir",
        lambda d: [] if d == "/Applications" else ["Bar.app", "readme.txt"],
    )
    monkeypatch.setattr("os.path.expanduser", lambda p: "/fake/Applications")
    assert TouchpointDetector._find_app_path("FooApp") is None
