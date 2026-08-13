"""mouse_keyboard / external_api 测试"""
import json
from unittest.mock import patch, MagicMock

from infra.tool_manager.tools import mouse_keyboard as mk
from infra.tool_manager.tools import external_api as ea


# ── external_api：SSRF / GET / POST ─────────────────────────────────────────

def test_is_private_ip():
    assert ea._is_private_ip("http://127.0.0.1/x") is True
    assert ea._is_private_ip("http://169.254.169.254/") is True
    assert ea._is_private_ip("http://10.0.0.1/") is True


def test_is_private_ip_public():
    # mock DNS：避免本地代理 fake-ip（如 198.18.x.x）把 example.com 解析为内网地址
    import socket
    fake_addr = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    with patch("socket.getaddrinfo", return_value=fake_addr):
        assert ea._is_private_ip("https://example.com") is False


def test_http_get_ssrf_blocked():
    with patch.object(ea, "_is_private_ip", lambda u: True):
        r = ea.http_get("http://127.0.0.1/admin")
    assert "禁止" in r["error"]


def test_http_get_success(monkeypatch):
    import requests as req_mod
    class Resp:
        url = "https://example.com"
        status_code = 200
        text = "data"
        headers = {}
        ok = True
    monkeypatch.setattr(ea, "_is_private_ip", lambda u: False)
    monkeypatch.setattr(req_mod, "get", lambda *a, **k: Resp())
    r = ea.http_get("https://example.com")
    assert r["status_code"] == 200


def test_http_get_timeout(monkeypatch):
    import requests as req_mod
    monkeypatch.setattr(ea, "_is_private_ip", lambda u: False)
    monkeypatch.setattr(req_mod, "get", lambda *a, **k: (_ for _ in ()).throw(req_mod.exceptions.Timeout()))
    r = ea.http_get("https://example.com")
    assert "超时" in r["error"]


def test_http_post_success(monkeypatch):
    import requests as req_mod
    class Resp:
        url = "https://example.com"
        status_code = 200
        text = "ok"
        headers = {}
        ok = True
    monkeypatch.setattr(ea, "_is_private_ip", lambda u: False)
    monkeypatch.setattr(req_mod, "post", lambda *a, **k: Resp())
    r = ea.http_post("https://example.com", data="{}")
    assert r["status_code"] == 200


# ── mouse_keyboard ──────────────────────────────────────────────────────────

def _patch_pag(monkeypatch):
    import sys, types
    pag = types.ModuleType("pyautogui")
    pag.FAILSAFE = True
    pag.PAUSE = 0.1
    for name in ["moveTo", "click", "scroll", "drag", "press", "write", "hotkey", "position", "doubleClick"]:
        setattr(pag, name, MagicMock())
    pag.position.return_value = (100, 100)
    monkeypatch.setitem(sys.modules, "pyautogui", pag)
    return pag


def test_mouse_move_success(monkeypatch):
    monkeypatch.setattr(mk, "_controller", MagicMock())
    mk._controller.move_to.return_value = True
    r = mk.mouse_move(100, 100)
    assert "鼠标移动到" in r


def test_mouse_click_success(monkeypatch):
    monkeypatch.setattr(mk, "_controller", MagicMock())
    mk._controller.click.return_value = True
    r = mk.mouse_click(100, 100)
    assert "点击" in r


def test_keyboard_type(monkeypatch):
    monkeypatch.setattr(mk, "_controller", MagicMock())
    mk._controller.type_text.return_value = True
    r = mk.keyboard_type("hello")
    assert "键盘输入" in r
