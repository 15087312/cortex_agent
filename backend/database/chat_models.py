"""
Chat persistence models — SQLite storage for conversation history.
"""
from datetime import datetime
from sqlalchemy import Column, String, Text, Integer, DateTime, Boolean, Index
import uuid

from .connection import Base


class ChatSession(Base):
    """Session metadata."""
    __tablename__ = "chat_sessions"

    id = Column(String(100), primary_key=True, default=lambda: f"ses_{uuid.uuid4().hex[:12]}")
    session_id = Column(String(100), nullable=False, unique=True, index=True)
    title = Column(String(200), default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime, default=datetime.utcnow, index=True)
    message_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True, index=True)


class ChatMessage(Base):
    """Single chat message."""
    __tablename__ = "chat_messages"

    id = Column(String(100), primary_key=True, default=lambda: f"msg_{uuid.uuid4().hex[:12]}")
    session_id = Column(String(100), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # user / assistant / system
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    round_num = Column(Integer, default=0)

    __table_args__ = (
        Index("ix_chat_messages_session_round", "session_id", "round_num"),
    )
