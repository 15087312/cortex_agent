"""
Session persistence repository — CRUD for sessions and messages.
"""
from datetime import datetime
from typing import List, Optional, Dict, Any
from sqlalchemy import desc

from .connection import get_db_manager
from .chat_models import ChatSession, ChatMessage
from backend.utils.logger import setup_logger

logger = setup_logger("session_repo")


class SessionRepository:
    """Session persistence repository."""

    def __init__(self):
        self._db = get_db_manager()

    def _session(self):
        return self._db.get_session()

    def create_session(self, session_id: str) -> None:
        with self._session() as s:
            existing = s.query(ChatSession).filter_by(session_id=session_id).first()
            if existing:
                existing.last_active = datetime.utcnow()
                existing.is_active = True
            else:
                s.add(ChatSession(session_id=session_id))
            s.commit()

    def touch_session(self, session_id: str) -> None:
        with self._session() as s:
            row = s.query(ChatSession).filter_by(session_id=session_id).first()
            if row:
                row.last_active = datetime.utcnow()
                s.commit()

    def close_session(self, session_id: str) -> None:
        with self._session() as s:
            row = s.query(ChatSession).filter_by(session_id=session_id).first()
            if row:
                row.is_active = False
                s.commit()

    def set_session_title(self, session_id: str, title: str) -> None:
        with self._session() as s:
            row = s.query(ChatSession).filter_by(session_id=session_id).first()
            if row:
                row.title = (title or "")[:200]
                s.commit()

    def get_all_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._session() as s:
            rows = s.query(ChatSession).order_by(desc(ChatSession.last_active)).limit(limit).all()
            return [{
                "session_id": r.session_id,
                "title": r.title,
                "created_at": r.created_at.isoformat() if r.created_at else "",
                "last_active": r.last_active.isoformat() if r.last_active else "",
                "message_count": r.message_count,
                "is_active": r.is_active,
            } for r in rows]

    def get_active_sessions(self) -> List[Dict[str, Any]]:
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
            } for r in rows]

    def save_message(self, session_id: str, role: str, content: str, round_num: int = 0) -> str:
        if not content or not content.strip():
            return ""
        with self._session() as s:
            msg = ChatMessage(
                session_id=session_id,
                role=role,
                content=content[:50000],
                round_num=round_num,
            )
            s.add(msg)
            s.flush()  # 立即生成 msg.id
            session_row = s.query(ChatSession).filter_by(session_id=session_id).first()
            if session_row:
                session_row.message_count += 1
                session_row.last_active = datetime.utcnow()
                if role == "user" and not session_row.title:
                    session_row.title = content[:200]
            s.commit()
            return msg.id

    def delete_message(self, session_id: str, message_id: str) -> bool:
        with self._session() as s:
            msg = s.query(ChatMessage).filter_by(session_id=session_id, id=message_id).first()
            if not msg:
                return False
            s.delete(msg)
            session_row = s.query(ChatSession).filter_by(session_id=session_id).first()
            if session_row:
                session_row.message_count = max(0, session_row.message_count - 1)
                session_row.last_active = datetime.utcnow()
            s.commit()
            return True

    def update_message(self, session_id: str, message_id: str, content: str) -> bool:
        if not content or not content.strip():
            return False
        with self._session() as s:
            msg = s.query(ChatMessage).filter_by(session_id=session_id, id=message_id).first()
            if not msg:
                return False
            msg.content = content[:50000]
            session_row = s.query(ChatSession).filter_by(session_id=session_id).first()
            if session_row:
                session_row.last_active = datetime.utcnow()
            s.commit()
            return True

    def get_messages(self, session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self._session() as s:
            rows = s.query(ChatMessage).filter_by(
                session_id=session_id
            ).order_by(ChatMessage.created_at).limit(limit).all()
            return [{
                "id": r.id,
                "role": r.role,
                "content": r.content,
                "created_at": r.created_at.isoformat() if r.created_at else "",
                "round_num": r.round_num,
            } for r in rows]

    def get_recent_messages(self, session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        with self._session() as s:
            rows = s.query(ChatMessage).filter_by(
                session_id=session_id
            ).order_by(desc(ChatMessage.created_at)).limit(limit).all()
            rows.reverse()
            return [{
                "role": r.role,
                "content": r.content,
                "created_at": r.created_at.isoformat() if r.created_at else "",
                "round_num": r.round_num,
            } for r in rows]

    def delete_session(self, session_id: str) -> bool:
        with self._session() as s:
            msg_count = s.query(ChatMessage).filter_by(session_id=session_id).delete()
            session_row = s.query(ChatSession).filter_by(session_id=session_id).first()
            if session_row:
                s.delete(session_row)
                s.commit()
                logger.info(f"Deleted session: {session_id[:12]}... ({msg_count} messages)")
                return True
            return False


# Global singleton
_session_repo: Optional[SessionRepository] = None


def get_session_repo() -> SessionRepository:
    global _session_repo
    if _session_repo is None:
        _session_repo = SessionRepository()
    return _session_repo
