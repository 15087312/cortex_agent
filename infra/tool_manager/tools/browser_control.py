"""
浏览器控制 — CDP 直驱（抄 Cherry Studio 思路，Python playwright 实现）

设计原则（用户要求：低成本快捷调用，不用截屏识别）：
- 用 playwright 的 selector / role 定位元素，不截屏、不走视觉模型
- snapshot 返回元素引用（e1/e2...），后续动作直接用 ref，快且便宜
- execute 透传任意 JS，兜底一切复杂操作

动作集（对标 Cherry 23 个，取核心 12 个）：
  open / snapshot / click / type / press_key / select_option /
  hover / scroll / go_back / go_forward / wait_for / execute / screenshot / close
"""
from typing import Any, Dict, List, Optional

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("browser_control")

# ── 会话单例（一个 browser，多 page 按需开）──

_playwright = None
_browser = None
_pages: Dict[str, Any] = {}
_ref_map: Dict[str, Dict[str, str]] = {}  # page_id -> {ref: selector}


def _ensure_browser():
    """惰性启动 chromium（headless）"""
    global _playwright, _browser
    if _browser is not None:
        return _browser
    from playwright.sync_api import sync_playwright
    _playwright = sync_playwright().start()
    _browser = _playwright.chromium.launch(headless=True)
    logger.info("[Browser] chromium 已启动 (headless)")
    return _browser


def _get_page(page_id: str = "default"):
    browser = _ensure_browser()
    if page_id not in _pages or _pages[page_id].is_closed():
        _pages[page_id] = browser.new_page()
    return _pages[page_id]


def _build_snapshot(page, page_id: str, max_nodes: int = 100) -> str:
    """DOM 快照：可交互元素 → eN 引用（抄 Cherry snapshot 思路）"""
    js = """() => {
        const out = [];
        let n = 0;
        const sel = 'a, button, input, select, textarea, [role=button], [role=link], [role=textbox]';
        for (const el of document.querySelectorAll(sel)) {
            if (n >= 100) break;
            const r = el.getBoundingClientRect();
            if (r.width === 0 && r.height === 0) continue;
            const tag = el.tagName.toLowerCase();
            const text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').trim().slice(0, 60);
            const role = el.getAttribute('role') || tag;
            n++;
            out.push({ref: 'e' + n, role, text, tag,
                selector: el.id ? '#' + el.id : (el.name ? `[name="${el.name}"]` : null)});
            if (!el.id && !el.name) {
                el.setAttribute('data-ll-ref', 'e' + n);
            }
        }
        return out;
    }"""
    try:
        nodes = page.evaluate(js)
    except Exception as e:
        return f"(snapshot 失败: {e})"
    _ref_map[page_id] = {}
    lines = []
    for nd in nodes[:max_nodes]:
        ref = nd["ref"]
        sel = nd["selector"] or f'[data-ll-ref="{ref}"]'
        _ref_map[page_id][ref] = sel
        lines.append(f'{ref} [{nd["role"]}] "{nd["text"]}"')
    return "\n".join(lines) if lines else "(页面无可交互元素)"


def _resolve(page_id: str, ref_or_selector: str) -> str:
    """ref（如 e3）→ selector；否则原样当 selector 用"""
    if ref_or_selector in _ref_map.get(page_id, {}):
        return _ref_map[page_id][ref_or_selector]
    return ref_or_selector


@ToolRegistry.register(
    "browser_open",
    description="浏览器打开 URL（CDP 直驱，无需截屏识别）。返回页面标题+快照。",
    params={"url": "目标 URL", "page_id": "可选，页面标识（多标签用），默认 default"},
    risk_level="MEDIUM",
    category="browser",
)
def browser_open(url: str, page_id: str = "default") -> Dict[str, Any]:
    try:
        page = _get_page(page_id)
        page.goto(url, timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        snap = _build_snapshot(page, page_id)
        return {"title": page.title(), "url": page.url, "snapshot": snap}
    except Exception as e:
        logger.error(f"[Browser] open 失败 {url}: {e}")
        return {"error": str(e)}


@ToolRegistry.register(
    "browser_snapshot",
    description="取当前页面可交互元素快照（eN 引用，供 click/type 等用）。",
    params={"page_id": "可选，默认 default"},
    risk_level="LOW",
    category="browser",
)
def browser_snapshot(page_id: str = "default") -> Dict[str, Any]:
    try:
        page = _get_page(page_id)
        return {"url": page.url, "title": page.title(), "snapshot": _build_snapshot(page, page_id)}
    except Exception as e:
        return {"error": str(e)}


@ToolRegistry.register(
    "browser_click",
    description="点击元素（用 snapshot 的 eN 引用或 CSS selector，无需坐标）。",
    params={"target": "eN 引用或 CSS selector", "page_id": "可选"},
    risk_level="MEDIUM",
    category="browser",
)
def browser_click(target: str, page_id: str = "default") -> Dict[str, Any]:
    try:
        page = _get_page(page_id)
        page.click(_resolve(page_id, target), timeout=10000)
        return {"ok": True, "snapshot": _build_snapshot(page, page_id)}
    except Exception as e:
        return {"error": str(e)}


@ToolRegistry.register(
    "browser_type",
    description="向输入框输入文本（先清空再输入，可选回车提交）。",
    params={
        "target": "eN 引用或 CSS selector",
        "text": "输入文本",
        "submit": "可选，输入后回车（默认 false）",
        "page_id": "可选",
    },
    risk_level="MEDIUM",
    category="browser",
)
def browser_type(target: str, text: str, submit: bool = False, page_id: str = "default") -> Dict[str, Any]:
    try:
        page = _get_page(page_id)
        loc = page.locator(_resolve(page_id, target))
        loc.fill(text, timeout=10000)
        if submit:
            loc.press("Enter")
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        return {"ok": True, "snapshot": _build_snapshot(page, page_id)}
    except Exception as e:
        return {"error": str(e)}


@ToolRegistry.register(
    "browser_press_key",
    description="按键（如 Enter / Escape / Tab / ArrowDown 等）。",
    params={"key": "键名", "page_id": "可选"},
    risk_level="LOW",
    category="browser",
)
def browser_press_key(key: str, page_id: str = "default") -> Dict[str, Any]:
    try:
        page = _get_page(page_id)
        page.keyboard.press(key)
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


@ToolRegistry.register(
    "browser_select",
    description="下拉框选值。",
    params={"target": "eN 或 selector", "values": "选项值（字符串或数组）", "page_id": "可选"},
    risk_level="MEDIUM",
    category="browser",
)
def browser_select(target: str, values: Any, page_id: str = "default") -> Dict[str, Any]:
    try:
        page = _get_page(page_id)
        if isinstance(values, str):
            values = [values]
        page.locator(_resolve(page_id, target)).select_option(values, timeout=10000)
        return {"ok": True, "snapshot": _build_snapshot(page, page_id)}
    except Exception as e:
        return {"error": str(e)}


@ToolRegistry.register(
    "browser_scroll",
    description="滚动页面（pages：页数，正数向下）。",
    params={"pages": "滚动页数，默认 1", "page_id": "可选"},
    risk_level="LOW",
    category="browser",
)
def browser_scroll(pages: float = 1.0, page_id: str = "default") -> Dict[str, Any]:
    try:
        page = _get_page(page_id)
        page.evaluate(f"window.scrollBy(0, window.innerHeight * {float(pages)})")
        return {"ok": True, "snapshot": _build_snapshot(page, page_id)}
    except Exception as e:
        return {"error": str(e)}


@ToolRegistry.register(
    "browser_navigate",
    description="后退 / 前进。",
    params={"direction": "back 或 forward", "page_id": "可选"},
    risk_level="LOW",
    category="browser",
)
def browser_navigate(direction: str, page_id: str = "default") -> Dict[str, Any]:
    try:
        page = _get_page(page_id)
        if direction == "back":
            page.go_back(timeout=15000)
        else:
            page.go_forward(timeout=15000)
        return {"ok": True, "url": page.url, "snapshot": _build_snapshot(page, page_id)}
    except Exception as e:
        return {"error": str(e)}


@ToolRegistry.register(
    "browser_wait",
    description="等待文本出现 / 元素出现 / 消失（抄 Cherry wait_for）。",
    params={
        "text": "可选，等待的文本",
        "selector": "可选，等待的 selector",
        "gone": "可选，等待消失而非出现",
        "timeout": "可选，秒，默认 10",
        "page_id": "可选",
    },
    risk_level="LOW",
    category="browser",
)
def browser_wait(text: str = "", selector: str = "", gone: bool = False,
                 timeout: int = 10, page_id: str = "default") -> Dict[str, Any]:
    try:
        page = _get_page(page_id)
        ms = int(timeout) * 1000
        if selector:
            state = "detached" if gone else "visible"
            page.locator(selector).wait_for(state=state, timeout=ms)
        elif text:
            if gone:
                page.wait_for_selector(f"text={text}", state="detached", timeout=ms)
            else:
                page.wait_for_selector(f"text={text}", timeout=ms)
        return {"ok": True, "snapshot": _build_snapshot(page, page_id)}
    except Exception as e:
        return {"error": f"等待超时或失败: {e}"}


@ToolRegistry.register(
    "browser_execute",
    description="执行任意 JS 并返回结果（兜底Everything，低成本直调）。",
    params={"code": "JS 代码（表达式或函数体，return 结果）", "page_id": "可选"},
    risk_level="HIGH",
    category="browser",
)
def browser_execute(code: str, page_id: str = "default") -> Dict[str, Any]:
    try:
        page = _get_page(page_id)
        result = page.evaluate(code)
        return {"ok": True, "result": str(result)[:2000]}
    except Exception as e:
        return {"error": str(e)}


@ToolRegistry.register(
    "browser_text",
    description="提取页面正文（去标签，截断）。",
    params={"max_chars": "可选，默认 5000", "page_id": "可选"},
    risk_level="LOW",
    category="browser",
)
def browser_text(max_chars: int = 5000, page_id: str = "default") -> Dict[str, Any]:
    try:
        page = _get_page(page_id)
        text = page.evaluate("() => document.body ? document.body.innerText : ''")
        text = (text or "")[:int(max_chars)]
        return {"url": page.url, "text": text}
    except Exception as e:
        return {"error": str(e)}


@ToolRegistry.register(
    "browser_close",
    description="关闭页面 / 浏览器。",
    params={"page_id": "可选，不传则关闭整个浏览器"},
    risk_level="LOW",
    category="browser",
)
def browser_close(page_id: str = "") -> Dict[str, Any]:
    global _browser, _playwright
    try:
        if page_id and page_id in _pages:
            _pages[page_id].close()
            del _pages[page_id]
            return {"ok": True, "closed": page_id}
        for p in _pages.values():
            try:
                p.close()
            except Exception as _swallowed_err:
                logger.debug(f"[browser_close] ignored Exception: {_swallowed_err}")
        _pages.clear()
        if _browser:
            _browser.close()
            _browser = None
        if _playwright:
            _playwright.stop()
            _playwright = None
        return {"ok": True, "closed": "all"}
    except Exception as e:
        return {"error": str(e)}
