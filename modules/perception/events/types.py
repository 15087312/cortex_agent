"""统一感知事件类型定义

所有感知层产出的事件都使用 PerceptionEvent 格式。
模块间通过 Event Bus 传递事件，不直接调用。

PerceptionEventType 常量类已提升至 modules.shared.events（跨模块共享契约），
此处 re-export 以保持感知层内部引用路径不变。
"""
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from modules.shared.events import PerceptionEventType  # noqa: F401  re-export 共享契约


@dataclass
class PerceptionEvent:
    """统一感知事件

    所有感知模块产出的事件都使用此格式。
    payload 内容由 event_type 决定。
    """
    event_type: str                          # PerceptionEventType.*
    timestamp: float = field(default_factory=time.time)
    platform: str = "unknown"                # "windows" / "macos" / "linux"
    source: str = ""                         # "ocr" / "ui" / "window" / "file"
    importance: float = 0.5                  # 0.0-1.0
    payload: Dict[str, Any] = field(default_factory=dict)
    roi_name: Optional[str] = None           # 关联的 ROI 区域名
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> Dict[str, Any]:
        # @link:block PerceptionEvent_Emit = Emit
        """转换为字典（用于序列化/日志）"""
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
