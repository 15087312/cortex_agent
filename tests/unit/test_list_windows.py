"""list_windows 测试（此前 10% 覆盖）：平台分支/Electron 检测/窗口列表/降级路径

macOS 分支通过 touchpoint + ps；win32 通过 wmic；均 100% mock subprocess，
不真实调用系统进程。
"""
import sys
import types

import pytest

from infra.tool_manager.service_registry import register_capability
from infra.tool_manager.tools import list_windows as lw


@pytest.fixture(autouse=True)
def _clear_electron_cache():
    lw._electron_cache.clear()
    yield
    lw._electron_cache.clear()


class _Win:
    def __init__(self, app, title="", is_active=False, pid=0):
        self.app = app
        self.title = title
        self.is_active = is_active
        self.pid = pid


def _fake_tp(windows):
    tp = types.ModuleType("touchpoint")
    tp.windows = lambda: windows
    return tp


def _register_fake_detector(app_path="/Applications/WeChat.app", is_elec=True):
    class _Det:
        @staticmethod
        def _find_app_path(app_name):
            return app_path

        @staticmethod
        def _is_electron_app(path):
            return is_elec

    register_capability("touchpoint_detector", lambda: _Det)


# ── _is_electron：缓存 ──────────────────────────────────────────────────────

def test_is_electron_cache_hit(monkeypatch):
    lw._electron_cache["X"] = True
    monkeypatch.setattr(sys, "platform", "linux")  # 缓存命中不应进入任何平台分支
    assert lw._is_electron("X") is True


# ── _is_electron：macOS + touchpoint + ps ──────────────────────────────────

def test_is_electron_darwin_ps_electron(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_tp([_Win("WeChat", pid=100)]))
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: types.SimpleNamespace(stdout="...electron helper..."),
    )
    assert lw._is_electron("WeChat") is True
    assert lw._electron_cache["WeChat"] is True


def test_is_electron_darwin_ps_cef(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_tp([_Win("网易云", pid=200)]))
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: types.SimpleNamespace(stdout=".../cef/..."),
    )
    assert lw._is_electron("网易云") is True


def test_is_electron_darwin_ps_native(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_tp([_Win("Safari", pid=300)]))
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: types.SimpleNamespace(stdout=".../Safari.app/Contents/MacOS/Safari"),
    )
    assert lw._is_electron("Safari") is False
    assert lw._electron_cache["Safari"] is False


# ── _is_electron：macOS 降级到 capability ───────────────────────────────────

def test_is_electron_darwin_ps_exception_then_capability(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_tp([_Win("WeChat", pid=123)]))

    def boom(*a, **k):
        raise OSError("ps failed")

    monkeypatch.setattr("subprocess.run", boom)
    _register_fake_detector(app_path="/Applications/WeChat.app", is_elec=True)
    assert lw._is_electron("WeChat") is True


def test_is_electron_darwin_no_pid_then_capability(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_tp([_Win("WeChat", pid=0)]))
    _register_fake_detector(app_path="/Applications/WeChat.app", is_elec=False)
    assert lw._is_electron("WeChat") is False


def test_is_electron_darwin_no_match_then_capability(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_tp([_Win("Safari", pid=1)]))
    _register_fake_detector(app_path="/Applications/WeChat.app", is_elec=True)
    assert lw._is_electron("WeChat") is True  # 无匹配窗口 → 走 capability 降级


def test_is_electron_darwin_touchpoint_import_error(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "touchpoint", None)  # import 抛 ImportError
    _register_fake_detector(app_path="/Applications/WeChat.app", is_elec=True)
    assert lw._is_electron("WeChat") is True


def test_is_electron_darwin_capability_none(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_tp([_Win("X", pid=0)]))
    monkeypatch.setattr(lw, "get_capability", lambda name: None)  # factory 为 None
    assert lw._is_electron("X") is False


def test_is_electron_darwin_capability_no_path(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_tp([_Win("X", pid=0)]))
    _register_fake_detector(app_path="", is_elec=True)  # 找不到 .app 路径
    assert lw._is_electron("X") is False


def test_is_electron_darwin_capability_exception(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_tp([_Win("X", pid=0)]))

    def bad(name):
        raise RuntimeError("capability boom")

    monkeypatch.setattr(lw, "get_capability", bad)
    assert lw._is_electron("X") is False


# ── _is_electron：win32 ─────────────────────────────────────────────────────

def test_is_electron_win32_electron(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: types.SimpleNamespace(stdout="c:\\program files\\electron\\app.exe"),
    )
    assert lw._is_electron("SomeApp") is True


def test_is_electron_win32_native(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: types.SimpleNamespace(stdout="c:\\windows\\notepad.exe"),
    )
    assert lw._is_electron("Notepad") is False
    assert lw._electron_cache["Notepad"] is False


def test_is_electron_win32_exception(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    def boom(*a, **k):
        raise OSError("wmic failed")

    monkeypatch.setattr("subprocess.run", boom)
    assert lw._is_electron("X") is False


# ── _is_electron：其他平台 ──────────────────────────────────────────────────

def test_is_electron_other_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert lw._is_electron("X") is False
    assert lw._electron_cache["X"] is False


# ── list_windows：降级/空/错误 ──────────────────────────────────────────────

def test_list_windows_touchpoint_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "touchpoint", None)
    r = lw.list_windows()
    assert r["success"] is False
    assert "touchpoint 未安装" in r["error"]


def test_list_windows_windows_raises(monkeypatch):
    tp = types.ModuleType("touchpoint")

    def boom():
        raise RuntimeError("tp error")

    tp.windows = boom
    monkeypatch.setitem(sys.modules, "touchpoint", tp)
    r = lw.list_windows()
    assert r["success"] is False
    assert "获取窗口列表失败" in r["error"]


def test_list_windows_empty(monkeypatch):
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_tp([]))
    r = lw.list_windows()
    assert r["success"] is True
    assert r["windows"] == []
    assert "未检测到任何窗口" in r["message"]


# ── list_windows：正常汇总 ──────────────────────────────────────────────────

def test_list_windows_basic(monkeypatch):
    tp = _fake_tp([
        _Win("Finder", title="桌面", is_active=False),
        _Win("Safari", title="我的页面", is_active=True),
        _Win("WeChat", title="微信", is_active=False),
    ])
    monkeypatch.setitem(sys.modules, "touchpoint", tp)
    monkeypatch.setattr(lw, "_is_electron", lambda app: app == "WeChat")

    r = lw.list_windows()
    assert r["success"] is True
    assert r["count"] == 3
    assert r["windows"][0]["app"] == "Safari"  # 活跃窗口排最前
    assert r["windows"][0]["active"] is True
    wechat = [w for w in r["windows"] if w["app"] == "WeChat"][0]
    assert wechat["type"] == "electron"
    assert "WeChat" in r["hint"]
    finder = [w for w in r["windows"] if w["app"] == "Finder"][0]
    assert finder["type"] == "native"


def test_list_windows_dedup(monkeypatch):
    tp = _fake_tp([
        _Win("Safari", title="窗口A"),
        _Win("Safari", title="窗口B"),
    ])
    monkeypatch.setitem(sys.modules, "touchpoint", tp)
    monkeypatch.setattr(lw, "_is_electron", lambda app: False)

    r = lw.list_windows()
    assert r["count"] == 1  # 同 app 去重
    assert r["windows"][0]["title"] == "窗口A"


def test_list_windows_only_active(monkeypatch):
    tp = _fake_tp([
        _Win("Safari", title="前台", is_active=True),
        _Win("Finder", title="后台", is_active=False),
    ])
    monkeypatch.setitem(sys.modules, "touchpoint", tp)
    monkeypatch.setattr(lw, "_is_electron", lambda app: False)

    r = lw.list_windows(only_active=True)
    assert r["count"] == 1
    assert r["windows"][0]["app"] == "Safari"


def test_list_windows_missing_attrs(monkeypatch):
    w = types.SimpleNamespace()  # 无 app/title/is_active 属性
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_tp([w]))
    monkeypatch.setattr(lw, "_is_electron", lambda app: False)

    r = lw.list_windows()
    assert r["windows"][0]["app"] == "未知"
    assert r["windows"][0]["title"] == ""
    assert r["windows"][0]["active"] is False


def test_list_windows_no_electron_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_tp([_Win("Safari", title="t")]))
    monkeypatch.setattr(lw, "_is_electron", lambda app: False)

    r = lw.list_windows()
    assert r["hint"].startswith("使用 detect_ui_elements")
    assert "Electron 应用" not in r["hint"]


def test_list_windows_real_electron_detector(monkeypatch):
    """list_windows 内真实调用 _is_electron（不 monkeypatch），验证集成路径"""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "touchpoint", _fake_tp([_Win("Safari", pid=0)]))
    monkeypatch.setattr(lw, "get_capability", lambda name: None)

    r = lw.list_windows()
    assert r["success"] is True
    assert r["windows"][0]["type"] == "native"
