"""
会话持久化模型 — SQLite 存储对话历史

允许前端断线重连后恢复上下文，跨设备查看历史会话。
"""
from datetime import datetime
from sqlalchemy import Column, String, Text, Integer, DateTime, Boolean, Index
import uuid

from .connection import Base


class ChatSession(Base):
    """会话元数据"""
    __tablename__ = "chat_sessions"

    id = Column(String(100), primary_key=True, default=lambda: f"ses_{uuid.uuid4().hex[:12]}")
    session_id = Column(String(100), nullable=False, unique=True, index=True)
    title = Column(String(200), default="")  # 首条用户消息作为标题
    created_at = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime, default=datetime.utcnow)
    message_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True, index=True)
    execution_mode = Column(String(20), default="edit")
    metadata_json = Column(Text, default="{}")

    __table_args__ = (
        Index("ix_chat_sessions_last_active", "last_active"),
    )


class ChatMessage(Base):
    """单条对话消息"""
    __tablename__ = "chat_messages"

    id = Column(String(100), primary_key=True, default=lambda: f"msg_{uuid.uuid4().hex[:12]}")
    session_id = Column(String(100), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # user / assistant / system / tool
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    round_num = Column(Integer, default=0)
    tier = Column(String(20), default="")  # large / supervisor / expert
    metadata_json = Column(Text, default="{}")

    __table_args__ = (
        Index("ix_chat_messages_session_round", "session_id", "round_num"),
    )


class BlackboardObservation(Base):
    """agent 黑板协作观察（落库追溯；内存保持 MAX_OBSERVATIONS 上限，旧的可查此表）"""
    __tablename__ = "blackboard_observations"

    id = Column(String(100), primary_key=True, default=lambda: f"obs_{uuid.uuid4().hex[:12]}")
    session_id = Column(String(100), nullable=False, index=True)
    observation_id = Column(String(100), default="")       # 黑板 Observation.observation_id
    tier = Column(String(20), default="")                  # large / supervisor / expert / user
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    metadata_json = Column(Text, default="{}")

    __table_args__ = (
        Index("ix_blackboard_obs_session_time", "session_id", "created_at"),
    )


class ProactiveLog(Base):
    """主动搭话触发记录（追溯 + 统计：时间/会话/触发原因/内容）"""
    __tablename__ = "proactive_log"

    id = Column(String(100), primary_key=True, default=lambda: f"pal_{uuid.uuid4().hex[:12]}")
    session_id = Column(String(100), nullable=False, index=True)
    reason = Column(String(20), default="")   # schedule / screen / idle / time_window
    content = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
