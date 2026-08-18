"""
网页获取工具 — 通过 HTTP 获取网页内容

对应 Claude Code 的 WebFetchTool。使用 requests 进行 HTTP GET/POST。
"""
import asyncio
import ipaddress
import requests
from typing import Dict, Any, Optional
from urllib.parse import urlparse

from infra.tool_manager.tool_registry import ToolRegistry
from infra.tool_manager.tools.web_search import _sanitize_web_content
from utils.logger import setup_logger

logger = setup_logger("web_fetch")

MAX_CONTENT_LENGTH = 100000
REQUEST_TIMEOUT = 30


def _is_private_ip(hostname: str) -> bool:
    """SEC: 检查主机名是否解析到内网 IP（SSRF 防护）

    注意：
    - 不判 `ip.is_reserved`——Python ipaddress 把公网保留段误报，误伤正常域名
    - IPv6 的 `is_private` 会把 2001::/32（RFC 保留）误报为私有，故 IPv6 只认
      ULA（fc00::/7）与 link-local（fe80::/10）——这才是真正的内网 IPv6
    """
    import socket
    try:
        addrinfos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in addrinfos:
            try:
                ip = ipaddress.ip_address(sockaddr[0])
            except ValueError:
                continue
            # IPv4：内网/环回/链路本地（含云元数据 169.254.169.254）
            if ip.version == 4:
                if ip.is_private or ip.is_loopback or ip.is_link_local:
                    return True
                if str(ip) == "169.254.169.254":
                    return True
            # IPv6：只认 ULA（fc00::/7）与 link-local（fe80::/10）——排除 is_private 误报的 2001::/32
            else:
                if ip.is_loopback or ip.is_link_local:
                    return True
                if ip in ipaddress.ip_network("fc00::/7"):
                    return True
        return False
    except (socket.gaierror, ValueError):
        # 无法解析时拒绝（SSRF 安全兜底）
        return True


@ToolRegistry.register(
    "web_fetch",
    description=(
        "通过 HTTP 获取网页/API 内容。支持 GET 和 POST 方法。"
        "返回文本内容（HTML/Markdown/JSON）。"
        "用于阅读在线文档、调用 API、获取网页数据。"
    ),
    params={
        "url": "要获取的 URL（完整网址，含 https://）",
        "method": "可选，HTTP 方法：GET（默认）或 POST",
        "data": "可选，POST 请求的 body 数据",
        "headers": "可选，自定义请求头（JSON 格式字典字符串）",
    },
    risk_level="LOW",
    category="query",
    core=True,
)
async def web_fetch(
    url: str,
    method: str = "GET",
    data: Optional[str] = None,
    headers: Optional[str] = None,
) -> Dict[str, Any]:
    """获取网页内容"""
    if not url or not url.startswith(("http://", "https://")):
        return {"error": "URL 必须以 http:// 或 https:// 开头"}

    # SEC: SSRF 防护 — 检查目标是否为内网地址
    parsed = urlparse(url)
    if parsed.hostname and _is_private_ip(parsed.hostname):
        return {"error": f"禁止访问内网地址: {parsed.hostname}"}

    method = method.upper().strip()
    if method not in ("GET", "POST"):
        return {"error": f"不支持的 HTTP 方法: {method}，仅支持 GET/POST"}

    def _do_fetch():
        parsed_headers = {}
        if headers:
            import json
            try:
                parsed_headers = json.loads(headers)
            except json.JSONDecodeError:
                return {"error": "headers 参数必须是有效的 JSON 字典字符串"}

        resp = requests.request(
            method=method,
            url=url,
            data=data if method == "POST" else None,
            headers=parsed_headers,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        content = resp.text
        truncated = False
        if len(content) > MAX_CONTENT_LENGTH:
            content = content[:MAX_CONTENT_LENGTH]
            truncated = True

        # 净化内容：去格式 + 过滤注入 + 截断
        content = _sanitize_web_content(content, max_chars=8000)

        return {
            "url": resp.url,
            "status_code": resp.status_code,
            "content": content,
            "content_type": resp.headers.get("content-type", ""),
            "content_length": len(resp.text),
            "truncated": truncated,
        }

    try:
        return await asyncio.to_thread(_do_fetch)
    except requests.exceptions.Timeout:
        return {"error": f"请求超时（{REQUEST_TIMEOUT}秒）", "url": url}
    except requests.exceptions.ConnectionError as e:
        return {"error": f"连接失败: {e}", "url": url}
    except Exception as e:
        logger.error(f"网页获取失败: {e}")
        return {"error": str(e), "url": url}
