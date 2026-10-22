"""browser_control.py（CDP 直驱浏览器工具）单元测试

mock playwright 边界（不起真浏览器），覆盖：
- open / snapshot / click / type / press_key / select / scroll
- navigate / wait / execute / text / close
- ref 解析（eN → selector）、异常路径
"""
from unittest.mock import MagicMock, patch

import pytest

from infra.tool_manager.tools import browser_control as bc


@pytest.fixture(autouse=True)
def _reset_state():
    bc._pages.clear()
    bc._ref_map.clear()
    bc._browser = None
    bc._playwright = None
    yield
    bc._pages.clear()
    bc._ref_map.clear()
    bc._browser = None
    bc._playwright = None


def _fake_page(url="https://ex.com", title="Ex"):
    page = MagicMock()
    page.url = url
    page.title.return_value = title
    page.is_closed.return_value = False
    page.evaluate.return_value = [
        {"ref": "e1", "role": "button", "text": "登录", "tag": "button", "selector": "#login"},
        {"ref": "e2", "role": "textbox", "text": "", "tag": "input", "selector": None},
    ]
    loc = MagicMock()
    page.locator.return_value = loc
    return page, loc


def _patch_page(page):
    bc._pages["default"] = page
    bc._browser = MagicMock()  # 避免 _ensure_browser 起真浏览器


def test_open_returns_snapshot():
    page, _ = _fake_page()
    with patch.object(bc, "_get_page", return_value=page):
        r = bc.browser_open("https://ex.com")
    assert r["title"] == "Ex"
    assert "e1" in r["snapshot"]
    assert 'button' in r["snapshot"]
    page.goto.assert_called_once()


def test_open_failure_returns_error():
    page, _ = _fake_page()
    page.goto.side_effect = RuntimeError("net down")
    with patch.object(bc, "_get_page", return_value=page):
        r = bc.browser_open("https://ex.com")
    assert "error" in r


def test_snapshot_builds_ref_map():
    page, _ = _fake_page()
    _patch_page(page)
    r = bc.browser_snapshot()
    assert "e1" in r["snapshot"]
    # ref 映射建成：e1 → #login（显式 selector），e2 → data-ll-ref（隐式）
    assert bc._ref_map["default"]["e1"] == "#login"
    assert bc._ref_map["default"]["e2"] == '[data-ll-ref="e2"]'


def test_resolve_uses_ref_map():
    bc._ref_map["default"] = {"e3": "#submit"}
    assert bc._resolve("default", "e3") == "#submit"
    # 非 ref 原样当 selector
    assert bc._resolve("default", ".my-class") == ".my-class"


def test_click_by_ref():
    page, _ = _fake_page()
    _patch_page(page)
    bc._ref_map["default"] = {"e1": "#login"}
    r = bc.browser_click("e1")
    assert r["ok"] is True
    page.click.assert_called_once_with("#login", timeout=10000)


def test_click_failure():
    page, _ = _fake_page()
    page.click.side_effect = RuntimeError("no such element")
    _patch_page(page)
    r = bc.browser_click(".missing")
    assert "error" in r


def test_type_with_submit():
    page, loc = _fake_page()
    _patch_page(page)
    bc._ref_map["default"] = {"e2": "#q"}
    r = bc.browser_type("e2", "hello", submit=True)
    assert r["ok"] is True
    loc.fill.assert_called_once_with("hello", timeout=10000)
    loc.press.assert_called_once_with("Enter")


def test_press_key():
    page, _ = _fake_page()
    _patch_page(page)
    r = bc.browser_press_key("Enter")
    assert r["ok"] is True
    page.keyboard.press.assert_called_once_with("Enter")


def test_select_option_string_to_list():
    page, loc = _fake_page()
    _patch_page(page)
    r = bc.browser_select("#sel", "opt1")
    assert r["ok"] is True
    loc.select_option.assert_called_once_with(["opt1"], timeout=10000)


def test_scroll():
    page, _ = _fake_page()
    _patch_page(page)
    r = bc.browser_scroll(pages=2.0)
    assert r["ok"] is True
    page.evaluate.assert_called()


def test_navigate_back_forward():
    page, _ = _fake_page()
    _patch_page(page)
    assert bc.browser_navigate("back")["ok"] is True
    page.go_back.assert_called_once()
    assert bc.browser_navigate("forward")["ok"] is True
    page.go_forward.assert_called_once()


def test_wait_for_text():
    page, _ = _fake_page()
    _patch_page(page)
    r = bc.browser_wait(text="登录成功", timeout=5)
    assert r["ok"] is True
    page.wait_for_selector.assert_called_once()


def test_wait_timeout_returns_error():
    page, _ = _fake_page()
    page.wait_for_selector.side_effect = RuntimeError("Timeout 5000ms exceeded")
    _patch_page(page)
    r = bc.browser_wait(text="永不出现")
    assert "error" in r


def test_execute_returns_result():
    page, _ = _fake_page()
    page.evaluate.return_value = {"count": 42}
    _patch_page(page)
    r = bc.browser_execute("() => document.querySelectorAll('a').length")
    assert r["ok"] is True


def test_text_extract_truncates():
    page, _ = _fake_page()
    page.evaluate.return_value = "x" * 10000
    _patch_page(page)
    r = bc.browser_text(max_chars=100)
    assert len(r["text"]) == 100


def test_close_single_page():
    page, _ = _fake_page()
    bc._pages["default"] = page
    bc._pages["other"] = MagicMock()
    r = bc.browser_close(page_id="default")
    assert r == {"ok": True, "closed": "default"}
    assert "default" not in bc._pages
    assert "other" in bc._pages


def test_close_all():
    bc._pages["a"] = MagicMock()
    bc._browser = MagicMock()
    bc._playwright = MagicMock()
    r = bc.browser_close()
    assert r == {"ok": True, "closed": "all"}
    assert bc._pages == {}
    assert bc._browser is None
