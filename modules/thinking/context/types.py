"""
上下文管理 — 共享数据类型

保留：CompressionLevel, EventType, EventRecord
"""
import time
import hashlib
from enum import Enum
from typing import Dict, Any
from dataclasses import dataclass, field


class CompressionLevel(str, Enum):
    """压缩级别"""
    NONE = "none"
    LIGHT = "light"
    MODERATE = "moderate"
    HEAVY = "heavy"
    AGGRESSIVE = "aggressive"


class EventType(str, Enum):
    """事件类型"""
    MODEL_OUTPUT = "model_output"
    TOOL_CALL = "tool_call"
    PROBE_SIGNAL = "probe_signal"
    EXPERT_RESULT = "expert_result"
    SYSTEM = "system"
    MEMORY_CONTEXT = "memory_context"
    FILE_CHANGE = "file_change"


@dataclass
class EventRecord:
    """事件记录 — 所有模型输出、工具调用、探针信号等"""
    event_id: str = ""
    timestamp: float = field(default_factory=time.time)
    event_type: EventType = EventType.SYSTEM
    source_role: str = "system"
    content: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    importance: float = 0.5

    def __post_init__(self):
        if not self.event_id:
            self.event_id = f"evt_{int(self.timestamp)}_{hashlib.md5(str(self.content).encode()).hexdigest()[:8]}"
