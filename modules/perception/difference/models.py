"""差异数据模型 — 表示系统检测到的一个差异

每个 Difference 记录一个具体差异:
- source_type: 来源类型（time/perception/internal 等）
- category: 类别（idle_critical/file_created 等）
- intensity: 强度 (0-100)
- ttl: 存活时间（秒），超时后标记为 dissolved
"""
from dataclasses import dataclass, field
from typing import Dict, Any, List
import uuid
import time


@dataclass
class Difference:
    """差异数据模型

    表示系统感知到的一个差异（环境变化/用户行为/时间流逝等）。
    """

    id: str = field(default_factory=lambda: f"diff_{uuid.uuid4().hex[:12]}")
    source_type: str = ""
    category: str = ""
    intensity: float = 0.0
    created_at: float = field(default_factory=time.time)
    ttl: float = 3600.0
    payload: Dict[str, Any] = field(default_factory=dict)
    related_ids: List[str] = field(default_factory=list)
    status: str = "active"

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于序列化/API 输出）"""

        return {
            "id": self.id,
            "source_type": self.source_type,
            "category": self.category,
            "intensity": self.intensity,
            "created_at": self.created_at,
            "ttl": self.ttl,
            "payload": self.payload,
            "related_ids": self.related_ids,
            "status": self.status,
        }
