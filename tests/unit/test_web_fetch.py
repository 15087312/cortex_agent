"""web_fetch 测试（此前 19% 覆盖）：URL 校验/SSRF/方法/请求/异常"""
import asyncio

import pytest

from infra.tool_manager.tools import web_fetch as wf


def _run(coro):
    return asyncio.run(coro)


def test_private_ip_detection():
    # 内网/回环
    import ipaddress
    assert wf._is_private_ip("127.0.0.1") is True
    assert wf._is_private_ip("localhost") is True  # 回环解析
    assert wf._is_private_ip("169.254.169.254") is True


def test_private_ip_unresolvable_rejected():
    assert wf._is_private_ip("不存在的域名.invalid") is True  # 无法解析 → 拒绝


def test_web_fetch_bad_url():
    assert _run(wf.web_fetch("ftp://x"))["error"]
    assert _run(wf.web_fetch(""))["error"]


def test_web_fetch_ssrf_blocked(monkeypatch):
    monkeypatch.setattr(wf, "_is_private_ip", lambda h: True)
    r = _run(wf.web_fetch("http://127.0.0.1/admin"))
    assert "禁止访问内网" in r["error"]


def test_web_fetch_bad_method(monkeypatch):
    # mock 内网检查：避免本地代理 fake-ip 把 example.com 解析为内网地址
    monkeypatch.setattr(wf, "_is_private_ip", lambda h: False)
    r = _run(wf.web_fetch("https://example.com", method="PUT"))
    assert "不支持的 HTTP 方法" in r["error"]


def test_web_fetch_bad_headers(monkeypatch):
    import requests as req_mod
    monkeypatch.setattr(wf, "_is_private_ip", lambda h: False)
    monkeypatch.setattr(wf, "requests", req_mod)
    r = _run(wf.web_fetch("https://example.com", headers="not-json"))
    assert "JSON" in r["error"]


def test_web_fetch_success(monkeypatch):
    import requests as req_mod
    class Resp:
        url = "https://example.com/final"
        status_code = 200
        text = "<html>你好</html>"
        headers = {"content-type": "text/html"}
    monkeypatch.setattr(wf, "_is_private_ip", lambda h: False)
    monkeypatch.setattr(req_mod, "request", lambda *a, **k: Resp())
    r = _run(wf.web_fetch("https://example.com"))
    assert r["status_code"] == 200
    assert "你好" in r["content"]
    assert r["url"] == "https://example.com/final"


def test_web_fetch_truncates(monkeypatch):
    import requests as req_mod
    class Resp:
        url = "u"
        status_code = 200
        text = "x" * (wf.MAX_CONTENT_LENGTH + 1000)
        headers = {}
    monkeypatch.setattr(wf, "_is_private_ip", lambda h: False)
    monkeypatch.setattr(req_mod, "request", lambda *a, **k: Resp())
    r = _run(wf.web_fetch("https://example.com"))
    assert r["truncated"] is True


def test_web_fetch_timeout(monkeypatch):
    import requests as req_mod
    monkeypatch.setattr(wf, "_is_private_ip", lambda h: False)
    monkeypatch.setattr(req_mod, "request", lambda *a, **k: (_ for _ in ()).throw(req_mod.exceptions.Timeout()))
    r = _run(wf.web_fetch("https://example.com"))
    assert "超时" in r["error"]


# ── 防御性分支：SSRF 边界 / 异常回退 ─────────────────────────────────────────

def test_private_ip_public_address():
    import socket
    def fake_getaddrinfo(*a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))]
    real = socket.getaddrinfo
    socket.getaddrinfo = fake_getaddrinfo
    try:
        assert wf._is_private_ip("example.com") is False
    finally:
        socket.getaddrinfo = real


def test_private_ip_cloud_metadata(monkeypatch):
    import socket
    class FakeIP:
        is_private = is_loopback = is_link_local = is_reserved = False
        def __str__(self):
            return "169.254.169.254"
    monkeypatch.setattr(wf.ipaddress, "ip_address", lambda s: FakeIP())
    def fake_getaddrinfo(*a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert wf._is_private_ip("metadata.example") is True


def test_web_fetch_connection_error(monkeypatch):
    import requests as req_mod
    monkeypatch.setattr(wf, "_is_private_ip", lambda h: False)
    monkeypatch.setattr(req_mod, "request", lambda *a, **k: (_ for _ in ()).throw(req_mod.exceptions.ConnectionError("refused")))
    r = _run(wf.web_fetch("https://example.com"))
    assert "连接失败" in r["error"]


def test_web_fetch_generic_error(monkeypatch):
    import requests as req_mod
    monkeypatch.setattr(wf, "_is_private_ip", lambda h: False)
    monkeypatch.setattr(req_mod, "request", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    r = _run(wf.web_fetch("https://example.com"))
    assert "boom" in r["error"]
