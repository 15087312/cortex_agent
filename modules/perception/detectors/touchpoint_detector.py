"""Touchpoint UI 结构化检测器（首选）

使用 Touchpoint（macOS 无障碍 API）替代 OmniParser 做 UI 元素检测。
零模型、零推理延迟，直接从系统读取原生 UI 控件树。

CDP（Chrome DevTools Protocol）支持：
对于 Electron/CEF 应用（如网易云音乐），如果应用以 --remote-debugging-port 启动，
可通过 CDP 获取完整的 WebView 内部 AX 树，远多于原生 AX 仅暴露的菜单栏。

配置方式：在 .env 中设置 CDP_PORTS 环境变量：
  CDP_PORTS={"网易云音乐":9223,"微信":9224}

降级：当 Touchpoint 不可用或返回空结果时，回退到 ScreenMonitorMCP（纯视觉方案）。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from modules.perception.detectors.base import PerceptionDetector
from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from utils.logger import setup_logger

logger = setup_logger("touchpoint_detector")

# ---------------------------------------------------------------------------
# CEF 116 兼容补丁
# CEF 116 的 /json/list 和 /json/version 用 Python urllib 请求返回 502，
# 但用 http.client 正常。touchpoint 内部用的 urllib，这里打补丁绕过。
# ---------------------------------------------------------------------------
_CEF_PATCH_APPLIED = False


def _apply_cef_patch():
    """给 touchpoint CDP 后端打 CEF 116 兼容补丁"""
    global _CEF_PATCH_APPLIED
    if _CEF_PATCH_APPLIED:
        return

    try:
        import http.client
        import json as _json
        import touchpoint.backends.cdp.cdp as cdp_mod

        def _patched_list_targets(port):
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                conn.request("GET", "/json")
                resp = conn.getresponse()
                return _json.loads(resp.read())
            except Exception:
                return []

        def _patched_get_browser_ws_url(port):
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                conn.request("GET", "/json/version")
                resp = conn.getresponse()
                data = _json.loads(resp.read())
                return data.get("webSocketDebuggerUrl")
            except Exception:
                return None

        cdp_mod._list_targets = _patched_list_targets
        cdp_mod._get_browser_ws_url = _patched_get_browser_ws_url
        _CEF_PATCH_APPLIED = True
        logger.debug("CEF 116 CDP 兼容补丁已应用")
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"CEF 补丁应用失败: {e}")


# ---------------------------------------------------------------------------
# CDP 端口配置（从环境变量加载，格式同 MCP_SERVERS）
#   CDP_PORTS={"网易云音乐":9223,"微信":9224}
# ---------------------------------------------------------------------------
_CDP_PORTS: Dict[str, int] = {}


def _load_cdp_ports():
    global _CDP_PORTS
    raw = os.environ.get("CDP_PORTS", "").strip()
    if not raw:
        return
    try:
        _CDP_PORTS = json.loads(raw)
        if not isinstance(_CDP_PORTS, dict):
            _CDP_PORTS = {}
            return
        # 验证值都是 int
        _CDP_PORTS = {k: int(v) for k, v in _CDP_PORTS.items()}
        logger.info(f"CDP 端口配置: {_CDP_PORTS}")
    except (json.JSONDecodeError, ValueError, TypeError):
        logger.warning(f"CDP_PORTS 格式无效: {raw}")


_load_cdp_ports()


@dataclass
class UIElement:
    """UI 元素描述（与 OmniParserDetector 的 UIElement 兼容）"""
    element_id: str = ""
    type: str = "unknown"
    label: str = ""
    bbox: List[int] = field(default_factory=list)  # [x1, y1, x2, y2]
    center_x: int = 0
    center_y: int = 0
    confidence: float = 1.0
    source: str = "touchpoint"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "element_id": self.element_id,
            "type": self.type,
            "label": self.label,
            "bbox": self.bbox,
            "center_x": self.center_x,
            "center_y": self.center_y,
            "confidence": self.confidence,
            "source": self.source,
        }


# Touchpoint flat format 中的角色名到 UIElement 类型映射
_FLAT_ROLE_MAP = {
    "button": "button",
    "text": "text",
    "text_field": "input",
    "text_area": "input",
    "combo_box": "select",
    "check_box": "checkbox",
    "radio_button": "radio",
    "pop_up_button": "button",
    "menu_button": "button",
    "static_text": "text",
    "slider": "slider",
    "switch": "checkbox",
    "tab_group": "tab",
    "search_field": "input",
    "menu_item": "menuitem",
    "link": "link",
    "image": "icon",
    "table": "table",
    "outline": "list",
    "progress_indicator": "progress",
    "disclosure_triangle": "disclosure",
}

# flat 格式解析正则： [id] [type] 'label' (x,y) w×h ...
_FLAT_LINE_RE = re.compile(
    r"\[([^\]]+)\]\s+"          # id
    r"\[([^\]]+)\]\s+"          # type
    r"\'([^\']*)\'\s+"          # label
    r"\((\d+),\s*(\d+)\)\s+"    # x, y
    r"(\d+)×(\d+)",             # w, h (注意 × 不是 x)
)


class TouchpointDetector(PerceptionDetector):
    """Touchpoint UI 检测器（单例）

    通过 macOS 无障碍 API 读取原生 UI 控件树。
    不加载任何视觉模型，零延迟。
    """

    _instance: Optional["TouchpointDetector"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, fallback_to_screenmonitor: bool = True):
        if self._initialized:
            return
        self._initialized = True

        # 应用 CEF 兼容补丁（全局一次）
        _apply_cef_patch()

        self._available: Optional[bool] = None
        self._tp = None  # 延迟导入
        self._prev_elements: List[UIElement] = []
        self._element_counter = 0
        self.precision = "element"
        self.backend = "touchpoint"
        self._fallback_to_screenmonitor = fallback_to_screenmonitor
        self._cdp_configured = False

    @property
    def detector_type(self) -> str:
        return "ui"

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available

        try:
            import touchpoint as tp
            diag = tp.diagnostics()
            backend = diag.get("backend", {})
            errs = diag.get("errors", []) or []
            self._available = backend.get("available", False) and len(errs) == 0
            if self._available:
                self._tp = tp
                # 配置 CDP 端口（如配置了 CDP_PORTS）
                self._configure_cdp()
                logger.info(f"Touchpoint 可用: backend={backend.get('name')}")
            else:
                logger.warning(f"Touchpoint 不可用: errors={errs}")
            return self._available
        except ImportError:
            logger.warning("Touchpoint 未安装 (pip install touchpoint-py)")
            self._available = False
            return False
        except Exception as e:
            logger.warning(f"Touchpoint 检测失败: {e}")
            self._available = False
            return False

    def _configure_cdp(self):
        """配置 CDP 端口（从 _CDP_PORTS 全局配置加载）"""
        if self._cdp_configured or not _CDP_PORTS:
            return
        try:
            self._tp.configure(cdp_ports=_CDP_PORTS)
            self._cdp_configured = True
            logger.info(f"CDP 端口已配置: {_CDP_PORTS}")
        except Exception as e:
            logger.warning(f"CDP 配置失败: {e}")

    def detect_elements(self, screenshot: Any = None, app: str = "") -> List[UIElement]:
        """主接口：截图（可选）→ UI 元素列表

        Args:
            screenshot: 兼容参数，Touchpoint 不需要（直接从系统读取 UI 树）
            app: 可选，指定应用名（如 "Safari"、"Edge"），为空则扫描全部活跃窗口

        Returns:
            UIElement 列表
        """
        if not self.is_available():
            if self._fallback_to_screenmonitor:
                return self._fallback_detect(screenshot)
            return []

        try:
            return self._detect_internal(app=app)
        except Exception as e:
            logger.error(f"Touchpoint detect_elements 失败: {e}")
            if self._fallback_to_screenmonitor:
                return self._fallback_detect(screenshot)
            return []

    def _detect_internal(self, app: str = "") -> List[UIElement]:
        """使用 Touchpoint 读取 UI 元素

        对于有 CDP 端口配置的 Electron/CEF 应用，自动使用 CDP AX 源
        （能穿透 WebView 获取内部元素），否则用原生 AX 源。
        """
        tp = self._tp
        if tp is None:
            return []

        elements: List[UIElement] = []
        self._element_counter = 0

        # 指定了 app → 只扫这一个
        if app:
            app_names = [app]
        else:
            windows = tp.windows()
            if not windows:
                return []
            targets = [w for w in windows if getattr(w, 'active', False)]
            if not targets:
                targets = windows[:1]
            app_names = list(dict.fromkeys(w.app for w in targets))

        for app_name in app_names:
            detected = self._scan_app(app_name, tp)
            elements.extend(detected)

        if elements and not app:
            logger.debug(f"Touchpoint 检测到 {len(elements)} 个 UI 元素，来自 {len(app_names)} 个应用")
        return elements

    def _scan_app(self, app_name: str, tp) -> List[UIElement]:
        """扫描单个应用的 UI 元素

        CDP 策略：
        - 已有 CDP 端口配置 → 直接 cdp_ax 源
        - 原生 AX 只返回 <10 个元素（仅菜单栏）→ 检测是否为 Electron，自动配 CDP 后重扫
        """
        elements: List[UIElement] = []

        # 已有 CDP 端口配置 → 直接 CDP
        if app_name in _CDP_PORTS:
            return self._scan_with_cdp(app_name, tp)

        # 先试原生 AX
        try:
            flat_text = tp.elements(
                app=app_name,
                named_only=True,
                max_depth=20,
                format="flat",
                source="full",
            )
        except Exception as e:
            logger.debug(f"Touchpoint 扫描 {app_name} 时出错: {e}")
            return []

        if isinstance(flat_text, str) and flat_text.strip():
            for line in flat_text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                parsed = self._parse_flat_line(line, app_name)
                if parsed:
                    elements.append(parsed)

        # 结果 ≥10 → native 够用，直接返回
        if len(elements) >= 10:
            return elements

        # 结果 <10（大概率仅菜单栏）→ 尝试 Electron CDP 自动配置
        logger.info(f"{app_name} 仅 {len(elements)} 个元素，检测是否为 Electron 应用...")
        if self._auto_setup_cdp(app_name):
            # CDP 配置成功，重新扫描
            elements = self._scan_with_cdp(app_name, tp)
            if len(elements) >= 5:
                return elements

        # Electron 也不管用 → 降级到 ScreenMonitorMCP 纯视觉
        logger.info(f"{app_name} 降级到 ScreenMonitorMCP 视觉分析...")
        visual = self._fallback_detect_visual(app_name)
        if visual:
            return visual

        return elements

    def _scan_with_cdp(self, app_name: str, tp) -> List[UIElement]:
        """用 CDP 源扫描应用"""
        elements: List[UIElement] = []

        for source in ("cdp_ax", "full"):
            try:
                flat_text = tp.elements(
                    app=app_name,
                    named_only=True,
                    max_depth=20,
                    format="flat",
                    source=source,
                )
            except Exception as e:
                logger.debug(f"Touchpoint {source} 读取 {app_name} 时出错: {e}")
                continue

            if not isinstance(flat_text, str) or not flat_text.strip():
                continue

            for line in flat_text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                parsed = self._parse_flat_line(line, app_name)
                if parsed:
                    elements.append(parsed)

            # CDP 拿到了足够元素 → 不降级
            if source == "cdp_ax" and len(elements) >= 5:
                break
            # CDP 结果太少 → 清空走 native 降级
            if source == "cdp_ax":
                logger.debug(
                    f"CDP 扫描 {app_name} 仅 {len(elements)} 个元素，降级到 native AX"
                )
                elements = []

        return elements

    def _auto_setup_cdp(self, app_name: str) -> bool:
        """自动为 Electron 应用配置 CDP

        1. 检测应用是否为 Electron/CEF
        2. 查找可用端口
        3. 杀掉现有进程，以 --remote-debugging-port=N 重启
        4. 注册到 CDP 配置
        """
        import subprocess
        import socket
        import time

        app_path = self._find_app_path(app_name)
        if not app_path:
            logger.debug(f"找不到 {app_name} 的路径")
            return False

        if not self._is_electron_app(app_path):
            logger.debug(f"{app_name} 不是 Electron 应用")
            return False

        # 找可用端口
        port = self._find_free_port(9223, 9245)
        if not port:
            logger.warning(f"没有可用端口用于 {app_name}")
            return False

        # 找到可执行文件
        executable = self._find_electron_executable(app_path)
        if not executable:
            logger.warning(f"找不到 {app_name} 的可执行文件")
            return False

        # 优雅退出旧进程
        logger.info(f"重启 {app_name} 以启用 CDP (端口 {port})...")
        self._quit_app_gracefully(app_name)
        time.sleep(1.5)

        # 带 CDP 启动
        try:
            subprocess.Popen(
                [executable, f"--remote-debugging-port={port}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.warning(f"启动 {app_name} 失败: {e}")
            return False

        # 等端口就绪
        for _ in range(10):
            time.sleep(0.5)
            if self._check_port_open(port):
                break
        else:
            logger.warning(f"{app_name} CDP 端口 {port} 未就绪")
            return False

        # 注册 CDP 端口
        global _CDP_PORTS
        _CDP_PORTS[app_name] = port
        try:
            self._tp.configure(cdp_ports=_CDP_PORTS)
            self._cdp_configured = True
            logger.info(f"{app_name} CDP 自动配置完成: 端口 {port}")
            return True
        except Exception as e:
            logger.warning(f"{app_name} CDP 配置失败: {e}")
            return False

    # ------------------------------------------------------------------
    # CDP 自动配置辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _find_app_path(app_name: str) -> Optional[str]:
        """查找 .app 路径（支持中文名、英文名、Bundle ID）"""
        import subprocess

        # 方法1: 通过 touchpoint window 拿 PID → lsappinfo 拿 bundle path
        try:
            import touchpoint as tp
            for w in tp.windows():
                if getattr(w, "app", "") == app_name:
                    pid = getattr(w, "pid", 0)
                    if pid:
                        result = subprocess.run(
                            ["lsappinfo", "info", "-only", "bundlepath",
                             "-p", str(pid)],
                            capture_output=True, text=True, timeout=5,
                        )
                        path = result.stdout.strip().strip('"')
                        # lsappinfo 返回格式: "LSBundlePath"="/path/to/WeChat.app"
                        if "=" in path:
                            path = path.split("=", 1)[1].strip().strip('"')
                        if path and path.endswith(".app") and os.path.isdir(path):
                            return path
        except Exception:
            pass

        # 方法2: 遍历标准目录 + 模糊匹配
        app_lower = app_name.lower()
        for base in ("/Applications", os.path.expanduser("~/Applications"),
                     "/System/Applications", os.path.expanduser("~/Desktop")):
            try:
                for item in os.listdir(base):
                    if not item.endswith(".app"):
                        continue
                    # 匹配：item不含.app后缀后与app_name忽略大小写比较
                    base_name = item[:-4]  # 去掉 .app
                    if base_name.lower() == app_lower:
                        return os.path.join(base, item)
                    # 也匹配包含关系（如 "WeChat" 包含 "微信" 的反向）
                    if app_lower in base_name.lower() or base_name.lower() in app_lower:
                        return os.path.join(base, item)
            except PermissionError:
                continue

        return None

    @staticmethod
    def _is_electron_app(app_path: str) -> bool:
        """检测 .app 是否为 Electron/CEF 应用

        特征：Contents/Frameworks/ 下存在 Helper (Renderer).app
        """
        frameworks = os.path.join(app_path, "Contents", "Frameworks")
        if not os.path.isdir(frameworks):
            return False
        try:
            for item in os.listdir(frameworks):
                if "Helper (Renderer).app" in item or " Helper" in item:
                    return True
        except PermissionError:
            pass
        return False

    @staticmethod
    def _find_electron_executable(app_path: str) -> Optional[str]:
        """找 Electron 应用的可执行文件"""
        macos_dir = os.path.join(app_path, "Contents", "MacOS")
        if not os.path.isdir(macos_dir):
            return None
        try:
            for item in os.listdir(macos_dir):
                full = os.path.join(macos_dir, item)
                if os.path.isfile(full) and os.access(full, os.X_OK):
                    return full
        except PermissionError:
            pass
        return None

    @staticmethod
    def _find_free_port(start: int = 9223, end: int = 9245) -> Optional[int]:
        """找可用 TCP 端口"""
        import socket
        for port in range(start, end + 1):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("127.0.0.1", port))
                    return port
            except OSError:
                continue
        return None

    @staticmethod
    def _check_port_open(port: int) -> bool:
        """检查端口是否已打开"""
        import http.client
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            conn.request("GET", "/json/version")
            resp = conn.getresponse()
            return resp.status == 200
        except Exception:
            return False

    @staticmethod
    def _quit_app_gracefully(app_name: str):
        """优雅退出应用"""
        import subprocess
        try:
            subprocess.run(
                ["osascript", "-e",
                 f'tell application "{app_name}" to quit'],
                capture_output=True, text=True, timeout=5,
            )
        except Exception:
            pass
        # 再补一刀 kill
        try:
            subprocess.run(
                ["pkill", "-f", app_name],
                capture_output=True, text=True, timeout=3,
            )
        except Exception:
            pass

    def _parse_flat_line(self, line: str, app_name: str) -> Optional[UIElement]:
        """解析 flat 格式的一行到 UIElement"""
        m = _FLAT_LINE_RE.match(line)
        if not m:
            return None

        el_id = m.group(1)
        role_name = m.group(2)
        label = m.group(3)[:120]
        x = int(m.group(4))
        y = int(m.group(5))
        w = int(m.group(6))
        h = int(m.group(7))

        if w <= 2 and h <= 2:
            return None

        element_type = _FLAT_ROLE_MAP.get(role_name, "unknown")
        non_interactive = {"group", "container", "toolbar", "window", "menubar", "progress"}
        if element_type in non_interactive and not label:
            return None

        self._element_counter += 1
        return UIElement(
            element_id=f"e{self._element_counter:03d}",
            type=element_type,
            label=label,
            bbox=[x, y, x + w, y + h],
            center_x=x + w // 2,
            center_y=y + h // 2,
            confidence=1.0,
            source=f"touchpoint/{app_name}",
        )

    def _fallback_detect_visual(self, app_name: str = "") -> List[UIElement]:
        """视觉降级：对指定应用窗口截图 OCR

        先用 touchpoint 截图指定窗口，再用 RapidOCR 识别文字。
        比 AX 方案慢（~2-5s），但适用于无法通过 AX 获取 UI 的所有应用。
        """
        elements: List[UIElement] = []

        # 1. 用 touchpoint 截取目标窗口
        try:
            import touchpoint as tp
            img = tp.screenshot(app=app_name)
            if img is None:
                logger.warning(f"无法截图 {app_name}")
                return []
            logger.debug(f"{app_name} 截图: {img.size}")
        except Exception as e:
            logger.warning(f"touchpoint 截图失败: {e}")
            return []

        # 2. 转 OpenCV 格式
        try:
            import cv2
            import numpy as np
            img_cv = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        except Exception as e:
            logger.warning(f"图片格式转换失败: {e}")
            return []

        # 3. OCR 识别
        try:
            from infra.mcp.servers.screen_monitor_server import _detect_elements
            raw = _detect_elements(img_cv, detect_buttons=True, extract_text=True)
        except Exception as e:
            logger.warning(f"ScreenMonitor OCR 失败: {e}")
            return []

        for item in raw:
            label = str(item.get("label", "")).strip()
            if not label:
                continue
            self._element_counter += 1
            bbox = item.get("bbox", [0, 0, 0, 0])
            elements.append(UIElement(
                element_id=f"v{self._element_counter:03d}",
                type=item.get("type", "text"),
                label=label[:120],
                bbox=bbox,
                center_x=(bbox[0] + bbox[2]) // 2 if len(bbox) >= 4 else 0,
                center_y=(bbox[1] + bbox[3]) // 2 if len(bbox) >= 4 else 0,
                confidence=float(item.get("confidence", 0.5)),
                source=f"screenmonitor/{app_name}" if app_name else "screenmonitor",
            ))

        if elements:
            logger.info(f"ScreenMonitor 视觉降级成功: {len(elements)} 个元素 (via {app_name})")
        return elements

    def _fallback_detect(self, screenshot: Any = None) -> List[UIElement]:
        """降级：通过 MCP 调用 ScreenMonitorMCP 做纯视觉分析"""
        try:
            from infra.tool_manager.tool_manager import ToolManager
            tm = ToolManager()
            result = tm.execute_tool(
                "analyze_ui_elements",
                params={"detect_buttons": True, "extract_text": True},
            )
            if result and result.get("success"):
                raw = result.get("result", "")
                # 解析 ScreenMonitorMCP 返回的文本
                elements = self._parse_screenmonitor_result(raw)
                if elements:
                    logger.info(f"ScreenMonitorMCP 降级成功: {len(elements)} 个元素")
                    return elements
            logger.warning(f"ScreenMonitorMCP 降级返回空: {result}")
        except Exception as e:
            logger.warning(f"ScreenMonitorMCP 降级失败: {e}")
        return []

    def _parse_screenmonitor_result(self, raw_text: str) -> List[UIElement]:
        """解析 ScreenMonitorMCP 的文本输出到 UIElement 列表

        ScreenMonitorMCP 的 analyze_ui_elements 返回文本描述，
        这里做基础解析。
        """
        elements = []
        lines = raw_text.strip().split("\n")
        for line in lines:
            if not line.strip():
                continue
            self._element_counter += 1
            elements.append(UIElement(
                element_id=f"e{self._element_counter:03d}",
                type="unknown",
                label=line.strip()[:120],
                source="screenmonitor/fallback",
            ))
        return elements

    def detect(
        self,
        roi_image: np.ndarray,
        roi_name: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[PerceptionEvent]:
        """检测 ROI 图像中的 UI 变化

        Args:
            roi_image: ROI 区域图像（BGR numpy array）
            roi_name: ROI 区域名
            context: 可选上下文

        Returns:
            PerceptionEvent 列表（最多一个 SCREEN_UI 事件）
        """
        elements = self.detect_elements(roi_image)

        if not elements:
            return []

        # 对比上一次的变化（省略实现，与 OmniParserDetector 类似）
        current_keys = {(e.element_id, e.type, e.label) for e in elements}
        prev_keys = {(e.element_id, e.type, e.label) for e in self._prev_elements} if self._prev_elements else set()

        # 如果元素没有变化，不发事件（变化检测）
        if current_keys == prev_keys and self._prev_elements:
            return []

        self._prev_elements = elements

        change_desc = f"检测到 {len(elements)} 个 UI 元素 (via {self.backend})"
        if elements:
            first = elements[0]
            change_desc += f"，例如「{first.label}」({first.type})"

        event = PerceptionEvent(
            event_type=PerceptionEventType.SCREEN_UI,
            source="touchpoint_detector",
            payload={
                "elements": [e.to_dict() for e in elements],
                "count": len(elements),
                "change_desc": change_desc,
                "backend": self.backend,
            },
        )
        return [event]

    def reset(self) -> None:
        """重置检测器状态"""
        self._prev_elements = []
        self._element_counter = 0
