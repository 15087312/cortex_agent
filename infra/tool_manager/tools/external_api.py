"""
外部 API 工具 — HTTP GET/POST 与通用外部 API 调用
"""
from typing import Dict, Any, Optional
import socket
import ipaddress

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("external_api")


def _is_private_ip(url: str) -> bool:
    """检查 URL 是否指向私有/内部 IP（防 SSRF）"""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False

        # 解析主机名
        try:
            ip = ipaddress.ip_address(hostname)
        except ValueError:
            # 是域名，解析为 IP
            try:
                addr = socket.getaddrinfo(hostname, None, socket.AF_INET)
                if addr:
                    ip = ipaddress.ip_address(addr[0][4][0])
                else:
                    return False
            except (socket.gaierror, OSError):
                return False

        # 检查是否为私有/保留地址
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or str(ip) == "169.254.169.254"
    except Exception:
        return False


@ToolRegistry.register("http_get", description="发送 HTTP GET 请求到指定 URL。用于调用 REST API 和获取网页数据。", params={
    "url": "请求的 URL",
    "headers": "可选，请求头（JSON 格式字符串）",
    "timeout": "可选，超时秒数（默认30）",
}, risk_level="LOW", category="query")
def http_get(url: str, headers: Optional[str] = None, timeout: Optional[int] = 30) -> Dict[str, Any]:
    """发送 HTTP GET 请求"""
    if not url: return {"error": "URL 不能为空"}
    if _is_private_ip(url): return {"error": "禁止访问内部/私有网络地址（SSRF 防护）"}
    timeout = max(5, min(timeout or 30, 120))
    try:
        import requests
        parsed_headers = {}
        if headers:
            import json
            try: parsed_headers = json.loads(headers)
            except json.JSONDecodeError: return {"error": "headers 不是有效的 JSON"}
        resp = requests.get(url, headers=parsed_headers, timeout=timeout, allow_redirects=True)
        content = resp.text[:100000]
        return {"success": resp.ok, "status_code": resp.status_code, "url": resp.url, "content": content, "content_type": resp.headers.get("content-type", ""), "content_length": len(resp.text)}
    except requests.exceptions.Timeout: return {"error": f"请求超时（{timeout}秒）", "url": url}
    except requests.exceptions.ConnectionError as e: return {"error": f"连接失败: {e}"}
    except Exception as e: return {"error": str(e)}


@ToolRegistry.register("http_post", description="发送 HTTP POST 请求到指定 URL。用于调用 REST API。", params={
    "url": "请求的 URL",
    "data": "可选，请求体数据",
    "headers": "可选，请求头（JSON 格式字符串）",
    "timeout": "可选，超时秒数（默认30）",
}, risk_level="LOW", category="query")
def http_post(url: str, data: Optional[str] = None, headers: Optional[str] = None, timeout: Optional[int] = 30) -> Dict[str, Any]:
    """发送 HTTP POST 请求"""
    if not url: return {"error": "URL 不能为空"}
    if _is_private_ip(url): return {"error": "禁止访问内部/私有网络地址（SSRF 防护）"}
    timeout = max(5, min(timeout or 30, 120))
    try:
        import requests
        parsed_headers = {}
        if headers:
            import json
            try: parsed_headers = json.loads(headers)
            except json.JSONDecodeError: return {"error": "headers 不是有效的 JSON"}
        resp = requests.post(url, data=data, headers=parsed_headers, timeout=timeout, allow_redirects=True)
        content = resp.text[:100000]
        return {"success": resp.ok, "status_code": resp.status_code, "url": resp.url, "content": content, "content_type": resp.headers.get("content-type", ""), "content_length": len(resp.text)}
    except requests.exceptions.Timeout: return {"error": f"请求超时（{timeout}秒）", "url": url}
    except requests.exceptions.ConnectionError as e: return {"error": f"连接失败: {e}"}
    except Exception as e: return {"error": str(e)}


@ToolRegistry.register("call_external_api", description="调用外部 API（需安全审批）。支持指定方法和格式。", params={
    "url": "API 请求 URL",
    "method": "可选，HTTP 方法（默认 GET）",
    "data": "可选，请求体（JSON 格式字符串）",
    "headers": "可选，请求头（JSON 格式字符串）",
}, risk_level="HIGH", category="admin")
def call_external_api(url: str, method: str = "GET", data: Optional[str] = None, headers: Optional[str] = None) -> Dict[str, Any]:
    """调用外部 API（带安全审批标记）"""
    method = method.upper().strip()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        return {"error": f"不支持的 HTTP 方法: {method}"}
    if method == "GET":
        return http_get(url, headers)
    return http_post(url, data, headers)
