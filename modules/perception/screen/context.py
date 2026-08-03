"""
ScreenContext — 统一屏幕理解输出格式

所有检测后端（touchpoint/cdp/vision）返回统一格式。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class UIElement:
    """UI 元素"""
    element_id: str = ""
    type: str = ""           # button / text / text_field / group / image / ...
    label: str = ""          # 元素文字/名称
    bbox: List[int] = field(default_factory=lambda: [0, 0, 0, 0])  # [x1, y1, x2, y2]
    center_x: int = 0
    center_y: int = 0
    actions: List[str] = field(default_factory=list)  # 可执行操作
    depth: int = 0           # 在 UI 树中的深度
    attributes: Dict[str, str] = field(default_factory=dict)


@dataclass
class ScreenContext:
    """屏幕理解结果"""
    # 基本信息
    app_name: str = ""
    window_title: str = ""
    timestamp: float = 0.0

    # UI 元素
    elements: List[UIElement] = field(default_factory=list)
    element_count: int = 0

    # 检测信息
    backend: str = ""        # touchpoint / cdp / vision / merged
    depth: int = 3
    elapsed_ms: float = 0.0

    # 角色统计
    role_summary: Dict[str, int] = field(default_factory=dict)

    # 视觉理解（当使用视觉模型时）
    visual_description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "app_name": self.app_name,
            "window_title": self.window_title,
            "element_count": self.element_count,
            "backend": self.backend,
            "depth": self.depth,
            "elapsed_ms": self.elapsed_ms,
            "role_summary": self.role_summary,
            "elements": [
                {
                    "type": e.type,
                    "label": e.label,
                    "center_x": e.center_x,
                    "center_y": e.center_y,
                    "actions": e.actions,
                }
                for e in self.elements[:50]  # 限制返回数量
            ],
            "visual_description": self.visual_description[:500] if self.visual_description else "",
        }

    def get_summary(self) -> str:
        """获取简洁摘要，用于注入 LLM 上下文"""
        parts = [f"当前应用: {self.app_name}"]
        if self.window_title:
            parts.append(f"窗口: {self.window_title[:50]}")

        if self.elements:
            # 按类型分组统计
            by_type = {}
            for e in self.elements:
                by_type.setdefault(e.type, []).append(e)
            type_info = ", ".join(f"{t}:{len(items)}" for t, items in by_type.items())
            parts.append(f"UI元素({self.element_count}个): {type_info}")

            # 列出关键元素
            buttons = [e for e in self.elements if e.type == "button" and e.label]
            if buttons:
                parts.append(f"按钮: {', '.join(e.label[:15] for e in buttons[:8])}")

            inputs = [e for e in self.elements if e.type in ("text_field", "textarea")]
            if inputs:
                parts.append(f"输入框: {len(inputs)}个")

        if self.visual_description:
            parts.append(f"视觉描述: {self.visual_description[:200]}")

        return " | ".join(parts)
