"""web_search 测试（mock HTTP；覆盖解析/净化/fallback 链）"""
from unittest.mock import MagicMock, patch

import pytest

from infra.tool_manager.tools import web_search as ws


# ── _regex_parse（纯逻辑）───────────────────────────────────────────────────

def test_regex_parse_extracts_results():
    html = """
    <a class="result__a" href="https://example.com/?uddg=https%3A%2F%2Ftarget.com%2Fpage">标题一</a>
    <a class="result__snippet">第一段摘要</a>
    <a class="result__a" href="https://example.com/direct">标题二</a>
    """
    results = ws._regex_parse(html, limit=5)
    assert len(results) == 2
    assert results[0]["title"] == "标题一"
    assert results[0]["url"] == "https://target.com/page"  # uddg 解码
    assert results[0]["snippet"] == "第一段摘要"
    assert results[1]["url"] == "https://example.com/direct"


def test_regex_parse_limit():
    html = '<a class="result__a" href="/a">A</a><a class="result__a" href="/b">B</a><a class="result__a" href="/c">C</a>'
    assert len(ws._regex_parse(html, limit=2)) == 2


def test_regex_parse_empty():
    assert ws._regex_parse("<html>无结果</html>", 5) == []


# ── _parse_html_results（HTML 解析器）───────────────────────────────────────

def test_parse_html_results():
    html = '<a class="result__a" href="/x">结果</a>'
    r = ws._parse_html_results(html, limit=5)
    assert isinstance(r, list)


# ── _sanitize_web_content（净化）────────────────────────────────────────────

def test_sanitize_removes_markdown():
    assert ws._sanitize_web_content("[链接](http://x) *强调*") == "链接 强调"


def test_sanitize_truncates():
    text = "x" * 100
    out = ws._sanitize_web_content(text, max_chars=10)
    assert out.endswith("[已截断]")
    assert len(out) < 25  # 截断 + "..." + "[已截断]" 后缀


def test_sanitize_empty():
    assert ws._sanitize_web_content("") == ""
    assert ws._sanitize_web_content("   ") == ""


# ── _search_ddg_html（mock HTTP）────────────────────────────────────────────

class _Resp:
    def __init__(self, text="", status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")


def test_search_ddg_html_success():
    html = '<a class="result__a" href="/r1">结果1</a><a class="result__snippet">摘要</a>'
    with patch("requests.post", return_value=_Resp(text=html)):
        r = ws._search_ddg_html("python", 5)
    assert len(r) >= 1
    assert r[0]["title"] == "结果1"


def test_search_ddg_html_rate_limited():
    with patch("requests.post", return_value=_Resp(status=202)):
        with pytest.raises(Exception):
            ws._search_ddg_html("python", 5)  # 202 限流应抛 HTTPError


# ── web_search 主入口（fallback 链）─────────────────────────────────────────

def test_web_search_empty_query():
    import asyncio
    r = asyncio.run(ws.web_search("   "))
    assert "error" in r


def test_web_search_uses_ddg(monkeypatch):
    import asyncio
    async def fake_fetch(results, max_fetch=3):
        return results
    monkeypatch.setattr(ws, "_check_ddg_reachable", lambda: True)
    monkeypatch.setattr(ws, "_search_ddg_html", lambda q, l: [{"title": "t", "url": "u", "snippet": "s"}])
    monkeypatch.setattr(ws, "_fetch_results_content", fake_fetch)
    monkeypatch.setattr(ws, "_sanitize_web_content", lambda t, max_chars=300: t)
    r = asyncio.run(ws.web_search("hello", fetch_content=False))
    assert r["source"] == "ddg_html"
    assert r["results_count"] == 1


def test_web_search_fallback_when_ddg_down(monkeypatch):
    import asyncio
    async def fake_fetch(results, max_fetch=3):
        return results
    monkeypatch.setattr(ws, "_check_ddg_reachable", lambda: True)
    monkeypatch.setattr(ws, "_search_ddg_html", lambda q, l: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(ws, "_search_ddg_lite", lambda q, l: [{"title": "t2", "url": "u2", "snippet": ""}])
    monkeypatch.setattr(ws, "_fetch_results_content", fake_fetch)
    monkeypatch.setattr(ws, "_sanitize_web_content", lambda t, max_chars=300: t)
    r = asyncio.run(ws.web_search("hello", fetch_content=False))
    assert r["source"] == "ddg_lite"
    assert r["results_count"] == 1


def test_web_search_all_fail(monkeypatch):
    import asyncio
    monkeypatch.setattr(ws, "_check_ddg_reachable", lambda: False)
    monkeypatch.setattr(ws, "_search_sogou", lambda q, l: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(ws, "_search_bing_cn", lambda q, l: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(ws, "_search_baidu", lambda q, l: (_ for _ in ()).throw(RuntimeError("x")))
    r = asyncio.run(ws.web_search("hello", fetch_content=False))
    assert "error" in r
    assert "所有搜索端点均失败" in r["error"]


def test_web_search_limit_clamped():
    import asyncio
    captured = {}

    def fake_search(q, l):
        captured["limit"] = l
        return [{"title": "t", "url": "u", "snippet": "s"}]

    with patch.object(ws, "_check_ddg_reachable", lambda: True), \
         patch.object(ws, "_search_ddg_html", fake_search), \
         patch.object(ws, "_fetch_results_content", lambda results, max_fetch=3: results), \
         patch.object(ws, "_sanitize_web_content", lambda t, max_chars=300: t):
        asyncio.run(ws.web_search("q", limit=999))
    assert captured["limit"] <= 20  # clamp
