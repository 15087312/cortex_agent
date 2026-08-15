"""frontend/server.py — 前端静态服务与 API 代理层测试

覆盖本文件此前零测试的盲区：
- 代理 URL 构建（IPv4 127.0.0.1，非 localhost→IPv6 陷阱）
- 动态读后端端口（后端端口回退/重启后前端跟随）
- 健康检查代理、静态页面服务
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
