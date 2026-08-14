"""utils/screen_capture_daemon_client 测试 — socket 行协议客户端

mock socket/子进程，绝不真实连接 daemon。
"""
import base64
import json
import subprocess
import time
from unittest.mock import MagicMock

import pytest

import utils.screen_capture_daemon_client as cc


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    cc._last_warn_ts[0] = 0.0
    monkeypatch.setattr(cc, "_DAEMON_START_LOCK", __import__("threading").Lock())
    yield


# ── 假 socket ─────────────────────────────────────────────────────────────

class _FakeFile:
    def __init__(self, line):
        self._line = line

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def readline(self):
        return self._line


class _FakeSock:
    """成功路径：connect/sendall/readline/close 正常"""

    def __init__(self, line=b"{}", close_error=None):
        self._line = line
        self._close_error = close_error
        self.closed = False
        self.sent = b""
        self.timeout = None

    def settimeout(self, t):
        self.timeout = t

    def connect(self, path):
        pass

    def sendall(self, data):
        self.sent = data

    def makefile(self, mode):
        return _FakeFile(self._line)

    def close(self):
        self.closed = True
        if self._close_error:
            raise self._close_error


def _patch_rpc(monkeypatch, sock=None, exists=True):
    monkeypatch.setattr(cc.os.path, "exists", lambda p: exists)
    if sock is not None:
        monkeypatch.setattr(cc.socket, "socket", lambda *a, **k: sock)
    monkeypatch.setattr(cc.time, "sleep", lambda *a, **k: None)


def _resp(**payload):
    return (json.dumps(payload) + "\n").encode()


# ── _ensure_daemon ────────────────────────────────────────────────────────

def test_ensure_daemon_socket_exists(monkeypatch):
    monkeypatch.setattr(cc.os.path, "exists", lambda p: True)
    assert cc._ensure_daemon() is True


def test_ensure_daemon_path_missing(monkeypatch):
    monkeypatch.setattr(cc.os.path, "exists", lambda p: False)
    assert cc._ensure_daemon() is False


def test_ensure_daemon_launches(monkeypatch):
    def exists(p):
        return p.endswith("screen_capture_daemon.py")

    monkeypatch.setattr(cc.os.path, "exists", exists)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: MagicMock())
    assert cc._ensure_daemon() is True


def test_ensure_daemon_popen_error(monkeypatch):
    def exists(p):
        return p.endswith("screen_capture_daemon.py")

    monkeypatch.setattr(cc.os.path, "exists", exists)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    assert cc._ensure_daemon() is False


def test_ensure_daemon_second_check_under_lock(monkeypatch):
    """外层检查 False、锁内检查 True → 不拉起直接返回"""
    calls = {"n": 0}

    def exists(p):
        calls["n"] += 1
        return calls["n"] >= 2

    monkeypatch.setattr(cc.os.path, "exists", exists)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("不应拉起"))
    assert cc._ensure_daemon() is True


# ── _rpc ──────────────────────────────────────────────────────────────────

def test_rpc_success(monkeypatch):
    sock = _FakeSock(line=_resp(result={"ok": True}))
    _patch_rpc(monkeypatch, sock)
    out = cc._rpc("ping")
    assert out == {"result": {"ok": True}}
    assert sock.closed is True
    assert b'"method": "ping"' in sock.sent


def test_rpc_empty_line_returns_none(monkeypatch):
    sock = _FakeSock(line=b"")
    _patch_rpc(monkeypatch, sock)
    assert cc._rpc("ping") is None


def test_rpc_close_error_swallowed(monkeypatch):
    sock = _FakeSock(line=_resp(result={"ok": True}), close_error=OSError("close fail"))
    _patch_rpc(monkeypatch, sock)
    assert cc._rpc("ping") == {"result": {"ok": True}}


def test_rpc_connection_refused_retries_then_none(monkeypatch):
    class Refused(_FakeSock):
        def __init__(self):
            super().__init__()
            self.attempt = 0

        def connect(self, path):
            self.attempt += 1
            raise ConnectionRefusedError("refused")

    sock = Refused()
    _patch_rpc(monkeypatch, sock)
    monkeypatch.setattr(cc, "_last_warn_ts", [0.0])
    assert cc._rpc("ping") is None
    assert sock.attempt == 2  # 两次尝试


def test_rpc_file_not_found_retries(monkeypatch):
    class Missing(_FakeSock):
        def connect(self, path):
            raise FileNotFoundError("no sock")

    sock = Missing()
    _patch_rpc(monkeypatch, sock)
    monkeypatch.setattr(cc, "_last_warn_ts", [0.0])
    assert cc._rpc("ping") is None


def test_rpc_generic_exception(monkeypatch):
    class Bad(_FakeSock):
        def connect(self, path):
            raise RuntimeError("weird")

    sock = Bad()
    _patch_rpc(monkeypatch, sock)
    monkeypatch.setattr(cc, "_last_warn_ts", [0.0])
    assert cc._rpc("ping") is None


def test_rpc_ensure_daemon_sleeps(monkeypatch):
    """socket 不存在 → _ensure_daemon 成功 → sleep(0.2) 后连接"""
    monkeypatch.setattr(cc.os.path, "exists", lambda p: False)
    monkeypatch.setattr(cc, "_ensure_daemon", lambda: True)
    slept = []
    monkeypatch.setattr(cc.time, "sleep", lambda s: slept.append(s))
    sock = _FakeSock(line=_resp(result={"ok": True}))
    monkeypatch.setattr(cc.socket, "socket", lambda *a, **k: sock)
    assert cc._rpc("ping") == {"result": {"ok": True}}
    assert 0.2 in slept


def test_rpc_ensure_daemon_fails(monkeypatch):
    monkeypatch.setattr(cc.os.path, "exists", lambda p: False)
    monkeypatch.setattr(cc, "_ensure_daemon", lambda: False)
    assert cc._rpc("ping") is None


# ── _warn_throttled ───────────────────────────────────────────────────────

def test_warn_throttled_within_window(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(cc, "logger", logger)
    cc._last_warn_ts[0] = 0.0  # 距上次 >30s → 首条打日志
    cc._warn_throttled("第一条")
    cc._warn_throttled("第二条")  # 30s 内 → 静默
    logger.warning.assert_called_once()
    assert logger.warning.call_args[0][0] == "第一条"


def test_warn_throttled_after_window(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(cc, "logger", logger)
    cc._last_warn_ts[0] = time.time() - 31
    cc._warn_throttled("过期警告")
    logger.warning.assert_called_once_with("过期警告")
    assert cc._last_warn_ts[0] > time.time() - 30


# ── ping ──────────────────────────────────────────────────────────────────

def test_ping_ok(monkeypatch):
    sock = _FakeSock(line=_resp(result={"ok": True}))
    _patch_rpc(monkeypatch, sock)
    assert cc.ping(timeout=2.0) is True


def test_ping_not_ok(monkeypatch):
    sock = _FakeSock(line=_resp(result={"ok": False}))
    _patch_rpc(monkeypatch, sock)
    assert cc.ping() is False


def test_ping_missing_result(monkeypatch):
    sock = _FakeSock(line=_resp())
    _patch_rpc(monkeypatch, sock)
    assert cc.ping() is False


# ── get_frame_bytes ───────────────────────────────────────────────────────

def test_get_frame_bytes_ok(monkeypatch):
    png = b"\x89PNG-fake"
    sock = _FakeSock(line=_resp(result={"png": base64.b64encode(png).decode()}))
    _patch_rpc(monkeypatch, sock)
    assert cc.get_frame_bytes(max_width=64) == png


def test_get_frame_bytes_region_param(monkeypatch):
    png = b"abc"
    sock = _FakeSock(line=_resp(result={"png": base64.b64encode(png).decode()}))
    _patch_rpc(monkeypatch, sock)
    cc.get_frame_bytes(region=(0, 0, 10, 10))
    req = json.loads(sock.sent.decode())
    assert req["params"]["region"] == [0, 0, 10, 10]


def test_get_frame_bytes_bad_region_ignored(monkeypatch):
    png = b"abc"
    sock = _FakeSock(line=_resp(result={"png": base64.b64encode(png).decode()}))
    _patch_rpc(monkeypatch, sock)
    cc.get_frame_bytes(region=(1, 2))  # 长度 !=4 → 不带 region
    req = json.loads(sock.sent.decode())
    assert "region" not in req["params"]


def test_get_frame_bytes_no_result(monkeypatch):
    _patch_rpc(monkeypatch, _FakeSock(line=_resp()))
    assert cc.get_frame_bytes() is None


def test_get_frame_bytes_decode_error(monkeypatch):
    sock = _FakeSock(line=_resp(result={"png": "!!not-base64!!"}))
    _patch_rpc(monkeypatch, sock)
    assert cc.get_frame_bytes() is None


def test_get_frame_bytes_rpc_none(monkeypatch):
    _patch_rpc(monkeypatch, _FakeSock(line=b""))
    assert cc.get_frame_bytes() is None


# ── get_frame_base64 ──────────────────────────────────────────────────────

def test_get_frame_base64_ok(monkeypatch):
    sock = _FakeSock(line=_resp(result={"png": "aGVsbG8="}))
    _patch_rpc(monkeypatch, sock)
    assert cc.get_frame_base64() == "aGVsbG8="


def test_get_frame_base64_region_param(monkeypatch):
    sock = _FakeSock(line=_resp(result={"png": "aGVsbG8="}))
    _patch_rpc(monkeypatch, sock)
    cc.get_frame_base64(region=(0, 0, 20, 20))
    req = json.loads(sock.sent.decode())
    assert req["params"]["region"] == [0, 0, 20, 20]


def test_get_frame_base64_no_result(monkeypatch):
    _patch_rpc(monkeypatch, _FakeSock(line=_resp()))
    assert cc.get_frame_base64() is None


def test_get_frame_base64_rpc_none(monkeypatch):
    _patch_rpc(monkeypatch, _FakeSock(line=b""))
    assert cc.get_frame_base64() is None
