"""端口发现与自动回退测试"""
import os
import socket
import threading

import pytest

from utils.port_discovery import (
    pick_free_port, save_backend_port, read_backend_port, probe_health,
)


def _bind(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    return s


def test_pick_free_port_skips_occupied():
    s = _bind(18080)
    try:
        assert pick_free_port(18080) == 18081
    finally:
        s.close()


def test_pick_free_port_returns_preferred_when_free():
    assert pick_free_port(18090) == 18090


def test_save_and_read_backend_port(tmp_path, monkeypatch):
    import utils.port_discovery as pd
    target = str(tmp_path / "backend_port.json")
    monkeypatch.setattr(pd, "_PORT_FILE", target)
    save_backend_port(18081)
    assert read_backend_port() == 18081
    assert read_backend_port(default=18082) == 18081


def test_read_backend_port_missing_uses_default(tmp_path, monkeypatch):
    import utils.port_discovery as pd
    monkeypatch.setattr(pd, "_PORT_FILE", str(tmp_path / "nope.json"))
    assert read_backend_port() == 8080
    assert read_backend_port(default=9999) == 9999


def test_probe_health_true_for_healthy_backend():
    # 起一个返回 200 的 http server 模拟后端 /health
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        assert probe_health(port) is True
    finally:
        srv.shutdown()


def test_probe_health_false_when_unreachable():
    s = _bind(18085)
    try:
        # 端口有监听但非健康后端（不响应 /health）
        assert probe_health(18085, timeout=0.3) is False
    finally:
        s.close()
