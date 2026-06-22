"""
记忆系统 SQLAlchemy 模型
"""
from datetime import datetime
from typing import List, Optional
from sqlalchemy import (
    Column, String, Text, Integer, Float, Boolean, DateTime,
    Index, JSON
)
import uuid

from .connection import Base


class ShortTermMemory(Base):
    """短期记忆模型"""
    __tablename__ = "short_term_memories"

    id = Column(String(100), primary_key=True, default=lambda: f"stm_{uuid.uuid4().hex[:12]}")
    content = Column(Text, nullable=False)
    memory_type = Column(String(50), default="dialog")
    importance = Column(Float, default=0.5)
    emotion = Column(String(50), default="")
    emotion_intensity = Column(Float, default=0.0)
    source = Column(String(100), default="system")
    owner = Column(String(50), default="system")
    session_id = Column(String(100), default="", index=True)

    tier = Column(String(20), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    expires_at = Column(DateTime, nullable=True)

    tags = Column(JSON, default=list)
    extra_data = Column(JSON, default=dict)

    is_active = Column(Boolean, default=True, index=True)
    access_count = Column(Integer, default=0)
    last_accessed = Column(DateTime, nullable=True)

    __table_args__ = (
        Index('idx_stm_tier_created', 'tier', 'created_at'),
        Index('idx_stm_type_importance', 'memory_type', 'importance'),
        Index('idx_stm_session_created', 'session_id', 'created_at'),
        Index('idx_stm_owner_created', 'owner', 'created_at'),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "memory_type": self.memory_type,
            "importance": self.importance,
            "emotion": self.emotion,
            "tier": self.tier,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "tags": self.tags or [],
            "extra_data": self.extra_data or {},
            "is_active": self.is_active,
            "session_id": self.session_id,
            "owner": self.owner,
        }


class ExperienceMemory(Base):
    """经验记忆模型"""
    __tablename__ = "experience_memories"

    id = Column(String(100), primary_key=True, default=lambda: f"exp_{uuid.uuid4().hex[:12]}")

    situation = Column(Text, nullable=False, index=True)
    action = Column(Text, nullable=False)
    result = Column(Text, nullable=False)
    success = Column(Boolean, nullable=False, index=True)

    context = Column(JSON, default=dict)
    tags = Column(JSON, default=list)

    attempt_count = Column(Integer, default=1)
    success_count = Column(Integer, default=0)
    success_rate = Column(Float, default=0.0)
    avg_reward = Column(Float, default=0.0)

    first_attempt = Column(DateTime, default=datetime.utcnow)
    last_attempt = Column(DateTime, default=datetime.utcnow, index=True)

    owner = Column(String(50), default="system")

    __table_args__ = (
        Index('idx_exp_success_rate', 'success_rate'),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "situation": self.situation,
            "action": self.action,
            "result": self.result,
            "success": self.success,
            "success_rate": self.success_rate,
            "attempt_count": self.attempt_count,
            "tags": self.tags or []
        }
