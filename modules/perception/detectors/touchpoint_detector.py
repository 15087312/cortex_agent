"""Touchpoint UI 结构化检测器（首选）

使用 Touchpoint（macOS 无障碍 API）替代 OmniParser 做 UI 元素检测。
零模型、零推理延迟，直接从系统读取原生 UI 控件树。

降级：当 Touchpoint 不可用或返回空结果时，回退到 ScreenMonitorMCP（纯视觉方案）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from modules.perception.detectors.base import PerceptionDetector
from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from utils.logger import setup_logger

logger = setup_logger("touchpoint_detector")


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
        self._available: Optional[bool] = None
        self._tp = None  # 延迟导入
        self._prev_elements: List[UIElement] = []
        self._element_counter = 0
        self.precision = "element"
        self.backend = "touchpoint"
        self._fallback_to_screenmonitor = fallback_to_screenmonitor
        self._ax_timeout_set = False

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

    def detect_elements(self, screenshot: Any = None, timeout: float = 8.0) -> List[UIElement]:
        """主接口：截图（可选）→ UI 元素列表

        Args:
            screenshot: 兼容参数，Touchpoint 不需要（直接从系统读取 UI 树）
            timeout: 超时秒数（保留参数签名兼容，内部不再使用线程包装）

        Returns:
            UIElement 列表
        """
        if not self.is_available():
            if self._fallback_to_screenmonitor:
                return self._fallback_detect(screenshot)
            return []

        try:
            return self._detect_internal()
        except Exception as e:
            logger.error(f"Touchpoint detect_elements 失败: {e}")
            if self._fallback_to_screenmonitor:
                return self._fallback_detect(screenshot)
            return []

    def _detect_internal(self) -> List[UIElement]:
        """使用 Touchpoint flat 格式读取当前活跃窗口的 UI 元素

        用 flat 格式 + 正则解析，比 json 格式更可靠、更快。
        """
        tp = self._tp
        if tp is None:
            return []

        # 配置更快的 macOS AX 超时（避免卡在无响应的应用上）
        if not self._ax_timeout_set:
            tp.configure(ax_messaging_timeout=0.5)
            self._ax_timeout_set = True

        elements: List[UIElement] = []
        self._element_counter = 0

        windows = tp.windows()
        if not windows:
            return []

        # 只处理活跃窗口，没有活跃则取第一个
        targets = [w for w in windows if getattr(w, 'active', False)]
        if not targets:
            targets = windows[:1]

        seen_apps = set()
        for win in targets:
            app_name = win.app
            if app_name in seen_apps:
                continue
            seen_apps.add(app_name)

            try:
                flat_text = tp.elements(
                    app=app_name,
                    named_only=True,
                    max_depth=2,  # depth=2 足够获取 UI 表层元素，避免深层 AX 遍历
                    format="flat",
                )
                if not isinstance(flat_text, str):
                    continue

                lines = [l for l in flat_text.split("\n") if l.strip()]
                for line in lines[:80]:  # 每 app 最多解析 80 行
                    parsed = self._parse_flat_line(line, app_name)
                    if parsed:
                        elements.append(parsed)

                if len(elements) >= 100:
                    break
            except Exception as e:
                logger.debug(f"Touchpoint 读取 {app_name} 时出错: {e}")
                continue

        # 注意：_prev_elements 由 detect() 方法统一管理，此处不设置
        if elements:
            logger.debug(f"Touchpoint 检测到 {len(elements)} 个 UI 元素，来自 {len(seen_apps)} 个应用")
        return elements

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
