"""
差异检测器数据模型

Difference dataclass + DifferenceRecord SQLAlchemy ORM
"""
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from datetime import datetime
from sqlalchemy import Column, String, Float, Boolean, DateTime, JSON, Index
import uuid
import time

from modules.database.connection import Base


@dataclass
class Difference:
    """差异 — 系统感知到的任何偏差、变化或异常"""
    id: str = field(default_factory=lambda: f"diff_{uuid.uuid4().hex[:12]}")
    source_type: str = ""           # "time"|"internal"|"behavioral"|"expectation"|"user_input"
    category: str = ""              # "idle_critical", "unfinished_task", etc.
    intensity: float = 0.0          # 0-100, IntensityAssigner 赋值
    created_at: float = field(default_factory=time.time)
    ttl: float = 3600.0             # 自动溶解时间(秒)
    payload: Dict[str, Any] = field(default_factory=dict)
    related_ids: List[str] = field(default_factory=list)
    status: str = "active"          # "active"|"incubating"|"dissolved"

    def to_dict(self) -> Dict[str, Any]:
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


class DifferenceRecord(Base):
    """差异持久化记录 — SQLite 表"""
    __tablename__ = "differences"

    id = Column(String(100), primary_key=True, default=lambda: f"diff_{uuid.uuid4().hex[:12]}")
    source_type = Column(String(50), nullable=False, index=True)
    category = Column(String(100), nullable=False)
    intensity = Column(Float, default=0.0, index=True)
    created_at = Column(Float, default=time.time)
    ttl = Column(Float, default=3600.0)
    payload = Column(JSON, default=dict)
    related_ids = Column(JSON, default=list)
    status = Column(String(20), default="active", index=True)
    expires_at = Column(Float, nullable=True)

    __table_args__ = (
        Index("idx_diff_source_status", "source_type", "status"),
        Index("idx_diff_intensity", "intensity"),
        Index("idx_diff_expires", "expires_at"),
        Index("idx_diff_created", "created_at"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_type": self.source_type,
            "category": self.category,
            "intensity": self.intensity,
            "created_at": self.created_at,
            "ttl": self.ttl,
            "payload": self.payload or {},
            "related_ids": self.related_ids or [],
            "status": self.status,
        }

    def to_difference(self) -> Difference:
        return Difference(
            id=self.id,
            source_type=self.source_type,
            category=self.category,
            intensity=self.intensity,
            created_at=self.created_at,
            ttl=self.ttl,
            payload=self.payload or {},
            related_ids=self.related_ids or [],
            status=self.status,
        )
