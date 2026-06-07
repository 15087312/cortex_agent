"""统一感知事件类型定义

所有感知层产出的事件都使用 PerceptionEvent 格式。
模块间通过 Event Bus 传递事件，不直接调用。
"""
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class PerceptionEventType:
    """事件类型常量"""
    # 感知层产出
    SCREEN_DIFF = "screen.diff"             # 帧差检测结果
    SCREEN_OCR = "screen.ocr"               # OCR 文本变化
    SCREEN_UI = "screen.ui"                 # UI 元素变化
    SCREEN_WINDOW = "screen.window"         # 窗口状态变化
    FILE_CHANGE = "file.change"             # 文件变化
    DIALOG_CHANGE = "dialog.change"         # 对话变化

    # 检测层产出
    DIFFERENCE_DETECTED = "difference.detected"  # 检测到差异

    # 触发层产出
    PROACTIVE_MESSAGE = "proactive.message"  # 主动搭话消息
    WORLD_STATE_UPDATE = "world.state.update"  # 世界状态更新

    # 通配
    ALL = "*"                                # 订阅所有事件


@dataclass
class PerceptionEvent:
    """统一感知事件

    所有感知模块产出的事件都使用此格式。
    payload 内容由 event_type 决定。
    """
    event_type: str                          # PerceptionEventType.*
    timestamp: float = field(default_factory=time.time)
    platform: str = "unknown"                # "windows" / "macos" / "linux"
    source: str = ""                         # "ocr" / "ui" / "window" / "file" / "dialog"
    importance: float = 0.5                  # 0.0-1.0
    payload: Dict[str, Any] = field(default_factory=dict)
    roi_name: Optional[str] = None           # 关联的 ROI 区域名
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "platform": self.platform,
            "source": self.source,
            "importance": self.importance,
            "payload": self.payload,
            "roi_name": self.roi_name,
        }

    def short_repr(self) -> str:
        """简短表示，用于日志"""
        payload_preview = str(self.payload)[:80] if self.payload else "{}"
        return (
            f"[{self.event_type}] src={self.source} "
            f"imp={self.importance:.1f} {payload_preview}"
        )
