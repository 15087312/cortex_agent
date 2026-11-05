"""frontend/server.py — 前端静态服务与 API 代理层测试

覆盖本文件此前零测试的盲区：
- 代理 URL 构建（IPv4 127.0.0.1，非 localhost→IPv6 陷阱）
- 动态读后端端口（后端端口回退/重启后前端跟随）
- 健康检查代理、静态页面服务
- SSE 代理全部分支：`_client_gone`（可读/EOF/select 异常/peek 阻塞）、
  JSON 直通（Content-Length）、逐帧转发、客户端断连关闭上游（先 shutdown 再 close，
  见 §103/§103.1）、上游不可达 502、上游空闲超时终止帧
"""
import json
import io
import os
from unittest.mock import patch, MagicMock

import pytest


# ── _resolve_backend_port：动态读后端端口 ──

def test_resolve_backend_port_reads_discovery_file(tmp_path, monkeypatch):
    import frontend.server as srv
    from utils import port_discovery
    monkeypatch.setattr(port_discovery, "_PORT_FILE", str(tmp_path / "port.json"))
    port_discovery.save_backend_port(18081)
    assert srv._resolve_backend_port() == 18081


def test_resolve_backend_port_default_8080(monkeypatch):
    import frontend.server as srv
    from utils import port_discovery
    monkeypatch.setattr(port_discovery, "_PORT_FILE", "/nonexistent/port.json")
    assert srv._resolve_backend_port() == 8080


# ── ProxyHandler 代理逻辑 ──

def _make_handler(path="/api/health", method="GET"):
    """构造 ProxyHandler 实例（绕过 socketserver 需要）。"""
    import frontend.server as srv

    class FakeServer:
        def __init__(self):
            self._serve_dir = srv.FRONTEND_DIR

    handler = srv.ProxyHandler.__new__(srv.ProxyHandler)
    handler.server = FakeServer()
    handler.path = path
    handler.command = method
    handler.headers = {}
    handler.rfile = io.BytesIO(b"")
    handler.wfile = io.BytesIO()
    handler.send_response = lambda *a, **k: None
    handler.send_header = lambda *a, **k: None
    handler.end_headers = lambda: None
    return handler


def test_proxy_uses_ipv4_not_localhost():
    """代理 URL 必须用 127.0.0.1（macOS localhost 解析 ::1 导致 502 的回归测试）"""
    import frontend.server as srv
    handler = _make_handler("/api/health")
    captured = {}

    def fake_urlopen(req, timeout=3):
        captured["url"] = req.full_url
        resp = MagicMock()
        resp.status = 200
        resp.headers = {}
        resp.read.return_value = b'{"ok":true}'
        return resp

    with patch("urllib.request.urlopen", fake_urlopen), \
         patch("urllib.request.Request", lambda u, data=None, method=None: type("R", (), {"full_url": u})()):
        handler._proxy_request("GET")
    assert captured["url"].startswith("http://127.0.0.1:"), f"应使用 IPv4 回环，实际 {captured['url']}"


def test_proxy_dynamic_port_reads_latest(tmp_path, monkeypatch):
    """代理每次请求动态读后端端口（后端回退端口后前端跟随）"""
    import frontend.server as srv
    from utils import port_discovery
    monkeypatch.setattr(port_discovery, "_PORT_FILE", str(tmp_path / "port.json"))
    port_discovery.save_backend_port(18081)

    handler = _make_handler("/api/health")
    captured = {}

    def fake_urlopen(req, timeout=3):
        captured["url"] = req.full_url
        resp = MagicMock()
        resp.status = 200
        resp.headers = {}
        resp.read.return_value = b"ok"
        return resp

    with patch("urllib.request.urlopen", fake_urlopen), \
         patch("urllib.request.Request", lambda u, data=None, method=None: type("R", (), {"full_url": u})()):
        handler._proxy_request("GET")
    assert "18081" in captured["url"], f"应动态读取最新端口，实际 {captured['url']}"

    # 后端端口变化 → 下一次请求跟随
    port_discovery.save_backend_port(19000)
    with patch("urllib.request.urlopen", fake_urlopen), \
         patch("urllib.request.Request", lambda u, data=None, method=None: type("R", (), {"full_url": u})()):
        handler._proxy_request("GET")
    assert "19000" in captured["url"], f"端口变化后应跟随，实际 {captured['url']}"


def test_proxy_strips_api_prefix():
    """/api/health → 后端 /health"""
    import frontend.server as srv
    from utils import port_discovery
    handler = _make_handler("/api/health")
    captured = {}

    def fake_urlopen(req, timeout=3):
        captured["url"] = req.full_url
        resp = MagicMock()
        resp.status = 200
        resp.headers = {}
        resp.read.return_value = b"ok"
        return resp

    with patch("urllib.request.urlopen", fake_urlopen), \
         patch("urllib.request.Request", lambda u, data=None, method=None: type("R", (), {"full_url": u})()):
        handler._proxy_request("GET")
    assert captured["url"].endswith("/health"), f"应剥 /api 前缀，实际 {captured['url']}"


def test_proxy_passes_through_error_status():
    """后端错误状态（401 等）应透传，前端 res.ok 据此判断"""
    import frontend.server as srv
    import urllib.error
    handler = _make_handler("/api/health")
    status = {}

    def fake_urlopen(req, timeout=3):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    handler.send_response = lambda code, *a: status.update(code=code)
    with patch("urllib.request.urlopen", fake_urlopen):
        handler._proxy_request("GET")
    assert status.get("code") == 401


# ── 静态页面 / 后端端口端点 ──

def test_serve_backend_port_endpoint():
    import frontend.server as srv
    handler = _make_handler("/backend-port")
    written = {}
    handler.send_response = lambda *a, **k: None
    handler.send_header = lambda *a, **k: None
    handler.end_headers = lambda: None
    handler.wfile = io.BytesIO()
    handler._serve_backend_port()
    body = handler.wfile.getvalue().decode()
    assert json.loads(body)["port"] == srv.BACKEND_PORT


def test_create_server_uses_port():
    import frontend.server as srv
    with patch("socketserver.ThreadingTCPServer") as m:
        srv.create_server(9999)
    args = m.call_args[0]
    assert args[0] == ("", 9999)


# ── SSE 代理：客户端断开必须关闭上游 ──

def test_proxy_sse_closes_upstream_when_client_disconnects(tmp_path, monkeypatch):
    """浏览器刷新/关页后代理必须关闭上游 SSE。

    回归：旧实现用 requests.iter_content 阻塞读上游，只有写失败才会发现客户端
    断开；桌宠两轮思考间隔可达 30s，期间没有任何写操作，于是上游永远收不到断连
    → `max_loop_rounds=0` 的持续思考继续空转并占住串行队列，重新打开页面后
    新请求排队拿不到任何 token（“前端收不到消息”）。
    """
    import http.server
    import socket
    import socketserver
    import threading
    import time

    import frontend.server as srv
    from utils import port_discovery

    state = {"closed": threading.Event()}

    class _UpstreamHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length:
                self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for i in range(2000):
                    self.wfile.write(
                        f'event: token\ndata: {{"token":"{i}"}}\n\n'.encode())
                    self.wfile.flush()
                    time.sleep(0.05)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                state["closed"].set()

    class _UpstreamServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    upstream = _UpstreamServer(("127.0.0.1", 0), _UpstreamHandler)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    monkeypatch.setattr(port_discovery, "_PORT_FILE", str(tmp_path / "port.json"))
    port_discovery.save_backend_port(upstream.server_address[1])

    proxy = srv.create_server(0)
    proxy.daemon_threads = True
    threading.Thread(target=proxy.serve_forever, daemon=True).start()

    # 用裸 socket 当客户端：http.client 关闭连接时会被仍存活的 response
    # 文件对象续命，模拟不出「浏览器刷新」的真实断连。
    client = socket.create_connection(("127.0.0.1", proxy.server_address[1]), timeout=5)
    try:
        client.sendall(
            b"POST /api/stream/pet/chat HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Accept: text/event-stream\r\n"
            b"Content-Length: 13\r\n\r\n"
            b'{"text":"hi"}'
        )
        first = client.recv(4096)
        assert b"200" in first
        for _ in range(20):  # headers 与首个数据帧可能分两次到达
            if b"event: token" in first:
                break
            first += client.recv(4096)
        assert b"event: token" in first
    finally:
        client.close()
        proxy.shutdown()
        upstream.shutdown()

    assert state["closed"].wait(5), "客户端断开后未关闭上游 SSE：桌宠会继续无限思考并占住队列"


# ── SSE 代理：内部分支（_client_gone / 分流 / 空闲超时 / 502 / 清理） ──

class _FakeSock:
    def __init__(self):
        self.timeouts = []
        self.shutdowns = []

    def settimeout(self, value):
        self.timeouts.append(value)

    def shutdown(self, how):
        self.shutdowns.append(how)


class _FakeUpstreamResponse:
    """假上游响应：可控 chunk 序列 / 阻塞读 / close 异常。"""

    def __init__(self, ctype="text/event-stream", status=200, chunks=(), body=b"",
                 block=False, close_error=None):
        import threading
        self._ctype = ctype
        self.status = status
        self._chunks = list(chunks)
        self._body = body
        self._block = block
        self._release = threading.Event()
        self.closed = False
        self.close_error = close_error

    def getheader(self, name):
        return self._ctype if name.lower() == "content-type" else None

    def read(self):
        return self._body

    def read1(self, _n):
        if self._block:
            self._release.wait(2.0)
            return b""
        return self._chunks.pop(0) if self._chunks else b""

    def close(self):
        self.closed = True
        self._release.set()
        if self.close_error:
            raise self.close_error


class _FakeUpstreamConnection:
    def __init__(self, resp, connect_error=None):
        self.resp = resp
        self.sock = _FakeSock()
        self.requests = []
        self.closed = False
        self.connect_error = connect_error

    def connect(self):
        if self.connect_error:
            raise self.connect_error

    def request(self, method, path, body=None, headers=None):
        self.requests.append({"method": method, "path": path, "body": body, "headers": headers})

    def getresponse(self):
        return self.resp

    def close(self):
        self.closed = True


def _sse_handler(monkeypatch, resp, path="/api/stream/pet/chat", method="POST",
                 headers=None, connect_error=None):
    import frontend.server as srv
    handler = _make_handler(path, method)
    handler.headers = headers if headers is not None else {}
    conn = _FakeUpstreamConnection(resp, connect_error)
    monkeypatch.setattr(srv.http.client, "HTTPConnection", lambda *a, **k: conn)
    handler._client_gone = lambda: False
    return handler, conn


def test_client_gone_false_without_readable_data():
    """客户端没发数据也没断开 → select 不可读 → 视为仍在线"""
    import socket
    handler = _make_handler()
    local, peer = socket.socketpair()
    try:
        handler.connection = local
        assert handler._client_gone() is False
    finally:
        local.close()
        peer.close()


def test_client_gone_true_on_eof():
    """对端 close（浏览器刷新/关页）→ peek 到 EOF → 已断开"""
    import socket
    handler = _make_handler()
    local, peer = socket.socketpair()
    handler.connection = local
    peer.close()
    try:
        assert handler._client_gone() is True
    finally:
        local.close()


def test_client_gone_keeps_pending_bytes():
    """可读但还有数据（不是 EOF）→ 未断开，且不消费客户端数据"""
    import socket
    handler = _make_handler()
    local, peer = socket.socketpair()
    try:
        peer.sendall(b"x")
        handler.connection = local
        assert handler._client_gone() is False
        assert local.recv(1) == b"x"  # MSG_PEEK 不吞字节
    finally:
        local.close()
        peer.close()


def test_client_gone_true_when_select_fails(monkeypatch):
    """select 本身报错（socket 已关闭）→ 视为断开，避免代理线程死循环"""
    import frontend.server as srv
    handler = _make_handler()
    handler.connection = object()
    monkeypatch.setattr(srv.select, "select", lambda *a, **k: (_ for _ in ()).throw(OSError("bad fd")))
    assert handler._client_gone() is True


def test_client_gone_blocking_recv_means_online(monkeypatch):
    """readable 但 recv 抛 BlockingIOError（select 竞态）→ 仍在线的保守判断"""
    import socket
    local, peer = socket.socketpair()

    class _Conn:
        def fileno(self):
            return local.fileno()

        def recv(self, *a):
            raise BlockingIOError()

    try:
        peer.sendall(b"x")
        handler = _make_handler()
        handler.connection = _Conn()
        assert handler._client_gone() is False
    finally:
        local.close()
        peer.close()


def test_client_gone_true_on_recv_oserror():
    """recv 抛 OSError（对端 RST）→ 已断开"""
    import socket
    handler = _make_handler()
    local, peer = socket.socketpair()

    class _Conn:
        def fileno(self):
            return local.fileno()

        def recv(self, *a):
            raise OSError("reset")

    try:
        peer.sendall(b"x")
        handler.connection = _Conn()
        assert handler._client_gone() is True
    finally:
        local.close()
        peer.close()


def test_proxy_sse_json_response_gets_content_length(monkeypatch):
    """非 event-stream 的上游响应按普通 JSON 转发（带 Content-Length，浏览器能结束请求）"""
    resp = _FakeUpstreamResponse(ctype="application/json", body=b'{"ok":1}')
    handler, conn = _sse_handler(monkeypatch, resp)
    status, headers = {}, {}
    handler.send_response = lambda code, *a, **k: status.update(code=code)
    handler.send_header = lambda k, v: headers.update({k: v})
    handler.end_headers = lambda: None

    handler._proxy_sse("GET", 1234, "/stream/pet/history", None)

    assert status["code"] == 200
    assert headers["Content-Type"] == "application/json"
    assert headers["Content-Length"] == str(len(b'{"ok":1}'))
    assert handler.wfile.getvalue() == b'{"ok":1}'
    assert resp.closed and conn.closed


def test_proxy_sse_json_defaults_content_type(monkeypatch):
    """上游没给 Content-Type 时按 application/json 兜底（浏览器按 JSON 解析）"""
    resp = _FakeUpstreamResponse(ctype="", body=b"{}")
    handler, _ = _sse_handler(monkeypatch, resp)
    headers = {}
    handler.send_header = lambda k, v: headers.update({k: v})

    handler._proxy_sse("GET", 1234, "/stream/pet/history", None)

    assert headers["Content-Type"] == "application/json"


def test_proxy_sse_filters_hop_by_hop_headers(monkeypatch):
    """Host/Content-Length/Connection 不转发；业务头（X-API-Key）保留"""
    resp = _FakeUpstreamResponse(chunks=[b"data: 1\n\n"])
    handler, conn = _sse_handler(monkeypatch, resp, headers={
        "Host": "127.0.0.1:8765",
        "Content-Length": "2",
        "Connection": "keep-alive",
        "X-API-Key": "secret",
    })

    handler._proxy_sse("POST", 1234, "/stream/pet/chat", b"{}")

    sent = conn.requests[0]["headers"]
    assert "X-API-Key" in sent
    for key in ("Host", "Content-Length", "Connection"):
        assert key not in sent
    assert conn.requests[0]["body"] == b"{}"


def test_proxy_sse_relaxes_read_timeout_after_connect(monkeypatch):
    """连接建立后把读超时放宽到上游空闲上限（5s 会把正常长连接误判断开）"""
    resp = _FakeUpstreamResponse(chunks=[b"data: 1\n\n"])
    handler, conn = _sse_handler(monkeypatch, resp)

    handler._proxy_sse("POST", 1234, "/stream/pet/chat", b"{}")

    assert conn.sock.timeouts == [300]


def test_proxy_sse_streams_all_chunks_then_closes(monkeypatch):
    """SSE chunk 全部原样转发，结束后关闭上游（resp + conn）"""
    resp = _FakeUpstreamResponse(chunks=[b"data: a\n\n", b"data: b\n\n"])
    handler, conn = _sse_handler(monkeypatch, resp)

    handler._proxy_sse("POST", 1234, "/stream/pet/chat", b"{}")

    out = handler.wfile.getvalue()
    assert b"data: a" in out and b"data: b" in out
    assert resp.closed and conn.closed
    assert handler.close_connection is True  # 流结束即关连接


def test_proxy_sse_client_disconnect_stops_immediately(monkeypatch):
    """客户端已断开 → 不等上游下一帧，立刻退出并关闭上游"""
    resp = _FakeUpstreamResponse(block=True)
    handler, conn = _sse_handler(monkeypatch, resp)
    handler._client_gone = lambda: True

    handler._proxy_sse("POST", 1234, "/stream/pet/chat", b"{}")

    assert resp.closed and conn.closed, "断连后必须关闭上游，否则后端生成器不会取消"
    # 先 shutdown 再 close：读取线程正阻塞在 resp.read1 时，直接 close 会等锁 →
    # 代理线程被挂住（每条断连泄漏一个线程 + 一条上游连接）。
    import socket as _socket
    assert conn.sock.shutdowns == [_socket.SHUT_RDWR]


def test_proxy_sse_idle_timeout_emits_error_frame(monkeypatch):
    """上游长时间无字节 → 给前端终止帧并断开，避免请求永久挂起"""
    resp = _FakeUpstreamResponse(block=True)
    handler, conn = _sse_handler(monkeypatch, resp)
    import frontend.server as srv
    ticks = {"n": 0}

    def fake_time():
        ticks["n"] += 1
        return 0.0 if ticks["n"] <= 2 else 10_000.0

    monkeypatch.setattr(srv.time, "time", fake_time)

    handler._proxy_sse("POST", 1234, "/stream/pet/chat", b"{}")

    out = handler.wfile.getvalue()
    assert b"event: error" in out and b"backend idle timeout" in out
    assert resp.closed and conn.closed


def test_proxy_sse_upstream_unreachable_sends_502(monkeypatch):
    """后端未启动 → 502 + 可解析终止帧（旧行为不能因改造丢失）"""
    resp = _FakeUpstreamResponse()
    handler, _ = _sse_handler(monkeypatch, resp, connect_error=ConnectionRefusedError("refused"))
    status = {}
    handler.send_response = lambda code, *a, **k: status.update(code=code)

    handler._proxy_sse("POST", 1234, "/stream/pet/chat", b"{}")

    assert status["code"] == 502
    assert b"backend unreachable" in handler.wfile.getvalue()


def test_proxy_sse_no_502_after_headers_sent(monkeypatch):
    """流已开始后发生 OSError（上游 RST）→ 不能再发 502，只结束请求"""
    resp = _FakeUpstreamResponse(chunks=[b"data: a\n\n"])
    handler, _ = _sse_handler(monkeypatch, resp)
    status = []
    handler.send_response = lambda code, *a, **k: status.append(code)

    class _BrokenWFile(io.BytesIO):
        def write(self, data):
            raise OSError("upstream reset")

    handler.wfile = _BrokenWFile()

    handler._proxy_sse("POST", 1234, "/stream/pet/chat", b"{}")

    assert 200 in status and 502 not in status
    assert resp.closed


def test_proxy_sse_generic_error_writes_proxy_frame(monkeypatch):
    """读响应头阶段的意外异常 → 写 `proxy: ...` 终止帧，不把异常抛给 handler"""
    class _BoomResponse(_FakeUpstreamResponse):
        def getheader(self, name):
            raise ValueError("boom")

    handler, _ = _sse_handler(monkeypatch, _BoomResponse())

    handler._proxy_sse("POST", 1234, "/stream/pet/chat", b"{}")

    assert b"proxy: boom" in handler.wfile.getvalue()


def test_proxy_sse_close_errors_are_swallowed(monkeypatch):
    """上游 close 自身报错不能掩盖正常结束（两个 close 都尝试）"""
    resp = _FakeUpstreamResponse(chunks=[b"data: a\n\n"], close_error=RuntimeError("close boom"))
    handler, conn = _sse_handler(monkeypatch, resp)

    handler._proxy_sse("POST", 1234, "/stream/pet/chat", b"{}")

    assert b"data: a" in handler.wfile.getvalue()
    assert resp.closed and conn.closed


# ── pet_widget 后端端口 ──

def test_pet_widget_reads_discovery_port(tmp_path, monkeypatch):
    """桌宠读后端端口：发现文件有则用（127.0.0.1 IPv4，避免 IPv6 陷阱）"""
    from utils import port_discovery
    monkeypatch.setattr(port_discovery, "_PORT_FILE", str(tmp_path / "port.json"))
    port_discovery.save_backend_port(18081)

    import importlib
    import frontend.pet_widget as pw
    importlib.reload(pw)
    assert "18081" in pw.BACKEND_URL
    assert "127.0.0.1" in pw.BACKEND_URL


def test_pet_widget_falls_back_8080(tmp_path, monkeypatch):
    from utils import port_discovery
    monkeypatch.setattr(port_discovery, "_PORT_FILE", str(tmp_path / "missing.json"))

    import importlib
    import frontend.pet_widget as pw
    importlib.reload(pw)
    assert "8080" in pw.BACKEND_URL
