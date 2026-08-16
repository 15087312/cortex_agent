"""infra/screen_capture_daemon.py — 屏幕采集 daemon 逻辑测试。

不进行真实截图：mock 系统边界（subprocess 截图命令 / socket 绑定），
用 PIL 内存生成假图，覆盖 daemon 的缓存、请求处理、错误回退与 socket 竞态逻辑。
"""
import base64
import io
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import infra.screen_capture_daemon as sd_mod
from infra.screen_capture_daemon import (
    CACHE_TTL_SECONDS,
    ScreenCaptureDaemon,
    _crop_resize,
    _screencapture_image,
    _to_png_b64,
    main,
)


# ── 工具：内存生成假 PIL 图 ──────────────────────────────────────────────

def _make_png_bytes(size=(8, 6), color=(255, 0, 0)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


# ── _screencapture_image：截图命令包装 ───────────────────────────────────

def test_screencapture_success_returns_rgb(monkeypatch):
    """截图命令成功 → 返回 PIL RGB Image"""
    png = _make_png_bytes()

    def fake_run(cmd, **kw):
        # cmd = ["screencapture", "-x", "-C", "-t", "png", tmp_path]
        with open(cmd[-1], "wb") as f:
            f.write(png)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    img = _screencapture_image()
    assert img is not None
    assert img.mode == "RGB"
    assert img.size == (8, 6)


def test_screencapture_nonzero_returns_none(monkeypatch):
    """截图命令非零退出码 → None"""
    def fake_run(cmd, **kw):
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _screencapture_image() is None


def test_screencapture_missing_file_returns_none(monkeypatch):
    """命令成功但文件不存在 → None"""
    def fake_run(cmd, **kw):
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    assert _screencapture_image() is None


def test_screencapture_exception_returns_none(monkeypatch):
    """截图命令超时/异常 → None（不抛出）"""
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, timeout=10)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _screencapture_image() is None


def test_screencapture_cleans_temp_file(monkeypatch):
    """无论成败都清理临时文件"""
    png = _make_png_bytes()
    unlinked = []

    def fake_run(cmd, **kw):
        with open(cmd[-1], "wb") as f:
            f.write(png)
        return SimpleNamespace(returncode=0)

    real_unlink = os.unlink
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(os, "unlink", lambda p: (unlinked.append(p), real_unlink(p))[1])
    _screencapture_image()
    assert len(unlinked) == 1


# ── _crop_resize / _to_png_b64：纯函数 ────────────────────────────────────

def _img(size=(10, 8)):
    from PIL import Image
    return Image.new("RGB", size)


def test_crop_resize_region():
    img = _img((100, 100))
    out = _crop_resize(img, max_width=None, region=[0, 0, 20, 30])
    assert out.size == (20, 30)


def test_crop_resize_max_width():
    img = _img((100, 50))
    out = _crop_resize(img, max_width=50, region=None)
    assert out.size == (50, 25)


def test_crop_resize_noop():
    img = _img((30, 30))
    out = _crop_resize(img, max_width=None, region=None)
    assert out.size == (30, 30)
    assert out is img


def test_crop_resize_bad_region_ignored():
    img = _img((10, 10))
    out = _crop_resize(img, max_width=None, region=[0, 0, 0])  # 长度 !=4 → 不裁剪
    assert out.size == (10, 10)


def test_to_png_b64_roundtrip():
    img = _img((4, 4))
    data = _to_png_b64(img)
    assert isinstance(data, str)
    assert base64.b64decode(data).startswith(b"\x89PNG")


# ── ScreenCaptureDaemon 帧缓存 ────────────────────────────────────────────

class _FakeFrame:
    def __init__(self):
        self.size = (8, 6)


def test_get_fresh_frame_caches(monkeypatch):
    d = ScreenCaptureDaemon("/tmp/t.sock")
    img = _FakeFrame()
    monkeypatch.setattr(
        "infra.screen_capture_daemon._screencapture_image",
        lambda: img,
    )
    first = d._get_fresh_frame()
    second = d._get_fresh_frame()
    assert first is img and second is img  # 命中缓存，不再调用截图


def test_get_fresh_frame_expires(monkeypatch):
    d = ScreenCaptureDaemon("/tmp/t.sock")
    calls = []
    monkeypatch.setattr(
        "infra.screen_capture_daemon._screencapture_image",
        lambda: calls.append(1) or _FakeFrame(),
    )
    monkeypatch.setattr("infra.screen_capture_daemon.time.time", lambda: 0.0)
    d._get_fresh_frame()
    monkeypatch.setattr("infra.screen_capture_daemon.time.time", lambda: CACHE_TTL_SECONDS + 1)
    d._get_fresh_frame()
    assert len(calls) == 2  # 缓存过期 → 重新截图


def test_get_fresh_frame_failure_keeps_old_cache(monkeypatch):
    d = ScreenCaptureDaemon("/tmp/t.sock")
    img = _FakeFrame()
    monkeypatch.setattr(
        "infra.screen_capture_daemon._screencapture_image",
        lambda: img,
    )
    d._get_fresh_frame()
    # 后续截图失败 → 返回 None，但旧缓存保留
    monkeypatch.setattr(
        "infra.screen_capture_daemon._screencapture_image",
        lambda: None,
    )
    ts = d._cached_ts
    monkeypatch.setattr(
        "infra.screen_capture_daemon.time.time",
        lambda: ts + CACHE_TTL_SECONDS + 1,
    )
    assert d._get_fresh_frame() is None
    assert d._cached_img is img


# ── ScreenCaptureDaemon.handle_request ────────────────────────────────────

def test_handle_ping():
    d = ScreenCaptureDaemon("/tmp/t.sock")
    r = d.handle_request({"method": "ping", "id": 1})
    assert r["result"]["ok"] is True
    assert r["result"]["pid"] == os.getpid()


def test_handle_frame_ok(monkeypatch):
    d = ScreenCaptureDaemon("/tmp/t.sock")
    from PIL import Image
    monkeypatch.setattr(
        "infra.screen_capture_daemon._screencapture_image",
        lambda: Image.new("RGB", (10, 10)),
    )
    r = d.handle_request({"method": "frame", "id": 2, "params": {"max_width": 5}})
    assert r["result"]["width"] == 5
    assert r["result"]["height"] == 5
    assert r["result"]["png"].startswith("iVBOR")  # PNG base64 头


def test_handle_frame_screenshot_fail(monkeypatch):
    d = ScreenCaptureDaemon("/tmp/t.sock")
    monkeypatch.setattr(
        "infra.screen_capture_daemon._screencapture_image",
        lambda: None,
    )
    r = d.handle_request({"method": "frame", "id": 3})
    assert r["error"]["code"] == -32001


def test_handle_frame_processing_error(monkeypatch):
    d = ScreenCaptureDaemon("/tmp/t.sock")
    monkeypatch.setattr(
        "infra.screen_capture_daemon._screencapture_image",
        lambda: _FakeFrame(),
    )
    r = d.handle_request({"method": "frame", "id": 4, "params": {"max_width": "bad"}})
    assert r["error"]["code"] == -32002


def test_handle_unknown_method():
    d = ScreenCaptureDaemon("/tmp/t.sock")
    r = d.handle_request({"method": "nope", "id": 5})
    assert r["error"]["code"] == -32601


# ── _serve_connection：socket 行协议 ──────────────────────────────────────

class _FakeRW(io.BytesIO):
    """组合读写：readline 从输入，write 到输出"""

    def __init__(self, inp: bytes):
        super().__init__(inp)
        self._out = io.BytesIO()

    def write(self, data) -> int:
        self._out.write(data)
        return len(data)

    def flush(self):
        pass


class _FakeConn:
    def __init__(self, inp: bytes):
        self._inp = inp
        self.rw = None
        self.closed = False

    def settimeout(self, t):
        pass

    def makefile(self, mode):
        self.rw = _FakeRW(self._inp)
        return self.rw

    def close(self):
        self.closed = True


def test_serve_connection_ping(monkeypatch):
    d = ScreenCaptureDaemon("/tmp/t.sock")
    req = {"method": "ping", "id": 9}
    conn = _FakeConn((json.dumps(req) + "\n").encode())
    d._serve_connection(conn)
    out = conn.rw._out.getvalue().decode().strip()
    resp = json.loads(out)
    assert resp["result"]["ok"] is True
    assert conn.closed


def test_serve_connection_invalid_json(monkeypatch):
    d = ScreenCaptureDaemon("/tmp/t.sock")
    conn = _FakeConn(b"not-json\n")
    d._serve_connection(conn)
    out = conn.rw._out.getvalue().decode().strip()
    resp = json.loads(out)
    assert resp["error"]["code"] == -32700


# ── _bind_server：socket 竞态 ─────────────────────────────────────────────

def test_bind_success(monkeypatch):
    d = ScreenCaptureDaemon("/tmp/x.sock")
    fake_server = MagicMock()
    monkeypatch.setattr(socket, "socket", lambda *a, **k: fake_server)
    monkeypatch.setattr(sd_mod.os, "chmod", lambda *a, **k: None)
    assert d._bind_server() is fake_server
    fake_server.bind.assert_called_once()
    fake_server.listen.assert_called_once()


def test_bind_detects_existing_daemon(monkeypatch):
    """bind 失败但 socket 可连 → 已有 daemon → None（本进程退出）"""
    d = ScreenCaptureDaemon("/tmp/x.sock")
    call = {"n": 0}

    def fake_socket(*a, **k):
        call["n"] += 1
        s = MagicMock()
        if call["n"] == 1:
            s.bind.side_effect = OSError("in use")
        return s  # probe.connect 成功（默认不抛）

    monkeypatch.setattr(sd_mod.socket, "socket", fake_socket)
    assert d._bind_server() is None


def test_bind_stale_retry(monkeypatch):
    """bind 失败 + 探测不可连 → stale → 清理后重试成功"""
    d = ScreenCaptureDaemon("/tmp/x.sock")
    call = {"n": 0}
    unlinked = []

    def fake_socket(*a, **k):
        call["n"] += 1
        s = MagicMock()
        if call["n"] == 1:
            s.bind.side_effect = OSError("in use")  # 首次 bind 失败
        elif call["n"] <= 4:
            s.connect.side_effect = OSError("refused")  # 3 次探测均失败
        return s  # 第 5 次（清理后重试 bind）成功

    monkeypatch.setattr(sd_mod.socket, "socket", fake_socket)
    monkeypatch.setattr(sd_mod.os, "chmod", lambda *a, **k: None)
    monkeypatch.setattr(sd_mod.os.path, "exists", lambda p: True)
    monkeypatch.setattr(sd_mod.os, "unlink", lambda p: unlinked.append(p))
    monkeypatch.setattr(sd_mod.time, "sleep", lambda *a, **k: None)

    ret = d._bind_server()
    assert ret is not None
    assert len(unlinked) == 1


# ── run()：主循环 ─────────────────────────────────────────────────────────

def test_run_exits_when_bind_none(monkeypatch):
    """bind 失败返回 None → run 立即返回"""
    d = ScreenCaptureDaemon("/tmp/x.sock")
    monkeypatch.setattr(d, "_bind_server", lambda: None)
    d.run()


def test_run_loop_exit_on_server_close(monkeypatch):
    """bind 成功，accept 抛 OSError → 主循环退出并关闭 server"""
    d = ScreenCaptureDaemon("/tmp/x.sock")
    fake_server = MagicMock()
    fake_server.accept.side_effect = OSError("closed")
    monkeypatch.setattr(d, "_bind_server", lambda: fake_server)
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    d._shutdown.set()
    d.run()
    fake_server.close.assert_called_once()


def test_run_loop_skips_timeout_and_breaks(monkeypatch):
    """accept 先 socket.timeout（continue）再 OSError（break）"""
    d = ScreenCaptureDaemon("/tmp/x.sock")
    fake_server = MagicMock()
    fake_server.accept.side_effect = [socket.timeout, OSError("closed")]
    monkeypatch.setattr(d, "_bind_server", lambda: fake_server)
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    d.run()
    assert fake_server.accept.call_count == 2


def test_bind_final_failure_returns_none(monkeypatch):
    """探测失败 + 清理重试仍失败 → None"""
    d = ScreenCaptureDaemon("/tmp/x.sock")
    call = {"n": 0}

    def fake_socket(*a, **k):
        call["n"] += 1
        s = MagicMock()
        if call["n"] == 1 or call["n"] == 5:
            s.bind.side_effect = OSError("still in use")  # 首次 + 重试均失败
        elif call["n"] <= 4:
            s.connect.side_effect = OSError("refused")
        return s

    monkeypatch.setattr(sd_mod.socket, "socket", fake_socket)
    monkeypatch.setattr(sd_mod.os, "chmod", lambda *a, **k: None)
    monkeypatch.setattr(sd_mod.os.path, "exists", lambda p: True)
    monkeypatch.setattr(os, "unlink", lambda p: None)
    monkeypatch.setattr(sd_mod.time, "sleep", lambda *a, **k: None)
    assert d._bind_server() is None


def test_serve_connection_error_still_closes(monkeypatch):
    """makefile/settimeout 抛异常 → 连接仍被 close"""
    d = ScreenCaptureDaemon("/tmp/t.sock")

    class BadConn:
        def __init__(self):
            self.closed = False

        def settimeout(self, t):
            raise OSError("boom")

        def close(self):
            self.closed = True

    c = BadConn()
    d._serve_connection(c)
    assert c.closed


def test_main_creates_and_runs(monkeypatch):
    called = []

    def fake_run(self):
        called.append(self)

    monkeypatch.setattr(ScreenCaptureDaemon, "run", fake_run)
    main()
    assert len(called) == 1


# ── 补全：防御分支 / 边界分支 ─────────────────────────────────────────────

def test_screencapture_unlink_failure_swallowed(monkeypatch):
    """截图成功后清理临时文件失败 → 不抛，仍返回图片"""
    png = _make_png_bytes()

    def fake_run(cmd, **kw):
        with open(cmd[-1], "wb") as f:
            f.write(png)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(os, "unlink", lambda p: (_ for _ in ()).throw(OSError("no perm")))
    img = _screencapture_image()
    assert img is not None


def test_crop_resize_max_width_not_exceeded():
    """max_width 大于原宽 → 不缩放，返回原图"""
    img = _img((30, 30))
    out = _crop_resize(img, max_width=100, region=None)
    assert out.size == (30, 30)


def test_serve_connection_empty_input_breaks():
    """连接立即 EOF（无请求）→ 正常关闭"""
    d = ScreenCaptureDaemon("/tmp/t.sock")
    conn = _FakeConn(b"")
    d._serve_connection(conn)
    assert conn.closed


def test_serve_connection_close_failure_swallowed():
    """close 抛 OSError → 不向外抛"""
    d = ScreenCaptureDaemon("/tmp/t.sock")

    class BadClose(_FakeConn):
        def close(self):
            self.closed = True
            raise OSError("close fail")

    conn = BadClose((json.dumps({"method": "ping", "id": 1}) + "\n").encode())
    d._serve_connection(conn)  # 不抛异常
    assert conn.closed


def test_bind_stale_without_existing_file(monkeypatch):
    """stale 重试时 socket 文件已不存在 → 跳过 unlink 直接重绑"""
    d = ScreenCaptureDaemon("/tmp/x.sock")
    call = {"n": 0}

    def fake_socket(*a, **k):
        call["n"] += 1
        s = MagicMock()
        if call["n"] == 1:
            s.bind.side_effect = OSError("in use")
        elif call["n"] <= 4:
            s.connect.side_effect = OSError("refused")
        return s

    monkeypatch.setattr(sd_mod.socket, "socket", fake_socket)
    monkeypatch.setattr(sd_mod.os, "chmod", lambda *a, **k: None)
    monkeypatch.setattr(os.path, "exists", lambda p: False)  # 文件已不存在
    monkeypatch.setattr(os, "unlink", lambda p: (_ for _ in ()).throw(AssertionError("不应 unlink")))
    monkeypatch.setattr(sd_mod.time, "sleep", lambda *a, **k: None)
    assert d._bind_server() is not None


def test_run_main_loop_other_exception_logs_and_exits(monkeypatch):
    """accept 抛非 timeout/OSError 的异常 → 外层捕获、记日志、走 finally"""
    d = ScreenCaptureDaemon("/tmp/x.sock")
    fake_server = MagicMock()
    fake_server.accept.side_effect = ValueError("unexpected")
    monkeypatch.setattr(d, "_bind_server", lambda: fake_server)
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    d.run()  # 不抛异常
    fake_server.close.assert_called_once()


def test_run_finally_unlink_failure_swallowed(monkeypatch):
    """退出清理时 unlink 失败 → 不抛"""
    d = ScreenCaptureDaemon("/tmp/x.sock")
    fake_server = MagicMock()
    fake_server.accept.side_effect = OSError("closed")
    monkeypatch.setattr(d, "_bind_server", lambda: fake_server)
    monkeypatch.setattr(sd_mod.os.path, "exists", lambda p: True)
    monkeypatch.setattr(os, "unlink", lambda p: (_ for _ in ()).throw(OSError("no perm")))
    d.run()  # 不抛异常


class _SyncThread:
    """同步执行线程 target，避免 _watch_stdin 时序 flake"""

    def __init__(self, target, daemon=True, args=(), kwargs=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


def _run_with_stdin(monkeypatch, stdin_mock):
    """用 SyncThread + 指定 stdin 运行 daemon.run()"""
    d = ScreenCaptureDaemon("/tmp/x.sock")
    fake_server = MagicMock()
    fake_server.accept.side_effect = OSError("closed")
    monkeypatch.setattr(threading, "Thread", _SyncThread)
    monkeypatch.setattr(d, "_bind_server", lambda: fake_server)
    monkeypatch.setattr(sys, "stdin", stdin_mock)
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    d.run()
    return d


def test_watch_stdin_isatty_true_reads_to_eof(monkeypatch):
    """stdin 是 tty → read() 到 EOF → 触发 shutdown"""
    stdin = MagicMock()
    stdin.isatty.return_value = True
    stdin.read.return_value = ""  # EOF
    d = _run_with_stdin(monkeypatch, stdin)
    assert d._shutdown.is_set()


def test_watch_stdin_fstat_error_skips(monkeypatch):
    """stdin 非 tty 且拿不到 fd（StringIO）→ 不监听、不 shutdown"""
    stdin = MagicMock()
    stdin.isatty.return_value = False
    stdin.fileno.side_effect = OSError("no fd")
    d = _run_with_stdin(monkeypatch, stdin)
    assert not d._shutdown.is_set()


def test_watch_stdin_devnull_skips(monkeypatch):
    """stdin 是 /dev/null（char device）→ 不监听、不 shutdown"""
    stdin = MagicMock()
    stdin.isatty.return_value = False
    mode = MagicMock()
    mode.st_mode = stat.S_IFCHR
    stdin.fileno.return_value = 0
    monkeypatch.setattr(os, "fstat", lambda fd: mode)
    d = _run_with_stdin(monkeypatch, stdin)
    assert not d._shutdown.is_set()


def test_watch_stdin_pipe_reads_to_eof(monkeypatch):
    """stdin 是管道（非 tty 非 devnull）→ read() 到 EOF → shutdown"""
    stdin = MagicMock()
    stdin.isatty.return_value = False
    mode = MagicMock()
    mode.st_mode = stat.S_IFIFO
    stdin.fileno.return_value = 0
    stdin.read.return_value = ""
    monkeypatch.setattr(os, "fstat", lambda fd: mode)
    d = _run_with_stdin(monkeypatch, stdin)
    assert d._shutdown.is_set()


def test_watch_stdin_read_error_sets_shutdown(monkeypatch):
    """stdin.read() 抛异常 → 吞掉后仍触发 shutdown"""
    stdin = MagicMock()
    stdin.isatty.return_value = False
    mode = MagicMock()
    mode.st_mode = stat.S_IFIFO
    stdin.fileno.return_value = 0
    stdin.read.side_effect = OSError("read fail")
    monkeypatch.setattr(os, "fstat", lambda fd: mode)
    d = _run_with_stdin(monkeypatch, stdin)
    assert d._shutdown.is_set()


def test_run_accept_success_starts_handler(monkeypatch):
    """accept 成功 → 启动处理线程（同步执行）；随后 OSError → 退出"""
    d = ScreenCaptureDaemon("/tmp/x.sock")
    conn = _FakeConn(b"")
    fake_server = MagicMock()
    fake_server.accept.side_effect = [(conn, "addr"), OSError("closed")]
    monkeypatch.setattr(threading, "Thread", _SyncThread)
    monkeypatch.setattr(d, "_bind_server", lambda: fake_server)
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    # stdin = /dev/null 分支：不 set shutdown，主循环进入 accept
    stdin = MagicMock()
    stdin.isatty.return_value = False
    mode = MagicMock()
    mode.st_mode = stat.S_IFCHR
    stdin.fileno.return_value = 0
    monkeypatch.setattr(os, "fstat", lambda fd: mode)
    monkeypatch.setattr(sys, "stdin", stdin)
    d.run()
    assert conn.closed  # _serve_connection 已同步处理该连接
    fake_server.close.assert_called_once()


def test_serve_connection_skips_when_shutdown_set():
    """shutdown 已置位时收到连接 → while 不进入，直接关闭"""
    d = ScreenCaptureDaemon("/tmp/t.sock")
    d._shutdown.set()
    conn = _FakeConn(b"")
    d._serve_connection(conn)
    assert conn.closed


class _ThrowRW(_FakeRW):
    def readline(self):
        raise OSError("read fail")


class _ThrowConn(_FakeConn):
    def makefile(self, mode):
        self.rw = _ThrowRW(self._inp)
        return self.rw


def test_serve_connection_read_error_logs_and_closes():
    """连接内 readline 抛异常 → 记录日志、关闭连接、不向外抛"""
    d = ScreenCaptureDaemon("/tmp/t.sock")
    conn = _ThrowConn(b"")
    d._serve_connection(conn)
    assert conn.closed
