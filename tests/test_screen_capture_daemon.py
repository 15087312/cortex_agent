"""screen_capture_daemon 测试：常驻截图进程 + 客户端 + 回退"""
import base64
import io
import json
import socket
import threading
import time
from unittest.mock import MagicMock, patch

import os

import infra.screen_capture_daemon as daemon_mod
from utils import screen_capture_daemon_client as client_mod
from utils import screen_capture as sc_mod


class FakeImage:
    def __init__(self, size=(100, 50)):
        self._size = size

    @property
    def size(self):
        return self._size

    def crop(self, box):
        return FakeImage((box[2] - box[0], box[3] - box[1]))

    def resize(self, size):
        return FakeImage(size)

    def convert(self, mode):
        return self

    def save(self, buf, format="PNG"):
        buf.write(b"\x89PNG\r\n\x1a\nfake")


def _daemon(**kw):
    d = daemon_mod.ScreenCaptureDaemon.__new__(daemon_mod.ScreenCaptureDaemon)
    d._socket_path = kw.get("socket_path", "/tmp/test_socket.sock")
    d._cached_img = kw.get("cached_img", None)
    d._cached_ts = kw.get("cached_ts", 0.0)
    d._lock = threading.Lock()
    d._shutdown = threading.Event()
    return d


def test_ping_request():
    d = _daemon()
    resp = d.handle_request({"id": 1, "method": "ping"})
    assert resp["result"]["ok"] is True


def test_unknown_method():
    d = _daemon()
    resp = d.handle_request({"id": 2, "method": "nope"})
    assert "error" in resp


def test_frame_request_success(monkeypatch):
    d = _daemon()
    monkeypatch.setattr(daemon_mod, "_screencapture_image", lambda: FakeImage())
    resp = d.handle_request({"id": 3, "method": "frame", "params": {"max_width": 1280}})
    assert "result" in resp
    png = base64.b64decode(resp["result"]["png"])
    assert png  # 非空
    assert resp["result"]["width"] == 100


def test_frame_caches():
    d = _daemon()
    calls = {"n": 0}

    def fake_shot():
        calls["n"] += 1
        return FakeImage()

    with patch.object(daemon_mod, "_screencapture_image", fake_shot):
        d.handle_request({"id": 1, "method": "frame"})
        d.handle_request({"id": 2, "method": "frame"})
        assert calls["n"] == 1  # 缓存命中，只截一次


def test_frame_region_and_resize():
    d = _daemon()
    with patch.object(daemon_mod, "_screencapture_image", lambda: FakeImage((200, 100))):
        resp = d.handle_request({
            "id": 1, "method": "frame",
            "params": {"max_width": 50, "region": [0, 0, 100, 50]},
        })
        assert resp["result"]["width"] == 50
        assert resp["result"]["height"] == 25


def test_frame_failure(monkeypatch):
    d = _daemon()
    monkeypatch.setattr(daemon_mod, "_screencapture_image", lambda: None)
    resp = d.handle_request({"id": 1, "method": "frame"})
    assert "error" in resp


# ── 客户端 ──

def test_client_no_socket():
    with patch.object(client_mod, "SOCKET_PATH", "/nonexistent.sock"):
        assert client_mod.ping() is False
        assert client_mod.get_frame_bytes() is None


def test_client_rpc_roundtrip(tmp_path):
    sock_path = "/tmp/cortex_test_rpc.sock"
    try:
        if os.path.exists(sock_path):
            os.unlink(sock_path)
    except OSError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    server.listen(1)
    server.settimeout(2.0)

    def serve():
        conn, _ = server.accept()
        try:
            with conn.makefile("rwb") as f:
                line = f.readline()
                req = json.loads(line)
                resp = {"id": req["id"], "result": {"ok": True}}
                f.write((json.dumps(resp) + "\n").encode())
                f.flush()
        finally:
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    with patch.object(client_mod, "SOCKET_PATH", sock_path):
        assert client_mod.ping(timeout=2.0) is True
    server.close()
    try:
        os.unlink(sock_path)
    except OSError:
        pass


def test_client_get_frame_bytes(tmp_path):
    sock_path = "/tmp/cortex_test_frame.sock"
    try:
        if os.path.exists(sock_path):
            os.unlink(sock_path)
    except OSError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    server.listen(1)
    server.settimeout(2.0)

    def serve():
        conn, _ = server.accept()
        try:
            with conn.makefile("rwb") as f:
                line = f.readline()
                req = json.loads(line)
                resp = {"id": req["id"], "result": {
                    "png": base64.b64encode(b"\x89PNG-fake").decode(),
                    "width": 10, "height": 10,
                }}
                f.write((json.dumps(resp) + "\n").encode())
                f.flush()
        finally:
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    with patch.object(client_mod, "SOCKET_PATH", sock_path):
        out = client_mod.get_frame_bytes(timeout=2.0)
        assert out == b"\x89PNG-fake"
    server.close()
    try:
        os.unlink(sock_path)
    except OSError:
        pass


# ── capture_screen 回退 ──

def test_capture_screen_bytes_daemon_first(monkeypatch):
    monkeypatch.setattr(sc_mod, "SCREENSHOT_ENABLED", True)
    monkeypatch.setattr("utils.screen_capture_daemon_client.get_frame_bytes", lambda **kw: b"daemon-png")
    assert sc_mod.capture_screen_bytes() == b"daemon-png"


def test_capture_screen_bytes_fallback_local(monkeypatch):
    monkeypatch.setattr(sc_mod, "SCREENSHOT_ENABLED", True)
    monkeypatch.setattr("utils.screen_capture_daemon_client.get_frame_bytes", lambda **kw: None)
    fake_img = MagicMock()
    fake_img.size = (10, 5)
    buf = io.BytesIO()
    fake_img.save.return_value = None
    fake_img.save.side_effect = lambda b, format="PNG": b.write(b"local-png")
    monkeypatch.setattr(sc_mod, "_grab_image", lambda *a, **k: fake_img)
    monkeypatch.setattr(sc_mod, "ensure_daemon_running", lambda: False)
    assert sc_mod.capture_screen_bytes() == b"local-png"


def test_capture_screen_bytes_disabled(monkeypatch):
    monkeypatch.setattr(sc_mod, "SCREENSHOT_ENABLED", False)
    assert sc_mod.capture_screen_bytes() is None


def test_ensure_daemon_running_launches(monkeypatch):
    monkeypatch.setattr(sc_mod, "_DAEMON_PROC", None)
    monkeypatch.setattr(sc_mod.sys, "platform", "darwin")
    import subprocess
    proc = MagicMock()
    proc.poll.return_value = None
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(sc_mod.os.path, "exists", lambda p: True)
    assert sc_mod.ensure_daemon_running() is True
    sc_mod._DAEMON_PROC = None


def test_ensure_daemon_running_already_running():
    proc = MagicMock()
    proc.poll.return_value = None
    sc_mod._DAEMON_PROC = proc
    try:
        assert sc_mod.ensure_daemon_running() is True
    finally:
        sc_mod._DAEMON_PROC = None


def test_ensure_daemon_running_non_mac(monkeypatch):
    monkeypatch.setattr(sc_mod, "_DAEMON_PROC", None)
    monkeypatch.setattr(sc_mod.sys, "platform", "win32")
    assert sc_mod.ensure_daemon_running() is False


def test_daemon_run_integration(monkeypatch):
    """真实启动 daemon socket 服务，客户端取帧（端到端）"""
    sock_path = "/tmp/cortex_test_daemon.sock"
    try:
        if os.path.exists(sock_path):
            os.unlink(sock_path)
    except OSError:
        pass

    d = daemon_mod.ScreenCaptureDaemon(socket_path=sock_path)
    monkeypatch.setattr(daemon_mod, "_screencapture_image", lambda: FakeImage((80, 40)))

    t = threading.Thread(target=d.run, daemon=True)
    t.start()
    time.sleep(0.3)

    with patch.object(client_mod, "SOCKET_PATH", sock_path):
        assert client_mod.ping(timeout=2.0) is True
        png = client_mod.get_frame_bytes(max_width=64, timeout=2.0)
        assert png is not None
        b64 = client_mod.get_frame_base64(timeout=2.0)
        assert b64 is not None

    # 停止 daemon（无 stdin /dev/null 场景下通过 shutdown 事件）
    d._shutdown.set()
    t.join(timeout=2)
    assert not os.path.exists(sock_path)


def test_daemon_run_ignores_dev_null_stdin(monkeypatch):
    """stdin 为 /dev/null 时 daemon 不应立即退出"""
    sock_path = "/tmp/cortex_test_stdin.sock"
    try:
        if os.path.exists(sock_path):
            os.unlink(sock_path)
    except OSError:
        pass

    d = daemon_mod.ScreenCaptureDaemon(socket_path=sock_path)
    t = threading.Thread(target=d.run, daemon=True)
    t.start()
    time.sleep(0.3)

    import stat as _stat
    with patch.object(daemon_mod.os.path, "exists", lambda p: p == sock_path):
        pass

    with patch.object(client_mod, "SOCKET_PATH", sock_path):
        assert client_mod.ping(timeout=2.0) is True

    d._shutdown.set()
    t.join(timeout=2)
    assert not os.path.exists(sock_path)
