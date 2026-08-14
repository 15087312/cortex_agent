"""external_api 补充测试：SSRF 边界 / headers 解析 / 错误分支 / call_external_api"""
import socket
from unittest.mock import patch

from infra.tool_manager.tools import external_api as ea


# ── _is_private_ip 边界 ───────────────────────────────────────────────────────

def test_no_hostname():
    assert ea._is_private_ip("not a url") is False
    assert ea._is_private_ip("") is False


def test_domain_resolves_to_private(monkeypatch):
    fake = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 80))]
    with patch("socket.getaddrinfo", return_value=fake):
        assert ea._is_private_ip("https://internal.example.com/x") is True


def test_domain_resolves_loopback(monkeypatch):
    fake = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]
    with patch("socket.getaddrinfo", return_value=fake):
        assert ea._is_private_ip("https://localhost.test") is True


def test_domain_getaddrinfo_empty(monkeypatch):
    with patch("socket.getaddrinfo", return_value=[]):
        assert ea._is_private_ip("https://nohost.example.com") is False


def test_domain_gaierror(monkeypatch):
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("nxdomain")):
        assert ea._is_private_ip("https://bad.example.com") is False


def test_domain_oserror(monkeypatch):
    with patch("socket.getaddrinfo", side_effect=OSError("conn")):
        assert ea._is_private_ip("https://bad.example.com") is False


def test_reserved_ip():
    # 192.0.2.0/24 是保留段（is_reserved）
    assert ea._is_private_ip("http://192.0.2.1/") is True
    # 链路本地
    assert ea._is_private_ip("http://169.254.10.10/") is True


def test_is_private_ip_generic_exception(monkeypatch):
    with patch("urllib.parse.urlparse", side_effect=Exception("boom")):
        assert ea._is_private_ip("anything") is False


# ── http_get 分支 ─────────────────────────────────────────────────────────────

class _Resp:
    ok = True
    status_code = 200
    url = "https://example.com"
    text = "hello world"
    headers = {"content-type": "text/plain"}


def _patch_req(monkeypatch, method="get", side_effect=None):
    import requests as req_mod
    monkeypatch.setattr(ea, "_is_private_ip", lambda u: False)
    if side_effect is not None:
        def _fn(*a, **k):
            return side_effect(*a, **k)
        monkeypatch.setattr(req_mod, method, _fn)
    else:
        monkeypatch.setattr(req_mod, method, lambda *a, **k: _Resp())


def test_http_get_empty_url():
    assert ea.http_get("") == {"error": "URL 不能为空"}


def test_http_get_invalid_headers(monkeypatch):
    _patch_req(monkeypatch)
    r = ea.http_get("https://example.com", headers="not-json")
    assert "JSON" in r["error"]


def test_http_get_with_headers(monkeypatch):
    import requests as req_mod
    captured = {}
    def fake_get(url, **kwargs):
        captured.update(kwargs)
        return _Resp()
    _patch_req(monkeypatch, side_effect=fake_get)
    r = ea.http_get("https://example.com", headers='{"Authorization": "Bearer x"}', timeout=100)
    assert r["success"] is True
    assert captured["headers"] == {"Authorization": "Bearer x"}
    assert captured["timeout"] == 100
    assert captured["allow_redirects"] is True


def test_http_get_timeout_clamped(monkeypatch):
    import requests as req_mod
    captured = {}
    def fake_get(url, **kwargs):
        captured.update(kwargs)
        return _Resp()
    _patch_req(monkeypatch, side_effect=fake_get)
    ea.http_get("https://example.com", timeout=2)
    assert captured["timeout"] == 5
    ea.http_get("https://example.com", timeout=500)
    assert captured["timeout"] == 120


def test_http_get_timeout_error(monkeypatch):
    import requests as req_mod
    _patch_req(monkeypatch, side_effect=lambda *a, **k: (_ for _ in ()).throw(req_mod.exceptions.Timeout()))
    r = ea.http_get("https://example.com")
    assert "超时" in r["error"]


def test_http_get_connection_error(monkeypatch):
    import requests as req_mod
    _patch_req(monkeypatch, side_effect=lambda *a, **k: (_ for _ in ()).throw(req_mod.exceptions.ConnectionError("refused")))
    r = ea.http_get("https://example.com")
    assert "连接失败" in r["error"]


def test_http_get_generic_error(monkeypatch):
    _patch_req(monkeypatch, side_effect=lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")))
    r = ea.http_get("https://example.com")
    assert r["error"] == "bad"


# ── http_post 分支 ────────────────────────────────────────────────────────────

def test_http_post_empty_url():
    assert ea.http_post("") == {"error": "URL 不能为空"}


def test_http_post_invalid_headers(monkeypatch):
    _patch_req(monkeypatch, method="post")
    r = ea.http_post("https://example.com", headers="not-json")
    assert "JSON" in r["error"]


def test_http_post_with_headers(monkeypatch):
    import requests as req_mod
    captured = {}
    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return _Resp()
    _patch_req(monkeypatch, method="post", side_effect=fake_post)
    r = ea.http_post("https://example.com", data='{"a":1}', headers='{"X": "y"}')
    assert r["success"] is True
    assert captured["data"] == '{"a":1}'
    assert captured["headers"] == {"X": "y"}


def test_http_post_timeout_error(monkeypatch):
    import requests as req_mod
    _patch_req(monkeypatch, method="post", side_effect=lambda *a, **k: (_ for _ in ()).throw(req_mod.exceptions.Timeout()))
    r = ea.http_post("https://example.com")
    assert "超时" in r["error"]


def test_http_post_connection_error(monkeypatch):
    import requests as req_mod
    _patch_req(monkeypatch, method="post", side_effect=lambda *a, **k: (_ for _ in ()).throw(req_mod.exceptions.ConnectionError("refused")))
    r = ea.http_post("https://example.com")
    assert "连接失败" in r["error"]


def test_http_post_generic_error(monkeypatch):
    _patch_req(monkeypatch, method="post", side_effect=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = ea.http_post("https://example.com")
    assert r["error"] == "boom"


# ── call_external_api ─────────────────────────────────────────────────────────

def test_call_external_api_invalid_method():
    r = ea.call_external_api("https://example.com", method="OPTIONS")
    assert "不支持的 HTTP 方法" in r["error"]


def test_call_external_api_get(monkeypatch):
    import requests as req_mod
    _patch_req(monkeypatch)
    monkeypatch.setattr(ea, "http_get", lambda *a, **k: {"success": True, "method": "get"})
    r = ea.call_external_api("https://example.com", method="get")
    assert r["method"] == "get"


def test_call_external_api_post(monkeypatch):
    monkeypatch.setattr(ea, "http_post", lambda *a, **k: {"success": True, "method": "post"})
    r = ea.call_external_api("https://example.com", method="POST", data="{}")
    assert r["method"] == "post"


def test_call_external_api_put(monkeypatch):
    monkeypatch.setattr(ea, "http_post", lambda *a, **k: {"success": True, "method": "post"})
    r = ea.call_external_api("https://example.com", method="PUT", data="{}")
    assert r["method"] == "post"
