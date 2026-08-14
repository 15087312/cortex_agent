"""CDPScanner 测试：mock urlopen / websocket，不发起真实网络请求

覆盖：端口扫描、scan 分发、HTTP 降级、WebSocket DOM 扫描、DOM 解析、活跃应用扫描、单例。
"""
import json
import sys
import types
from urllib.error import URLError

import pytest

import infra.data_process.core.cdp_scanner as cdp_mod
from infra.data_process.core.cdp_scanner import CDPScanner


class _FakeResp:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data


def _scanner():
    return CDPScanner()


@pytest.fixture(autouse=True)
def _reset_captured_logger():
    _CapturedLogger._errors = []
    yield
    _CapturedLogger._errors = []


class _CapturedLogger:
    """记录模块 logger 的 error/warning 调用（模块 logger propagate 未必可用）"""

    _errors = []

    @classmethod
    def error(cls, msg, *a, **k):
        cls._errors.append(str(msg))

    @classmethod
    def warning(cls, msg, *a, **k):
        pass

    @classmethod
    def info(cls, msg, *a, **k):
        pass

    @classmethod
    def debug(cls, msg, *a, **k):
        pass


def _install_websocket(monkeypatch, responses, fail_create=False):
    """注入假 websocket 模块；responses 依次作为 recv() 返回值。"""
    fake = types.ModuleType("websocket")

    class FakeWS:
        def __init__(self, *a, **k):
            self.sent = []
            self._recv = list(responses)

        def send(self, msg):
            self.sent.append(msg)

        def recv(self):
            return self._recv.pop(0)

        def close(self):
            pass

    if fail_create:

        def create_connection(*a, **k):
            raise ConnectionError("refused")

    else:

        def create_connection(*a, **k):
            return FakeWS(*a, **k)

    fake.create_connection = create_connection
    monkeypatch.setitem(sys.modules, "websocket", fake)
    return fake


# ── __init__ / 端口扫描 ─────────────────────────────────────────────────────

def test_init():
    s = CDPScanner()
    assert s._connections == {}


def test_find_chromium_ports(monkeypatch):
    s = _scanner()
    pages = [
        {"id": "p1", "title": "T1", "url": "http://x", "type": "page"},
        {"id": "p2", "title": "T2", "url": "http://y", "type": "other"},
    ]

    def fake_urlopen(req, timeout=None):
        if "9222" in req.full_url:
            return _FakeResp(json.dumps(pages).encode())
        raise URLError("conn refused")

    monkeypatch.setattr(cdp_mod, "urlopen", fake_urlopen)
    results = s.find_chromium_ports()
    assert len(results) == 1
    assert results[0]["port"] == 9222
    assert len(results[0]["pages"]) == 1
    assert results[0]["pages"][0]["id"] == "p1"


def test_find_chromium_ports_empty_data(monkeypatch):
    s = _scanner()
    monkeypatch.setattr(cdp_mod, "urlopen", lambda req, timeout=None: _FakeResp(b"[]"))
    assert s.find_chromium_ports() == []


def test_find_chromium_ports_json_error_skipped(monkeypatch):
    s = _scanner()
    monkeypatch.setattr(cdp_mod, "urlopen", lambda req, timeout=None: _FakeResp(b"not json"))
    assert s.find_chromium_ports() == []


# ── scan() 分发 ─────────────────────────────────────────────────────────────

def _patch_pages(monkeypatch, pages):
    monkeypatch.setattr(
        cdp_mod, "urlopen",
        lambda req, timeout=None: _FakeResp(json.dumps(pages).encode()),
    )


def test_scan_empty_pages(monkeypatch):
    s = _scanner()
    _patch_pages(monkeypatch, [])
    assert s.scan(9222) == []


def test_scan_no_matching_page_id(monkeypatch):
    s = _scanner()
    _patch_pages(monkeypatch, [{"id": "a", "type": "page"}])
    assert s.scan(9222, page_id="zzz") == []


def test_scan_selects_ws_page(monkeypatch):
    s = _scanner()
    _patch_pages(monkeypatch, [{"id": "a", "type": "page", "webSocketDebuggerUrl": "ws://a"}])
    called = {}
    monkeypatch.setattr(s, "_scan_via_ws", lambda ws, d: called.__setitem__("ws", ws) or ["e1"])
    assert s.scan(9222) == ["e1"]
    assert called["ws"] == "ws://a"


def test_scan_selects_matching_page_id(monkeypatch):
    s = _scanner()
    _patch_pages(monkeypatch, [
        {"id": "a", "type": "page", "webSocketDebuggerUrl": "ws://a"},
        {"id": "b", "type": "page", "webSocketDebuggerUrl": "ws://b"},
    ])
    called = {}
    monkeypatch.setattr(s, "_scan_via_ws", lambda ws, d: called.__setitem__("ws", ws) or ["e"])
    assert s.scan(9222, page_id="b") == ["e"]
    assert called["ws"] == "ws://b"


def test_scan_skips_non_page_entries(monkeypatch):
    s = _scanner()
    _patch_pages(monkeypatch, [
        {"id": "x", "type": "other"},
        {"id": "a", "type": "page", "webSocketDebuggerUrl": "ws://a"},
    ])
    called = {}
    monkeypatch.setattr(s, "_scan_via_ws", lambda ws, d: called.__setitem__("ws", ws) or ["e"])
    assert s.scan(9222) == ["e"]
    assert called["ws"] == "ws://a"


def test_scan_falls_back_to_http(monkeypatch):
    s = _scanner()
    _patch_pages(monkeypatch, [{"id": "a", "type": "page"}])
    called = {}
    monkeypatch.setattr(s, "_scan_via_http", lambda port, pid: called.__setitem__("pid", pid) or ["h"])
    assert s.scan(9222) == ["h"]
    assert called["pid"] == "a"


def test_scan_exception_logs_error(monkeypatch):
    s = _scanner()
    monkeypatch.setattr(cdp_mod, "logger", _CapturedLogger())
    monkeypatch.setattr(
        cdp_mod, "urlopen",
        lambda req, timeout=None: (_ for _ in ()).throw(OSError("boom")),
    )
    assert s.scan(9222) == []
    assert any("CDP 扫描失败" in m for m in _CapturedLogger._errors)


# ── _scan_via_http ──────────────────────────────────────────────────────────

def test_scan_via_http_success(monkeypatch):
    s = _scanner()

    def fake_urlopen(req, timeout=None):
        if isinstance(req, str):
            return _FakeResp(b"")
        return _FakeResp(json.dumps([{"id": "a", "title": "T", "url": "U"}]).encode())

    monkeypatch.setattr(cdp_mod, "urlopen", fake_urlopen)
    res = s._scan_via_http(9222, "a")
    assert res[0]["type"] == "page"
    assert res[0]["name"] == "T"


def test_scan_via_http_page_missing(monkeypatch):
    s = _scanner()

    def fake_urlopen(req, timeout=None):
        if isinstance(req, str):
            return _FakeResp(b"")
        return _FakeResp(json.dumps([{"id": "other"}]).encode())

    monkeypatch.setattr(cdp_mod, "urlopen", fake_urlopen)
    assert s._scan_via_http(9222, "a") == []


def test_scan_via_http_exception(monkeypatch):
    s = _scanner()
    monkeypatch.setattr(
        cdp_mod, "urlopen",
        lambda req, timeout=None: (_ for _ in ()).throw(URLError("down")),
    )
    assert s._scan_via_http(9222, "a") == []


# ── _scan_via_ws ────────────────────────────────────────────────────────────

def test_scan_via_ws_success(monkeypatch):
    s = _scanner()
    root = {
        "nodeName": "DIV", "nodeType": 1, "attributes": [],
        "children": [
            {"nodeName": "BUTTON", "nodeType": 1, "attributes": ["aria-label", "提交"], "children": []}
        ],
    }
    responses = [json.dumps({}), json.dumps({"result": {"root": root}})]
    _install_websocket(monkeypatch, responses)
    res = s._scan_via_ws("ws://x", 3)
    assert len(res) == 1
    assert res[0]["role"] == "button"


def test_scan_via_ws_missing_websocket(monkeypatch):
    s = _scanner()
    monkeypatch.setitem(sys.modules, "websocket", None)
    assert s._scan_via_ws("ws://x", 3) == []


def test_scan_via_ws_exception(monkeypatch):
    s = _scanner()
    monkeypatch.setattr(cdp_mod, "logger", _CapturedLogger())
    _install_websocket(monkeypatch, [], fail_create=True)
    assert s._scan_via_ws("ws://x", 3) == []
    assert any("CDP WebSocket 扫描失败" in m for m in _CapturedLogger._errors)


# ── _parse_dom_node 补充分支 ────────────────────────────────────────────────

def test_parse_dom_odd_attributes_tail_ignored():
    s = _scanner()
    node = {"nodeName": "DIV", "nodeType": 1, "attributes": ["role", "button", "lone"], "children": []}
    els = s._parse_dom_node(node, 0, 3)
    assert els[0]["role"] == "button"


def test_parse_dom_text_from_child_node():
    s = _scanner()
    node = {
        "nodeName": "SPAN", "nodeType": 1, "attributes": [],
        "children": [{"nodeType": 3, "nodeValue": "  你好  "}],
    }
    els = s._parse_dom_node(node, 0, 3)
    assert els and els[0]["name"] == "你好"


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("BUTTON", "button"), ("INPUT", "text_field"), ("TEXTAREA", "text_field"),
        ("A", "link"), ("IMG", "image"), ("SELECT", "dropdown"),
        ("H1", "heading"), ("H2", "heading"), ("H3", "heading"),
        ("DIV", "group"), ("SPAN", "text"), ("P", "text"), ("ARTICLE", "unknown"),
    ],
)
def test_parse_dom_role_map(tag, expected):
    s = _scanner()
    node = {"nodeName": tag, "nodeType": 1, "attributes": [], "children": []}
    els = s._parse_dom_node(node, 0, 3)
    if expected in ("button", "text_field", "link", "heading"):
        assert len(els) == 1 and els[0]["role"] == expected
    else:
        assert els == []


def test_parse_dom_empty_text_child_then_valid(monkeypatch):
    """空白文本子节点不产出 text → 继续找下一个文本子节点"""
    s = _scanner()
    node = {
        "nodeName": "SPAN", "nodeType": 1, "attributes": [],
        "children": [
            {"nodeType": 3, "nodeValue": "   "},
            {"nodeType": 3, "nodeValue": "  有效文本  "},
        ],
    }
    els = s._parse_dom_node(node, 0, 3)
    assert els and els[0]["name"] == "有效文本"


def test_parse_dom_recursion():
    s = _scanner()
    btn = {"nodeName": "BUTTON", "nodeType": 1, "attributes": ["aria-label", "OK"], "children": []}
    parent = {"nodeName": "DIV", "nodeType": 1, "attributes": [], "children": [btn]}
    els = s._parse_dom_node(parent, 0, 3)
    assert len(els) == 1
    assert els[0]["role"] == "button"
    assert els[0]["children_count"] == 0


def test_parse_dom_recursion_respects_depth():
    s = _scanner()
    btn = {"nodeName": "BUTTON", "nodeType": 1, "attributes": ["aria-label", "OK"], "children": []}
    inner = {"nodeName": "DIV", "nodeType": 1, "attributes": [], "children": [btn]}
    parent = {"nodeName": "DIV", "nodeType": 1, "attributes": [], "children": [inner]}
    # btn 在 depth=2，max_depth=1 → 超深被跳过；parent/inner 无文本不产出元素
    assert s._parse_dom_node(parent, 0, 1) == []


def test_parse_dom_attribute_truncation():
    s = _scanner()
    node = {
        "nodeName": "INPUT", "nodeType": 1,
        "attributes": ["aria-label", "x" * 100, "type", "text", "data-x", "keep"],
        "children": [],
    }
    els = s._parse_dom_node(node, 0, 3)
    assert els
    assert len(els[0]["name"]) <= 100
    # data-x 不在白名单，不进入 attributes；type 在白名单
    assert "data-x" not in els[0]["attributes"]
    assert els[0]["attributes"]["type"] == "text"


# ── scan_active_chromium ────────────────────────────────────────────────────

def test_scan_active_chromium_no_ports(monkeypatch):
    s = _scanner()
    monkeypatch.setattr(s, "find_chromium_ports", lambda: [])
    r = s.scan_active_chromium()
    assert r["success"] is False
    assert "未找到 CDP 端口" in r["error"]


def test_scan_active_chromium_with_app_filter(monkeypatch):
    s = _scanner()
    ports = [{"port": 9222, "pages": [{"title": "MyApp", "id": "p1"}, {"title": "Other", "id": "p2"}]}]
    monkeypatch.setattr(s, "find_chromium_ports", lambda: ports)
    monkeypatch.setattr(s, "scan", lambda port, max_depth=3: [{"type": "button"}])
    r = s.scan_active_chromium(app_name="myapp")
    assert r["success"] is True
    assert len(r["results"]) == 1
    assert r["results"][0]["page"]["title"] == "MyApp"
    assert r["total_elements"] == 1


def test_scan_active_chromium_app_filter_no_match(monkeypatch):
    s = _scanner()
    ports = [{"port": 9222, "pages": [{"title": "Other", "id": "p2"}]}]
    monkeypatch.setattr(s, "find_chromium_ports", lambda: ports)
    monkeypatch.setattr(s, "scan", lambda port, max_depth=3: [])
    r = s.scan_active_chromium(app_name="nomatch")
    assert r["results"] == []
    assert r["total_elements"] == 0


# ── 单例 ────────────────────────────────────────────────────────────────────

def test_get_cdp_scanner_singleton(monkeypatch):
    monkeypatch.setattr(cdp_mod, "_scanner", None)
    s1 = cdp_mod.get_cdp_scanner()
    s2 = cdp_mod.get_cdp_scanner()
    assert s1 is s2
