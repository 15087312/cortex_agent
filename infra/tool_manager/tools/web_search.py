"""
网页搜索工具 — 多引擎自动 fallback

搜索链路（按优先级）：
1. DuckDuckGo HTML 搜索（海外可访问时）
2. DuckDuckGo lite 搜索
3. DuckDuckGo 零点击 API
4. 搜狗搜索（国内主力，中文查询质量高）
5. 必应中国 cn.bing.com（国内可用，英文查询质量高）
6. 百度搜索（国内兜底，可能触发验证码）
"""
import json
import re
import time
import asyncio
import requests
from typing import Dict, Any, List, Optional
from html.parser import HTMLParser

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("web_search")

SEARCH_TIMEOUT = 8
MAX_RETRIES = 1
_CONNECTIVITY_TIMEOUT = 3  # 连通性检测超时

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
}


# ── HTML 解析器（替代脆弱的正则） ──

class _DDGHTMLParser(HTMLParser):
    """解析 DuckDuckGo HTML 搜索结果"""

    def __init__(self):
        super().__init__()
        self.results: List[Dict[str, str]] = []
        self._current: Dict[str, str] = {}
        self._in_result_link = False
        self._in_snippet = False
        self._in_title = False
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")

        # 结果链接: <a class="result__a" href="...">
        if tag == "a" and "result__a" in cls:
            self._in_result_link = True
            href = attrs_dict.get("href", "")
            # DuckDuckGo 有时用 //duckduckgo.com/l/?uddg=... 重定向
            if "uddg=" in href:
                import urllib.parse
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                href = parsed.get("uddg", [href])[0]
            self._current = {"url": href, "title": "", "snippet": ""}

        # 摘要: <a class="result__snippet"> 或 <td class="result-snippet">
        if tag == "a" and "result__snippet" in cls:
            self._in_snippet = True
        if tag == "td" and "result-snippet" in cls:
            self._in_snippet = True

    def handle_endtag(self, tag):
        if tag == "a" and self._in_result_link:
            self._in_result_link = False
        if self._in_snippet and tag in ("a", "td"):
            self._in_snippet = False
            # 一条结果完成
            if self._current.get("url"):
                self.results.append(self._current)
                self._current = {}

    def handle_data(self, data):
        if self._in_result_link:
            self._current["title"] = self._current.get("title", "") + data
        if self._in_snippet:
            self._current["snippet"] = self._current.get("snippet", "") + data


class _DDGHTMLFallbackParser(HTMLParser):
    """解析 DuckDuckGo 主站 HTML（备用解析器）"""

    def __init__(self):
        super().__init__()
        self.results: List[Dict[str, str]] = []
        self._current: Dict[str, str] = {}
        self._in_link = False
        self._in_snippet = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")
        data_attrs = attrs_dict.get("data-testid", "")

        # 结果链接
        if tag == "a" and ("result__a" in cls or data_attrs == "result-title-a"):
            self._in_link = True
            href = attrs_dict.get("href", "")
            if "uddg=" in href:
                import urllib.parse
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                href = parsed.get("uddg", [href])[0]
            self._current = {"url": href, "title": "", "snippet": ""}

        # 摘要
        if tag in ("span", "td", "div") and ("result__snippet" in cls or "snippet" in cls.lower()):
            self._in_snippet = True

    def handle_endtag(self, tag):
        if tag == "a" and self._in_link:
            self._in_link = False
        if self._in_snippet and tag in ("span", "td", "div"):
            self._in_snippet = False
            if self._current.get("url") and self._current.get("title"):
                self.results.append(self._current)
                self._current = {}

    def handle_data(self, data):
        if self._in_link:
            self._current["title"] = self._current.get("title", "") + data
        if self._in_snippet:
            self._current["snippet"] = self._current.get("snippet", "") + data


def _parse_html_results(html: str, limit: int, parser_class=None) -> List[Dict[str, str]]:
    """用 HTML 解析器提取搜索结果"""
    parser = (parser_class or _DDGHTMLParser)()
    try:
        parser.feed(html)
    except Exception as e:
        logger.debug(f"HTML 解析异常: {e}")
    return parser.results[:limit]


# ── 正则 fallback（HTML 解析器失败时兜底） ──

def _regex_parse(html: str, limit: int) -> List[Dict[str, str]]:
    """正则兜底解析"""
    results = []
    # 匹配结果链接
    links = re.findall(
        r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        html, re.DOTALL,
    )
    snippets = re.findall(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        html, re.DOTALL,
    )
    for i, (url, title) in enumerate(links[:limit]):
        if "uddg=" in url:
            import urllib.parse
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            url = parsed.get("uddg", [url])[0]
        snippet = ""
        if i < len(snippets):
            snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()
        results.append({
            "title": re.sub(r"<[^>]+>", "", title).strip(),
            "url": url,
            "snippet": snippet,
        })
    return results


# ── 搜索后端 ──

def _search_ddg_html(query: str, limit: int) -> List[Dict[str, str]]:
    """DuckDuckGo HTML 搜索（主）"""
    resp = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query, "b": ""},
        headers=_HEADERS,
        timeout=SEARCH_TIMEOUT,
        allow_redirects=True,
    )
    # 202 = 限流页面（无搜索结果），当作失败让 fallback 接管
    if resp.status_code == 202 or resp.status_code >= 400:
        raise requests.exceptions.HTTPError(f"HTTP {resp.status_code}")
    resp.raise_for_status()
    results = _parse_html_results(resp.text, limit)
    if not results:
        results = _regex_parse(resp.text, limit)
    return results


def _search_ddg_lite(query: str, limit: int) -> List[Dict[str, str]]:
    """DuckDuckGo lite 搜索（备）"""
    resp = requests.post(
        "https://lite.duckduckgo.com/lite/",
        data={"q": query},
        headers=_HEADERS,
        timeout=SEARCH_TIMEOUT,
        allow_redirects=True,
    )
    if resp.status_code == 202 or resp.status_code >= 400:
        raise requests.exceptions.HTTPError(f"HTTP {resp.status_code}")
    resp.raise_for_status()
    results = _parse_html_results(resp.text, limit)
    if not results:
        results = _regex_parse(resp.text, limit)
    return results


def _search_ddg_api(query: str, limit: int) -> List[Dict[str, str]]:
    """DuckDuckGo 零点击 API（兜底）"""
    resp = requests.get(
        "https://api.duckduckgo.com/",
        params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
        headers=_HEADERS,
        timeout=SEARCH_TIMEOUT,
    )
    if resp.status_code == 202 or resp.status_code >= 400:
        raise requests.exceptions.HTTPError(f"HTTP {resp.status_code}")
    resp.raise_for_status()
    data = resp.json()
    results = []
    if data.get("AbstractText"):
        results.append({
            "title": data.get("Heading", ""),
            "url": data.get("AbstractURL", ""),
            "snippet": data.get("AbstractText", ""),
        })
    for topic in data.get("RelatedTopics", [])[:limit]:
        if isinstance(topic, dict) and "Text" in topic:
            results.append({
                "title": topic.get("Text", "").split(" - ")[0][:80],
                "url": topic.get("FirstURL", ""),
                "snippet": topic.get("Text", ""),
            })
    return results[:limit]


# ── 搜狗搜索（国内主力，中文查询质量高） ──

class _SogouParser(HTMLParser):
    """解析搜狗搜索结果（www.sogou.com/web）"""

    def __init__(self):
        super().__init__()
        self.results: List[Dict[str, str]] = []
        self._current: Optional[Dict[str, str]] = None
        self._in_title = False
        self._in_abstract = False
        self._capture_text = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")

        # 结果标题: <h3> 内的 <a>
        if tag == "h3":
            self._in_title = True
            self._capture_text = ""
            self._current = {"url": "", "title": "", "snippet": ""}
        if tag == "a" and self._in_title and self._current is not None:
            href = attrs_dict.get("href", "")
            if href:
                self._current["url"] = href

        # 摘要: <p class="str_info"> 或 <div class="space-txt">
        if tag in ("p", "div") and ("str_info" in cls or "space-txt" in cls or "star-wiki" in cls):
            self._in_abstract = True
            self._capture_text = ""

    def handle_endtag(self, tag):
        if tag == "h3" and self._in_title:
            self._in_title = False
            if self._current is not None:
                self._current["title"] = self._capture_text.strip()
        if tag in ("p", "div") and self._in_abstract:
            self._in_abstract = False
            if self._current is not None:
                self._current["snippet"] = self._capture_text.strip()
            # 摘要结束后，一条结果完成
            if self._current and self._current.get("title"):
                self.results.append(self._current)
                self._current = None

    def handle_data(self, data):
        if self._in_title or self._in_abstract:
            self._capture_text += data


def _search_sogou(query: str, limit: int) -> List[Dict[str, str]]:
    """搜狗搜索（国内主力，中文查询质量高，不触发验证码）"""
    resp = requests.get(
        "https://www.sogou.com/web",
        params={"query": query},
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
        timeout=SEARCH_TIMEOUT,
        allow_redirects=True,
    )
    if resp.status_code >= 400:
        raise requests.exceptions.HTTPError(f"HTTP {resp.status_code}")

    # 检查验证码
    if "antispider" in resp.url.lower() or "验证码" in resp.text[:2000]:
        raise RuntimeError("搜狗返回验证码页面")

    parser = _SogouParser()
    try:
        parser.feed(resp.text)
    except Exception as e:
        logger.debug(f"搜狗 HTML 解析异常，回退正则提取: {e}")
    results = parser.results[:limit]

    # 兜底：正则提取
    if not results:
        h3_links = re.findall(
            r'<h3[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?</h3>',
            resp.text, re.DOTALL,
        )
        # 尝试提取摘要
        snippets = re.findall(r'<p[^>]*class="[^"]*str_info[^"]*"[^>]*>(.*?)</p>', resp.text, re.DOTALL)
        for i, (url, title) in enumerate(h3_links[:limit]):
            title = re.sub(r"<[^>]+>", "", title).strip()
            snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip() if i < len(snippets) else ""
            if title:
                results.append({"title": title, "url": url, "snippet": snippet})

    return results


# ── 必应中国搜索（国内可用，结果质量高） ──

class _BingCNParser(HTMLParser):
    """解析 cn.bing.com 搜索结果

    只采集 <ol id="b_results"> 内的 b_algo 项，跳过字典卡片等侧边栏结果。
    """

    def __init__(self):
        super().__init__()
        self.results: List[Dict[str, str]] = []
        self._current: Optional[Dict[str, str]] = None
        self._in_title_link = False
        self._in_snippet = False
        self._capture_text = ""
        self._in_results_list = False  # 是否在 <ol id="b_results"> 内

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")
        elem_id = attrs_dict.get("id", "")

        # 主结果列表: <ol id="b_results">
        if tag == "ol" and elem_id == "b_results":
            self._in_results_list = True

        # 结果容器: <li class="b_algo">（仅在主列表内）
        if tag == "li" and "b_algo" in cls and self._in_results_list:
            self._current = {"url": "", "title": "", "snippet": ""}

        # 标题链接: <h2><a href="...">title</a></h2>
        if tag == "h2" and self._current is not None:
            self._in_title_link = True
            self._capture_text = ""
        if tag == "a" and self._in_title_link and self._current is not None:
            href = attrs_dict.get("href", "")
            if href:
                self._current["url"] = href

        # 摘要: <p class="b_lineclamp2 ..."> 或 <div class="b_caption"><p>
        if tag == "p" and ("b_lineclamp" in cls or "b_paractl" in cls):
            self._in_snippet = True
            self._capture_text = ""

    def handle_endtag(self, tag):
        if tag == "a" and self._in_title_link:
            self._in_title_link = False
            if self._current is not None:
                self._current["title"] = self._capture_text.strip()
        if tag == "p" and self._in_snippet:
            self._in_snippet = False
            if self._current is not None:
                self._current["snippet"] = self._capture_text.strip()
        # 一条结果结束（仅在主列表内）
        if tag == "li" and self._current and self._current.get("title"):
            self.results.append(self._current)
            self._current = None
        # 离开主结果列表
        if tag == "ol" and self._in_results_list:
            self._in_results_list = False

    def handle_data(self, data):
        if self._in_title_link or self._in_snippet:
            self._capture_text += data


def _search_bing_cn(query: str, limit: int) -> List[Dict[str, str]]:
    """必应中国搜索（cn.bing.com，国内可访问，不触发验证码）"""
    resp = requests.get(
        "https://cn.bing.com/search",
        params={"q": query, "count": limit},
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
        timeout=SEARCH_TIMEOUT,
        allow_redirects=True,
    )
    if resp.status_code >= 400:
        raise requests.exceptions.HTTPError(f"HTTP {resp.status_code}")

    # 检查是否被重定向到验证码页面
    if "captcha" in resp.url.lower() or "challenge" in resp.url.lower():
        raise RuntimeError("必应返回验证码页面")

    parser = _BingCNParser()
    try:
        parser.feed(resp.text)
    except Exception as e:
        logger.debug(f"必应 HTML 解析异常，回退正则提取: {e}")
    results = parser.results[:limit]

    # 兜底：正则提取
    if not results:
        items = re.findall(
            r'<li class="b_algo"[^>]*>.*?<h2[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?</h2>(.*?)</li>',
            resp.text, re.DOTALL,
        )
        for url, title, body in items[:limit]:
            title = re.sub(r"<[^>]+>", "", title).strip()
            snippet_m = re.search(r'<p[^>]*>(.*?)</p>', body, re.DOTALL)
            snippet = re.sub(r"<[^>]+>", "", snippet_m.group(1)).strip() if snippet_m else ""
            if title:
                results.append({"title": title, "url": url, "snippet": snippet})

    return results


# ── 百度搜索（国内 fallback） ──

class _BaiduParser(HTMLParser):
    """解析百度搜索结果"""

    def __init__(self):
        super().__init__()
        self.results: List[Dict[str, str]] = []
        self._current: Dict[str, str] = {}
        self._in_title = False
        self._in_abstract = False
        self._capture_text = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")

        # 结果容器: <div class="result c-container ...">
        if tag == "div" and "result" in cls and "c-container" in cls:
            self._current = {"url": "", "title": "", "snippet": ""}

        # 标题链接: <h3 class="t"> 内的 <a>
        if tag == "h3" and "t" in cls.split():
            self._in_title = True
            self._capture_text = ""
        if tag == "a" and self._in_title:
            href = attrs_dict.get("href", "")
            if href and self._current is not None:
                self._current["url"] = href

        # 摘要: <span class="content-right_8Zs40"> 或 <div class="c-abstract">
        if tag in ("span", "div") and ("content-right" in cls or "c-abstract" in cls):
            self._in_abstract = True
            self._capture_text = ""

    def handle_endtag(self, tag):
        if tag == "a" and self._in_title:
            self._in_title = False
            if self._current is not None:
                self._current["title"] = self._capture_text.strip()
        if tag in ("span", "div") and self._in_abstract:
            self._in_abstract = False
            if self._current is not None:
                self._current["snippet"] = self._capture_text.strip()
        # 一条结果结束
        if tag == "div" and self._current and self._current.get("title"):
            self.results.append(self._current)
            self._current = {}

    def handle_data(self, data):
        if self._in_title or self._in_abstract:
            self._capture_text += data


def _search_baidu(query: str, limit: int) -> List[Dict[str, str]]:
    """百度搜索（国内 fallback）"""
    resp = requests.get(
        "https://www.baidu.com/s",
        params={"wd": query, "rn": limit, "ie": "utf-8"},
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
        timeout=SEARCH_TIMEOUT,
        allow_redirects=True,
    )
    if resp.status_code >= 400:
        raise requests.exceptions.HTTPError(f"HTTP {resp.status_code}")

    # 优先用 HTMLParser
    parser = _BaiduParser()
    try:
        parser.feed(resp.text)
    except Exception as e:
        logger.debug(f"百度 HTML 解析异常，回退正则提取: {e}")
    results = parser.results[:limit]

    # 兜底：正则提取
    if not results:
        # 百度结果标题在 <h3> 内的 <a> 中
        titles = re.findall(r'<h3[^>]*class="t"[^>]*>.*?<a[^>]*>(.*?)</a>', resp.text, re.DOTALL)
        urls = re.findall(r'<h3[^>]*class="t"[^>]*>.*?<a[^>]*href="([^"]*)"', resp.text, re.DOTALL)
        snippets = re.findall(r'<span[^>]*class="content-right[^"]*"[^>]*>(.*?)</span>', resp.text, re.DOTALL)
        for i in range(min(len(titles), limit)):
            title = re.sub(r"<[^>]+>", "", titles[i]).strip()
            url = urls[i] if i < len(urls) else ""
            snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip() if i < len(snippets) else ""
            if title:
                results.append({"title": title, "url": url, "snippet": snippet})

    return results


# ── 连通性检测（避免 DuckDuckGo 不可达时白等） ──

_ddg_reachable: Optional[bool] = None  # 缓存检测结果

def _check_ddg_reachable() -> bool:
    """快速检测 DuckDuckGo 是否可达（结果缓存 5 分钟）"""
    global _ddg_reachable
    if _ddg_reachable is not None:
        return _ddg_reachable
    try:
        requests.head(
            "https://html.duckduckgo.com/",
            timeout=_CONNECTIVITY_TIMEOUT,
            headers=_HEADERS,
        )
        _ddg_reachable = True
    except Exception:
        _ddg_reachable = False
    return _ddg_reachable


# ── 页面内容抓取 ──

_FETCH_TIMEOUT = 10
_MAX_CONTENT_LEN = 3000  # 每条结果最多保留字符数


_sogou_session: Optional[requests.Session] = None


def _get_sogou_session() -> requests.Session:
    """获取带搜狗 cookie 的 session（懒初始化，缓存复用）"""
    global _sogou_session
    if _sogou_session is None:
        _sogou_session = requests.Session()
        _sogou_session.headers.update(_HEADERS)
        try:
            _sogou_session.get("https://www.sogou.com/web", params={"query": "test"}, timeout=5)
        except Exception as e:
            logger.debug(f"搜狗预热请求失败 (非致命): {e}")
    return _sogou_session


# ── crawl4ai 页面抓取（无头浏览器，能执行 JS） ──

_crawler = None


async def _get_crawler():
    """获取或创建全局 Crawler 实例（复用浏览器进程）"""
    global _crawler
    if _crawler is None:
        from crawl4ai import AsyncWebCrawler
        _crawler = AsyncWebCrawler()
        await _crawler.__aenter__()
    return _crawler


async def close_crawler():
    """关闭全局 Crawler（应用退出时调用）"""
    global _crawler
    if _crawler is not None:
        try:
            await _crawler.__aexit__(None, None, None)
        except Exception as e:
            logger.debug(f"crawl4ai 关闭失败 (非致命): {e}")
        _crawler = None


async def _crawl_url(url: str) -> Optional[str]:
    """用 crawl4ai 抓取单个页面，返回 markdown 正文"""
    try:
        crawler = await _get_crawler()
        result = await crawler.arun(url=url)
        if result and result.success and result.markdown:
            md = result.markdown
            if len(md) > _MAX_CONTENT_LEN:
                md = md[:_MAX_CONTENT_LEN] + "..."
            return md
    except Exception as e:
        logger.debug(f"crawl4ai 抓取失败 {url[:60]}: {e}")
    return None


def _fetch_page_content(url: str) -> Optional[str]:
    """抓取页面正文（crawl4ai 无头浏览器 → fallback requests）"""
    if not url:
        return None
    # 处理相对路径（搜狗等）
    if url.startswith("/"):
        url = "https://www.sogou.com" + url
    if not url.startswith(("http://", "https://")):
        return None

    # 1. crawl4ai（能处理 JS 重定向）
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 已有事件循环（如 aiohttp 环境），用线程池隔离
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(asyncio.run, _crawl_url(url))
                text = future.result(timeout=30)
        else:
            text = loop.run_until_complete(_crawl_url(url))
        if text:
            return text
    except Exception as e:
        logger.debug(f"crawl4ai 失败，fallback requests: {e}")

    # 2. fallback: requests 直接抓取 + 去标签
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_FETCH_TIMEOUT, allow_redirects=True)
        if resp.status_code >= 400:
            return None
        if resp.url.rstrip("/") == "https://www.sogou.com":
            return None
        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            return None
        text = _extract_text_from_html(resp.text)
        if len(text) > _MAX_CONTENT_LEN:
            text = text[:_MAX_CONTENT_LEN] + "..."
        return text if text else None
    except Exception as e:
        logger.debug(f"网页内容抓取失败: {e}")
        return None


def _extract_text_from_html(html: str) -> str:
    """从 HTML 提取纯文本（去标签、去脚本/样式）"""
    # 移除 script / style / nav / header / footer
    cleaned = re.sub(
        r"<(script|style|nav|header|footer|noscript)[^>]*>.*?</\1>",
        "", html, flags=re.DOTALL | re.IGNORECASE,
    )
    # 移除所有标签
    text = re.sub(r"<[^>]+>", " ", cleaned)
    # 解码 HTML 实体
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'")
    # 压缩空白
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _fetch_results_content(results: List[Dict[str, str]], max_fetch: int = 3) -> List[Dict[str, str]]:
    """批量抓取搜索结果的页面内容（前 max_fetch 条）"""
    fetched = 0
    for result in results:
        if fetched >= max_fetch:
            break
        url = result.get("url", "")
        if not url:
            continue
        content = _fetch_page_content(url)
        if content:
            result["content"] = content
            fetched += 1
    return results


# ── 主入口 ──

@ToolRegistry.register(
    "web_search",
    description=(
        "搜索互联网并自动抓取页面正文，返回标题+链接+摘要+全文(markdown)。"
        "使用 crawl4ai 无头浏览器抓取，能处理 JS 重定向。"
        "自动选择搜索引擎（DuckDuckGo/搜狗/必应/百度），无需 API key。"
        "用于查找在线信息、文档、解决方案等。"
    ),
    params={
        "query": "搜索关键词",
        "limit": "可选，返回结果数量（默认5，最大20）",
        "fetch_content": "可选，是否抓取页面正文（默认true）。设为false则只返回标题和摘要，速度更快",
    },
    risk_level="LOW",
    category="query",
    core=True,
)
async def web_search(
    query: str,
    limit: Optional[int] = 5,
    fetch_content: Optional[bool] = True,
) -> Dict[str, Any]:
    """搜索互联网 — 多端点自动 fallback，可选抓取页面正文（异步包装）"""
    def _sync_search():
        if not query or not query.strip():
            return {"error": "搜索关键词不能为空"}

        try:
            _limit = int(limit) if limit is not None else 5
        except (ValueError, TypeError):
            _limit = 5
        _limit = max(1, min(_limit, 20))

        _fetch = fetch_content
        if isinstance(_fetch, str):
            _fetch = _fetch.lower() not in ("false", "0", "no")

        errors = []

        # 快速检测 DuckDuckGo 是否可达
        ddg_ok = _check_ddg_reachable()

        def _finalize(results: List[Dict[str, str]], source: str) -> Dict[str, Any]:
            if _fetch:
                results = _fetch_results_content(results)
            return {
                "query": query,
                "results_count": len(results),
                "results": results,
                "source": source,
            }

        if ddg_ok:
            for attempt in range(MAX_RETRIES):
                try:
                    results = _search_ddg_html(query, _limit)
                    if results:
                        return _finalize(results, "ddg_html")
                    break
                except Exception as e:
                    errors.append(f"ddg_html: {e}")
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(1)

            try:
                results = _search_ddg_lite(query, _limit)
                if results:
                    return _finalize(results, "ddg_lite")
            except Exception as e:
                errors.append(f"ddg_lite: {e}")

            try:
                results = _search_ddg_api(query, _limit)
                if results:
                    return _finalize(results, "ddg_api")
            except Exception as e:
                errors.append(f"ddg_api: {e}")
        else:
            errors.append("ddg: 不可达，跳过")

        try:
            results = _search_sogou(query, _limit)
            if results:
                return _finalize(results, "sogou")
        except Exception as e:
            errors.append(f"sogou: {e}")

        try:
            results = _search_bing_cn(query, _limit)
            if results:
                return _finalize(results, "bing_cn")
        except Exception as e:
            errors.append(f"bing_cn: {e}")

        try:
            results = _search_baidu(query, _limit)
            if results:
                return _finalize(results, "baidu")
        except Exception as e:
            errors.append(f"baidu: {e}")

        return {"error": f"所有搜索端点均失败: {'; '.join(errors)}", "query": query}

    return await asyncio.to_thread(_sync_search)
