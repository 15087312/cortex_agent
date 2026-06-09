"""
会话持久化仓库 — 读写 SQLite

提供会话和消息的 CRUD 操作，供 api_stream.py 调用。
"""
from datetime import datetime
from typing import List, Optional, Dict, Any
from sqlalchemy import desc

from modules.database.connection import get_db_manager
from modules.database.chat_models import ChatSession, ChatMessage
from utils.logger import setup_logger

logger = setup_logger("session_repo")


class SessionRepository:
    """会话持久化仓库"""

    def __init__(self):
        self._db = get_db_manager()

    def _session(self):
        return self._db.get_session()

    # ── 会话 ──

    def create_session(self, session_id: str, execution_mode: str = "edit") -> None:
        """创建会话记录（幂等）"""
        with self._session() as s:
            existing = s.query(ChatSession).filter_by(session_id=session_id).first()
            if existing:
                existing.last_active = datetime.utcnow()
                existing.is_active = True
            else:
                s.add(ChatSession(
                    session_id=session_id,
                    execution_mode=execution_mode,
                ))

    def touch_session(self, session_id: str) -> None:
        """更新会话最后活跃时间"""
        with self._session() as s:
            row = s.query(ChatSession).filter_by(session_id=session_id).first()
            if row:
                row.last_active = datetime.utcnow()

    def close_session(self, session_id: str) -> None:
        """标记会话为非活跃"""
        with self._session() as s:
            row = s.query(ChatSession).filter_by(session_id=session_id).first()
            if row:
                row.is_active = False

    def set_session_title(self, session_id: str, title: str) -> None:
        """设置会话标题（取首条用户消息）"""
        with self._session() as s:
            row = s.query(ChatSession).filter_by(session_id=session_id).first()
            if row and not row.title:
                row.title = title[:200]

    def get_all_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取所有会话（按最后活跃时间倒序）"""
        with self._session() as s:
            rows = s.query(ChatSession).order_by(desc(ChatSession.last_active)).limit(limit).all()
            return [{
                "session_id": r.session_id,
                "title": r.title,
                "created_at": r.created_at.isoformat() if r.created_at else "",
                "last_active": r.last_active.isoformat() if r.last_active else "",
                "message_count": r.message_count,
                "is_active": r.is_active,
                "execution_mode": r.execution_mode,
            } for r in rows]

    def get_active_sessions(self) -> List[Dict[str, Any]]:
        """获取活跃会话"""
        with self._session() as s:
            rows = s.query(ChatSession).filter_by(is_active=True).order_by(
                desc(ChatSession.last_active)
            ).all()
            return [{
                "session_id": r.session_id,
                "title": r.title,
                "created_at": r.created_at.isoformat() if r.created_at else "",
                "last_active": r.last_active.isoformat() if r.last_active else "",
                "message_count": r.message_count,
                "execution_mode": r.execution_mode,
            } for r in rows]

    # ── 消息 ──

    def save_message(self, session_id: str, role: str, content: str,
                     round_num: int = 0, tier: str = "") -> None:
        """保存单条消息"""
        if not content or not content.strip():
            return
        with self._session() as s:
            s.add(ChatMessage(
                session_id=session_id,
                role=role,
                content=content[:50000],  # 截断过长内容
                round_num=round_num,
                tier=tier,
            ))
            # 更新会话消息计数和标题
            session_row = s.query(ChatSession).filter_by(session_id=session_id).first()
            if session_row:
                session_row.message_count += 1
                session_row.last_active = datetime.utcnow()
                if role == "user" and not session_row.title:
                    session_row.title = content[:200]

    def get_messages(self, session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """获取会话消息（按时间正序）"""
        with self._session() as s:
            rows = s.query(ChatMessage).filter_by(
                session_id=session_id
            ).order_by(ChatMessage.created_at).limit(limit).all()
            return [{
                "role": r.role,
                "content": r.content,
                "created_at": r.created_at.isoformat() if r.created_at else "",
                "round_num": r.round_num,
                "tier": r.tier,
            } for r in rows]

    def get_recent_messages(self, session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """获取最近 N 条消息（用于重连时恢复上下文）"""
        with self._session() as s:
            rows = s.query(ChatMessage).filter_by(
                session_id=session_id
            ).order_by(desc(ChatMessage.created_at)).limit(limit).all()
            rows.reverse()  # 正序
            return [{
                "role": r.role,
                "content": r.content,
                "created_at": r.created_at.isoformat() if r.created_at else "",
                "round_num": r.round_num,
                "tier": r.tier,
            } for r in rows]

    def get_session_summary(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取会话摘要（元数据 + 最近消息）"""
        with self._session() as s:
            session_row = s.query(ChatSession).filter_by(session_id=session_id).first()
            if not session_row:
                return None
            return {
                "session_id": session_row.session_id,
                "title": session_row.title,
                "created_at": session_row.created_at.isoformat() if session_row.created_at else "",
                "last_active": session_row.last_active.isoformat() if session_row.last_active else "",
                "message_count": session_row.message_count,
                "is_active": session_row.is_active,
                "execution_mode": session_row.execution_mode,
            }


# 全局单例
_session_repo: Optional[SessionRepository] = None


def get_session_repo() -> SessionRepository:
    global _session_repo
    if _session_repo is None:
        _session_repo = SessionRepository()
    return _session_repo
