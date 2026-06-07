"""
记忆系统 SQLAlchemy 模型
"""
from datetime import datetime
from typing import List, Optional
from sqlalchemy import (
    Column, String, Text, Integer, Float, Boolean, DateTime,
    ForeignKey, Index, JSON, Numeric, UniqueConstraint
)
from sqlalchemy.orm import relationship
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


class LongTermMemory(Base):
    """长期记忆模型"""
    __tablename__ = "long_term_memories"
    
    id = Column(String(100), primary_key=True, default=lambda: f"ltm_{uuid.uuid4().hex[:12]}")
    content = Column(Text, nullable=False)
    memory_type = Column(String(50), default="general")
    
    timestamp = Column(DateTime, default=datetime.utcnow)
    cause_effect = Column(Text, default="")
    emotion = Column(String(50), default="")
    source = Column(String(100), default="system")
    importance = Column(Float, default=0.5)
    
    category = Column(String(50), nullable=False, index=True)
    region = Column(String(50), nullable=False, index=True)
    owner = Column(String(50), default="system")
    
    tags = Column(JSON, default=list)
    extra_data = Column(JSON, default=dict)
    
    access_count = Column(Integer, default=0)
    last_accessed = Column(DateTime, nullable=True)
    
    is_archived = Column(Boolean, default=False, index=True)
    archived_at = Column(DateTime, nullable=True)
    
    version = Column(Integer, default=1)
    parent_id = Column(String(100), nullable=True)
    
    __table_args__ = (
        Index('idx_ltm_category_timestamp', 'category', 'timestamp'),
        Index('idx_ltm_region_importance', 'region', 'importance'),
    )
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "memory_type": self.memory_type,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "cause_effect": self.cause_effect,
            "emotion": self.emotion,
            "importance": self.importance,
            "category": self.category,
            "region": self.region,
            "tags": self.tags or [],
            "extra_data": self.extra_data or {},
            "is_archived": self.is_archived
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


class MemoryRegion(Base):
    """记忆区域模型"""
    __tablename__ = "memory_regions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), unique=True, nullable=False)
    display_name = Column(String(100))
    description = Column(Text)
    
    parent_id = Column(Integer, ForeignKey('memory_regions.id'), nullable=True)
    icon = Column(String(20))
    color = Column(String(20))
    sort_order = Column(Integer, default=0)
    
    read_roles = Column(JSON, default=list)
    write_roles = Column(JSON, default=list)
    
    max_size_mb = Column(Integer, default=100)
    retention_days = Column(Integer, default=365)
    auto_archive = Column(Boolean, default=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MemoryShortcut(Base):
    """记忆快捷方式模型"""
    __tablename__ = "memory_shortcuts"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), nullable=False)
    target_region = Column(String(50), nullable=False)
    description = Column(Text)
    filter_conditions = Column(JSON, default={})
    owner = Column(String(50), default="system")
    created_at = Column(DateTime, default=datetime.utcnow)


class MemoryPermission(Base):
    """记忆权限模型"""
    __tablename__ = "memory_permissions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    role = Column(String(50), nullable=False)
    region = Column(String(50), nullable=False)
    can_read = Column(Boolean, default=False)
    can_write = Column(Boolean, default=False)
    can_delete = Column(Boolean, default=False)
    can_modify_acl = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        UniqueConstraint('role', 'region', name='uq_permission_role_region'),
    )


class MemoryRelation(Base):
    """记忆关系模型"""
    __tablename__ = "memory_relations"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(String(100), nullable=False, index=True)
    target_id = Column(String(100), nullable=False, index=True)
    relation_type = Column(String(50), nullable=False, index=True)
    strength = Column(Float, default=0.5)
    extra_data = Column(JSON, default={})
    created_at = Column(DateTime, default=datetime.utcnow)
