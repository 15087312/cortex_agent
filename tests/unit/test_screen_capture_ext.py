"""utils/screen_capture 补充测试 — 权限检测/daemon 保活/截图回退/图像处理边界

不真实截图：mock 系统边界（subprocess screencapture / Quartz / daemon 客户端），
用 PIL 内存生成假图。
"""
import base64
import io
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import utils.screen_capture as sc


@pytest.fixture(autouse=True)
def _reset_globals(monkeypatch):
    old_enabled = sc.SCREENSHOT_ENABLED
    old_proc = sc._DAEMON_PROC
    old_checked = sc._daemon_checked
    sc.SCREENSHOT_ENABLED = True
    sc._DAEMON_PROC = None
    sc._daemon_checked = False
    # init_screen_permission 里授权后会自动拉起 daemon，测试默认打桩防止真实 Popen
    monkeypatch.setattr(sc, "_ensure_daemon_running", lambda: None)
    yield
    sc.SCREENSHOT_ENABLED = old_enabled
    sc._DAEMON_PROC = old_proc
    sc._daemon_checked = old_checked


def _png(size=(8, 6)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color=(10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _img(size=(10, 8)):
    from PIL import Image
    return Image.new("RGB", size)


# ── init_screen_permission ────────────────────────────────────────────────

def test_init_permission_non_darwin(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "win32")
    sc.init_screen_permission()
    assert sc.SCREENSHOT_ENABLED is True


def test_init_permission_quartz_granted(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "darwin")
    called = []

    class FakeQuartz:
        CGPreflightScreenCaptureAccess = staticmethod(lambda: True)

    monkeypatch.setitem(sys.modules, "Quartz", FakeQuartz())
    monkeypatch.setattr(sc, "_ensure_daemon_running", lambda: called.append(1))
    sc.init_screen_permission()
    assert sc.SCREENSHOT_ENABLED is True
    assert called == [1]  # 已授权 → 拉起 daemon


def test_init_permission_quartz_denied(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "darwin")

    class FakeQuartz:
        CGPreflightScreenCaptureAccess = staticmethod(lambda: False)

    monkeypatch.setitem(sys.modules, "Quartz", FakeQuartz())
    sc.init_screen_permission()
    assert sc.SCREENSHOT_ENABLED is False


def test_init_permission_fallback_success(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "Quartz", None)  # 强制 import 失败

    def fake_run(cmd, timeout=3, capture_output=True):
        with open(cmd[-1], "wb") as f:
            f.write(_png())
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sc, "_ensure_daemon_running", lambda: None)
    sc.init_screen_permission()
    assert sc.SCREENSHOT_ENABLED is True


def test_init_permission_fallback_nonzero(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "Quartz", None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    sc.init_screen_permission()
    assert sc.SCREENSHOT_ENABLED is False


def test_init_permission_fallback_empty_file(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "Quartz", None)

    def fake_run(cmd, timeout=3, capture_output=True):
        return SimpleNamespace(returncode=0)  # 不写文件 → size 0

    monkeypatch.setattr(subprocess, "run", fake_run)
    sc.init_screen_permission()
    assert sc.SCREENSHOT_ENABLED is False


def test_init_permission_fallback_exception(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "Quartz", None)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("screencapture", 3)),
    )
    sc.init_screen_permission()
    assert sc.SCREENSHOT_ENABLED is False


def test_init_permission_unlink_oserror_swallowed(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "Quartz", None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(sc.os.path, "getsize", lambda p: 1)
    monkeypatch.setattr(sc.os, "unlink", lambda p: (_ for _ in ()).throw(OSError("no perm")))
    sc.init_screen_permission()  # 不抛异常
    assert sc.SCREENSHOT_ENABLED is True  # returncode==0 且 size>0 → 授权成功


# ── ensure_daemon_running ─────────────────────────────────────────────────

def test_ensure_daemon_non_mac(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "win32")
    assert sc.ensure_daemon_running() is False


def test_ensure_daemon_already_running(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "darwin")
    proc = MagicMock()
    proc.poll.return_value = None
    sc._DAEMON_PROC = proc
    assert sc.ensure_daemon_running() is True
    sc._DAEMON_PROC = None


def test_ensure_daemon_launches(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "darwin")
    proc = MagicMock()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(sc.os.path, "exists", lambda p: True)
    assert sc.ensure_daemon_running() is True
    assert sc._daemon_checked is True
    sc._DAEMON_PROC = None


def test_ensure_daemon_path_missing(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "darwin")
    monkeypatch.setattr(sc.os.path, "exists", lambda p: False)
    assert sc.ensure_daemon_running() is False


def test_ensure_daemon_popen_error(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("no fork")))
    monkeypatch.setattr(sc.os.path, "exists", lambda p: True)
    assert sc.ensure_daemon_running() is False
    assert sc._DAEMON_PROC is None


def test_ensure_daemon_respawns_when_dead(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "darwin")
    dead = MagicMock()
    dead.poll.return_value = 1  # 已退出
    sc._DAEMON_PROC = dead
    proc = MagicMock()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(sc.os.path, "exists", lambda p: True)
    assert sc.ensure_daemon_running() is True
    sc._DAEMON_PROC = None


# ── capture_screen_bytes ──────────────────────────────────────────────────

def test_capture_disabled(monkeypatch):
    monkeypatch.setattr(sc, "SCREENSHOT_ENABLED", False)
    assert sc.capture_screen_bytes() is None


def test_capture_via_daemon(monkeypatch):
    import utils.screen_capture_daemon_client as client_mod
    monkeypatch.setattr(client_mod, "get_frame_bytes", lambda **kw: b"daemon-png")
    assert sc.capture_screen_bytes(max_width=640) == b"daemon-png"


def test_capture_daemon_retry_then_local(monkeypatch):
    import utils.screen_capture_daemon_client as client_mod
    calls = {"n": 0}

    def get_frame(max_width=1280, region=None):
        calls["n"] += 1
        return None  # 两次都失败

    monkeypatch.setattr(client_mod, "get_frame_bytes", get_frame)
    monkeypatch.setattr(sc, "ensure_daemon_running", lambda: False)
    monkeypatch.setattr(sc, "_grab_image", lambda *a, **k: _img((10, 5)))
    out = sc.capture_screen_bytes()
    assert out.startswith(b"\x89PNG")
    assert calls["n"] == 2


def test_capture_daemon_then_local_success(monkeypatch):
    import utils.screen_capture_daemon_client as client_mod
    calls = {"n": 0}

    def get_frame(max_width=1280, region=None):
        calls["n"] += 1
        return b"ok" if calls["n"] > 1 else None

    monkeypatch.setattr(client_mod, "get_frame_bytes", get_frame)
    monkeypatch.setattr(sc, "ensure_daemon_running", lambda: True)
    monkeypatch.setattr(sc, "_grab_image", lambda *a, **k: pytest.fail("不应本地截图"))
    assert sc.capture_screen_bytes() == b"ok"


def test_capture_all_channels_fail(monkeypatch):
    import utils.screen_capture_daemon_client as client_mod
    monkeypatch.setattr(client_mod, "get_frame_bytes", lambda **kw: None)
    monkeypatch.setattr(sc, "ensure_daemon_running", lambda: False)
    monkeypatch.setattr(sc, "_grab_image", lambda *a, **k: None)
    assert sc.capture_screen_bytes() is None


# ── capture_screen (base64) ───────────────────────────────────────────────

def test_capture_screen_base64(monkeypatch):
    import utils.screen_capture_daemon_client as client_mod
    raw = _png()
    monkeypatch.setattr(client_mod, "get_frame_bytes", lambda **kw: raw)
    out = sc.capture_screen()
    assert out == base64.b64encode(raw).decode()


def test_capture_screen_none(monkeypatch):
    import utils.screen_capture_daemon_client as client_mod
    monkeypatch.setattr(client_mod, "get_frame_bytes", lambda **kw: None)
    monkeypatch.setattr(sc, "ensure_daemon_running", lambda: False)
    monkeypatch.setattr(sc, "_grab_image", lambda *a, **k: None)
    assert sc.capture_screen() is None


# ── _grab_image ───────────────────────────────────────────────────────────

def test_grab_image_darwin_screencapture(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "darwin")
    monkeypatch.setattr(sc, "_try_screencapture", lambda: _img((100, 50)))
    img = sc._grab_image(50, None)
    assert img.size == (50, 25)  # 缩放


def test_grab_image_darwin_fail(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "darwin")
    monkeypatch.setattr(sc, "_try_screencapture", lambda: None)
    assert sc._grab_image(50, None) is None


def test_grab_image_darwin_exception(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "darwin")
    monkeypatch.setattr(sc, "_try_screencapture", lambda: (_ for _ in ()).throw(RuntimeError("no perm")))
    assert sc._grab_image(50, None) is None


def test_grab_image_non_darwin_imagegrab(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "linux")
    monkeypatch.setattr(sc, "_try_imagegrab", lambda: _img((20, 20)))
    img = sc._grab_image(1280, None)
    assert img.size == (20, 20)  # 不超过 max_width → 不缩放


def test_grab_image_non_darwin_fallback_screencapture(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "linux")
    monkeypatch.setattr(sc, "_try_imagegrab", lambda: None)
    monkeypatch.setattr(sc, "_try_screencapture", lambda: _img((40, 40)))
    img = sc._grab_image(20, None)
    assert img.size == (20, 20)


def test_grab_image_non_darwin_imagegrab_exception(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "linux")
    monkeypatch.setattr(sc, "_try_imagegrab", lambda: (_ for _ in ()).throw(RuntimeError("no display")))
    monkeypatch.setattr(sc, "_try_screencapture", lambda: _img((40, 40)))
    img = sc._grab_image(20, None)
    assert img.size == (20, 20)


def test_grab_image_non_darwin_both_exceptions(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "linux")
    monkeypatch.setattr(sc, "_try_imagegrab", lambda: (_ for _ in ()).throw(RuntimeError("no display")))
    monkeypatch.setattr(sc, "_try_screencapture", lambda: (_ for _ in ()).throw(RuntimeError("no screencapture")))
    assert sc._grab_image(20, None) is None


def test_grab_image_all_fail(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "linux")
    monkeypatch.setattr(sc, "_try_imagegrab", lambda: None)
    monkeypatch.setattr(sc, "_try_screencapture", lambda: None)
    assert sc._grab_image(20, None) is None


def test_grab_image_region_crop(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "darwin")
    monkeypatch.setattr(sc, "_try_screencapture", lambda: _img((100, 100)))
    img = sc._grab_image(100, (10, 10, 30, 40))
    assert img.size == (30, 40)


def test_grab_image_bad_region_ignored(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "darwin")
    monkeypatch.setattr(sc, "_try_screencapture", lambda: _img((100, 100)))
    img = sc._grab_image(100, (1, 2))  # 长度 != 4 → 不裁剪
    assert img.size == (100, 100)


# ── _try_imagegrab / _try_screencapture ───────────────────────────────────

def test_try_imagegrab_success(monkeypatch):
    from PIL import ImageGrab
    monkeypatch.setattr(ImageGrab, "grab", lambda: _img((5, 5)))
    assert sc._try_imagegrab().size == (5, 5)


def test_try_imagegrab_failure(monkeypatch):
    from PIL import ImageGrab
    monkeypatch.setattr(ImageGrab, "grab", lambda: (_ for _ in ()).throw(RuntimeError("no display")))
    assert sc._try_imagegrab() is None


def test_try_screencapture_success(monkeypatch):
    png = _png((8, 6))

    def fake_run(cmd, timeout=5, check=True, capture_output=True):
        with open(cmd[-1], "wb") as f:
            f.write(png)

    monkeypatch.setattr(subprocess, "run", fake_run)
    img = sc._try_screencapture()
    assert img is not None
    assert img.size == (8, 6)


def test_try_screencapture_subprocess_error(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "screencapture")),
    )
    assert sc._try_screencapture() is None


def test_try_screencapture_missing_file(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(sc.os, "unlink", lambda p: None)  # 跳过真实删除
    assert sc._try_screencapture() is None  # Image.open 失败


def test_try_screencapture_unlink_error(monkeypatch):
    png = _png()

    def fake_run(cmd, timeout=5, check=True, capture_output=True):
        with open(cmd[-1], "wb") as f:
            f.write(png)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sc.os, "unlink", lambda p: (_ for _ in ()).throw(OSError("no perm")))
    assert sc._try_screencapture() is not None  # 清理失败不影响结果
