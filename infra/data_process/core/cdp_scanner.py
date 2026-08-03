"""
CDP Scanner — Chrome DevTools Protocol UI 元素扫描

用于检测 Chromium/ Electron 应用的内部 UI 元素。
macOS 无障碍 API 无法读取 Chromium 应用内部，需要通过 CDP 获取 DOM 树。

使用方式:
    scanner = CDPScanner()
    elements = scanner.scan(port=9222)
"""
import json
from typing import Any, Dict, List, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

from utils.logger import setup_logger

logger = setup_logger("cdp_scanner")


class CDPScanner:
    """Chrome DevTools Protocol UI 元素扫描器"""

    def __init__(self):
        self._connections: Dict[int, Any] = {}

    def find_chromium_ports(self) -> List[Dict[str, Any]]:
        """扫描本地运行的 Chromium 应用的 CDP 端口"""
        results = []
        # 常见的 CDP 端口范围
        for port in range(9222, 9240):
            try:
                req = Request(f"http://127.0.0.1:{port}/json", headers={"Accept": "application/json"})
                resp = urlopen(req, timeout=1)
                data = json.loads(resp.read())
                if data:
                    results.append({
                        "port": port,
                        "pages": [
                            {
                                "id": p.get("id", ""),
                                "title": p.get("title", ""),
                                "url": p.get("url", ""),
                                "type": p.get("type", ""),
                            }
                            for p in data if p.get("type") == "page"
                        ],
                    })
            except (URLError, OSError, json.JSONDecodeError):
                continue
        return results

    def scan(self, port: int, page_id: Optional[str] = None, max_depth: int = 3) -> List[Dict[str, Any]]:
        """通过 CDP 扫描页面 DOM 元素"""
        try:
            # 获取页面列表
            req = Request(f"http://127.0.0.1:{port}/json", headers={"Accept": "application/json"})
            resp = urlopen(req, timeout=3)
            pages = json.loads(resp.read())

            if not pages:
                return []

            # 选择目标页面
            target = None
            for p in pages:
                if p.get("type") != "page":
                    continue
                if page_id and p.get("id") == page_id:
                    target = p
                    break
                if not page_id and not target:
                    target = p

            if not target:
                return []

            # 通过 WebSocket 或 HTTP 执行 JavaScript
            ws_url = target.get("webSocketDebuggerUrl", "")
            if not ws_url:
                # 降级：尝试用 HTTP endpoint
                return self._scan_via_http(port, target.get("id", ""))

            return self._scan_via_ws(ws_url, max_depth)

        except Exception as e:
            logger.error(f"CDP 扫描失败: {e}")
            return []

    def _scan_via_http(self, port: int, page_id: str) -> List[Dict[str, Any]]:
        """通过 HTTP 降级扫描（功能有限）"""
        try:
            # 使用 Runtime.evaluate 获取基本信息
            url = f"http://127.0.0.1:{port}/json/activate/{page_id}"
            urlopen(url, timeout=2)

            # 获取页面信息
            req = Request(f"http://127.0.0.1:{port}/json")
            resp = urlopen(req, timeout=2)
            pages = json.loads(resp.read())

            for p in pages:
                if p.get("id") == page_id:
                    return [{
                        "type": "page",
                        "role": "group",
                        "name": p.get("title", ""),
                        "url": p.get("url", ""),
                        "bbox": [0, 0, 0, 0],
                        "center_x": 0,
                        "center_y": 0,
                        "children_count": 0,
                    }]
            return []
        except Exception:
            return []

    def _scan_via_ws(self, ws_url: str, max_depth: int) -> List[Dict[str, Any]]:
        """通过 WebSocket 扫描（需要 websocket-client）"""
        try:
            import websocket
        except ImportError:
            logger.warning("websocket-client 未安装，CDP 功能受限")
            return []

        elements = []
        try:
            ws = websocket.create_connection(ws_url, timeout=5)
            msg_id = 1

            # 启用 DOM
            ws.send(json.dumps({"id": msg_id, "method": "DOM.enable"}))
            ws.recv()
            msg_id += 1

            # 获取文档根节点
            ws.send(json.dumps({"id": msg_id, "method": "DOM.getDocument", "params": {"depth": max_depth}}))
            result = json.loads(ws.recv())
            msg_id += 1

            root = result.get("result", {}).get("root", {})
            elements = self._parse_dom_node(root, depth=0, max_depth=max_depth)

            ws.close()
        except Exception as e:
            logger.error(f"CDP WebSocket 扫描失败: {e}")

        return elements

    def _parse_dom_node(self, node: Dict, depth: int, max_depth: int, parent_bbox: Optional[List] = None) -> List[Dict]:
        """递归解析 DOM 节点"""
        elements = []

        if depth > max_depth:
            return elements

        node_name = node.get("nodeName", "")
        node_type = node.get("nodeType", 0)

        # 跳过文本节点和注释
        if node_type == 3 or node_type == 8:
            return elements

        # 获取属性
        attributes = {}
        attrs_list = node.get("attributes", [])
        for i in range(0, len(attrs_list), 2):
            if i + 1 < len(attrs_list):
                attributes[attrs_list[i]] = attrs_list[i + 1]

        # 获取文本内容
        text = attributes.get("aria-label", "") or attributes.get("title", "") or attributes.get("placeholder", "")
        if not text:
            # 尝试从子文本节点获取
            for child in node.get("children", []):
                if child.get("nodeType") == 3:
                    text = child.get("nodeValue", "").strip()
                    if text:
                        break

        # 角色映射
        role = attributes.get("role", "")
        if not role:
            tag = node_name.lower()
            role_map = {
                "button": "button", "input": "text_field", "textarea": "text_field",
                "a": "link", "img": "image", "select": "dropdown",
                "h1": "heading", "h2": "heading", "h3": "heading",
                "div": "group", "span": "text", "p": "text",
            }
            role = role_map.get(tag, "unknown")

        # 构建元素
        if text or role in ("button", "text_field", "link", "heading"):
            element = {
                "type": role,
                "role": role,
                "name": text[:100] if text else f"({node_name})",
                "tag": node_name,
                "bbox": [0, 0, 0, 0],  # CDP 默认不提供坐标
                "center_x": 0,
                "center_y": 0,
                "depth": depth,
                "children_count": len(node.get("children", [])),
                "attributes": {k: v[:50] for k, v in attributes.items() if k.startswith("aria-") or k in ("role", "type", "placeholder")},
            }
            elements.append(element)

        # 递归子节点
        for child in node.get("children", []):
            elements.extend(self._parse_dom_node(child, depth + 1, max_depth))

        return elements

    def scan_active_chromium(self, app_name: str = "", max_depth: int = 3) -> Dict[str, Any]:
        """扫描活跃的 Chromium 应用"""
        ports = self.find_chromium_ports()
        if not ports:
            return {"success": False, "error": "未找到 CDP 端口（需要启动 Chromium 应用时加 --remote-debugging-port=9222）"}

        results = []
        for port_info in ports:
            elements = self.scan(port_info["port"], max_depth=max_depth)
            for page in port_info.get("pages", []):
                if app_name and app_name.lower() not in page.get("title", "").lower():
                    continue
                results.append({
                    "port": port_info["port"],
                    "page": page,
                    "elements": elements,
                    "count": len(elements),
                })

        return {
            "success": True,
            "results": results,
            "total_elements": sum(r["count"] for r in results),
            "hint": "使用 --remote-debugging-port=9222 启动 Chromium 应用以启用 CDP 扫描",
        }


# 全局单例
_scanner: Optional[CDPScanner] = None


def get_cdp_scanner() -> CDPScanner:
    global _scanner
    if _scanner is None:
        _scanner = CDPScanner()
    return _scanner
