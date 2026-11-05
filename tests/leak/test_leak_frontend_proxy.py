"""前端 SSE 代理每请求资源有界（断连闭环的内存安全回归）

类型: 资源型泄漏防护——代理为每个 SSE 请求创建「上游连接 + 后台读取线程 +
队列」，浏览器刷新/关页后必须全部释放；否则桌宠持续思考场景下每次刷新都
残留一条上游连接与一个读线程，最终拖垮前端进程。

（对应 ERRORS_AND_FIXES 中「刷新后前端收不到消息」的修复：客户端断开即关闭
上游连接，后端 ASGI 才能取消生成器）
"""
import gc
import io
import socket
import socketserver
import threading
import time

import pytest

import frontend.server as srv
import modules.thinking.api_stream  # noqa: F401  (预热导入，排除一次性分配)

pytestmark = pytest.mark.leak


# ── 直连 handler：线程 / 队列 / 连接不随请求数增长 ─────────────────────────

class _Resp:
    """假上游 SSE 响应：先给一帧，再 EOF"""

    def __init__(self):
        self.closed = False
        self._reads = 0

    def getheader(self, name):
        return "text/event-stream" if name.lower() == "content-type" else None

    def read1(self, _n):
        self._reads += 1
        return b"data: 1\n\n" if self._reads == 1 else b""

    def read(self):
        return b""

    def close(self):
        self.closed = True


class _Conn:
    def __init__(self):
        self.resp = _Resp()
        self.sock = None
        self.closed = False

    def connect(self):
        pass

    def request(self, *a, **k):
        pass

    def getresponse(self):
        return self.resp

    def close(self):
        self.closed = True


def _handler():
    h = srv.ProxyHandler.__new__(srv.ProxyHandler)
    h.path = "/api/stream/pet/chat"
    h.command = "POST"
    h.headers = {}
    h.rfile = io.BytesIO(b"")
    h.wfile = io.BytesIO()
    h.send_response = lambda *a, **k: None
    h.send_header = lambda *a, **k: None
    h.end_headers = lambda: None
    h.close_connection = False
    h._client_gone = lambda: False
    return h


def test_proxy_requests_do_not_accumulate_threads(monkeypatch):
    """连续 60 次 SSE 转发后，后台读线程数不随请求数增长"""
    monkeypatch.setattr(srv.http.client, "HTTPConnection", lambda *a, **k: _Conn())

    def one():
        h = _handler()
        h._proxy_sse("POST", 8080, "/stream/pet/chat", b"{}")

    for _ in range(5):          # 预热（首次导入/线程池初始化）
        one()
    time.sleep(0.2)
    base = threading.active_count()

    for _ in range(60):
        one()
    time.sleep(0.5)
    after = threading.active_count()
    assert after - base <= 2, f"后台读线程泄漏: {base} → {after}"

    # 每个请求都必须关闭上游连接（否则后端生成器不会取消）
    conns = []
    monkeypatch.setattr(srv.http.client, "HTTPConnection",
                        lambda *a, **k: conns.append(_Conn()) or conns[-1])
    one()
    assert conns[-1].closed and conns[-1].resp.closed


def test_client_gone_check_does_not_leave_sockets(tmp_path):
    """反复断连检测（select + MSG_PEEK）不残留 socket 对象"""
    h = _handler()
    local, peer = socket.socketpair()
    h.connection = local
    try:
        for _ in range(50):
            h._client_gone()
        gc.collect()
        before = len(gc.get_objects())
        for _ in range(200):
            h._client_gone()
        gc.collect()
        growth = len(gc.get_objects()) - before
        assert growth < 500, f"断连检测分配增长 {growth}"
    finally:
        local.close()
        peer.close()


# ── 真实服务器：断连后上游连接确实被关闭（不累积） ────────────────────────

class _UpstreamHandler(socketserver.BaseRequestHandler):
    """最小 SSE 上游：连上就发一帧，然后等对端关闭"""

    closed = 0
    _lock = threading.Lock()

    def handle(self):
        self.request.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
            b"Connection: close\r\n\r\n"
        )
        try:
            while True:
                if not self.request.recv(4096):
                    break
        except OSError:
            pass
        finally:
            with _UpstreamHandler._lock:
                _UpstreamHandler.closed += 1


class _UpstreamServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def test_proxy_disconnect_closes_upstream_for_every_request(tmp_path, monkeypatch):
    """N 次刷新代理后，上游连接应全部关闭（没有一条被挂住）"""
    from utils import port_discovery

    _UpstreamHandler.closed = 0
    upstream = _UpstreamServer(("127.0.0.1", 0), _UpstreamHandler)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    monkeypatch.setattr(port_discovery, "_PORT_FILE", str(tmp_path / "port.json"))
    port_discovery.save_backend_port(upstream.server_address[1])

    proxy = srv.create_server(0)
    proxy.daemon_threads = True
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    proxy_port = proxy.server_address[1]

    try:
        for _ in range(5):
            client = socket.create_connection(("127.0.0.1", proxy_port), timeout=5)
            try:
                client.sendall(
                    b"POST /api/stream/pet/chat HTTP/1.1\r\nHost: x\r\n"
                    b"Accept: text/event-stream\r\nContent-Length: 0\r\n\r\n"
                )
                client.recv(4096)
            finally:
                client.close()
        deadline = time.time() + 10
        while time.time() < deadline and _UpstreamHandler.closed < 5:
            time.sleep(0.1)
        assert _UpstreamHandler.closed == 5, f"上游仅关闭 {_UpstreamHandler.closed}/5 条"
    finally:
        proxy.shutdown()
        upstream.shutdown()
