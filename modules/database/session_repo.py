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


# 本次进程启动后是否保存过用户消息（主动搭话前提判定用，进程级标志）
_boot_has_spoken = False


def get_boot_has_spoken() -> bool:
    """本次进程启动后是否真正发过消息（进程级标志，重启自动归零）。"""
    return _boot_has_spoken


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
        """设置会话标题（覆盖旧标题，支持重命名）"""
        with self._session() as s:
            row = s.query(ChatSession).filter_by(session_id=session_id).first()
            if row:
                row.title = (title or "")[:200]

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
                     round_num: int = 0, tier: str = "") -> str:
        """保存单条消息，返回消息 ID"""
        global _boot_has_spoken
        if role == "user":
            _boot_has_spoken = True
        if not content or not content.strip():
            return ""
        with self._session() as s:
            msg = ChatMessage(
                session_id=session_id,
                role=role,
                content=content[:50000],  # 截断过长内容
                round_num=round_num,
                tier=tier,
            )
            s.add(msg)
            s.flush()  # 立即生成 msg.id
            # 更新会话消息计数和标题
            session_row = s.query(ChatSession).filter_by(session_id=session_id).first()
            if session_row:
                session_row.message_count += 1
                session_row.last_active = datetime.utcnow()
                if role == "user" and not session_row.title:
                    session_row.title = content[:200]
            return msg.id

    def delete_message(self, session_id: str, message_id: str) -> bool:
        """删除单条消息（同步更新会话消息计数）"""
        with self._session() as s:
            msg = s.query(ChatMessage).filter_by(
                session_id=session_id, id=message_id
            ).first()
            if not msg:
                return False
            s.delete(msg)
            session_row = s.query(ChatSession).filter_by(session_id=session_id).first()
            if session_row:
                session_row.message_count = max(0, session_row.message_count - 1)
                session_row.last_active = datetime.utcnow()
            return True

    def update_message(self, session_id: str, message_id: str, content: str) -> bool:
        """修改单条消息内容"""
        if not content or not content.strip():
            return False
        with self._session() as s:
            msg = s.query(ChatMessage).filter_by(
                session_id=session_id, id=message_id
            ).first()
            if not msg:
                return False
            msg.content = content[:50000]
            session_row = s.query(ChatSession).filter_by(session_id=session_id).first()
            if session_row:
                session_row.last_active = datetime.utcnow()
            return True

    def get_messages(self, session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """获取会话消息（按时间正序）"""
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
                "id": r.id,
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

    def delete_session(self, session_id: str) -> bool:
        """删除会话及其所有消息"""
        with self._session() as s:
            msg_count = s.query(ChatMessage).filter_by(session_id=session_id).delete()
            session_row = s.query(ChatSession).filter_by(session_id=session_id).first()
            if session_row:
                s.delete(session_row)
                logger.info(f"[SessionRepo] 删除会话: {session_id[:12]}... ({msg_count} 条消息)")
                return True
            return False

    def delete_empty_sessions(self, exclude_ids: Optional[List[str]] = None,
                              min_idle_minutes: int = 10) -> int:
        """自动清理没有任何消息（0 对话）的空会话

        新建后从未发过消息的会话会残留占位，这里批量删除。
        - exclude_ids: 明确需要保留的会话 ID（如当前有活跃 WebSocket 的会话）
        - min_idle_minutes: 只删除超过该时长未活动的空会话（最近创建/使用中的保留）

        Returns:
            删除的空会话数量
        """
        from datetime import timedelta
        exclude = set(exclude_ids or [])
        cutoff = datetime.utcnow() - timedelta(minutes=max(0, min_idle_minutes))
        with self._session() as s:
            rows = s.query(ChatSession).filter(ChatSession.message_count == 0).all()
            deleted = 0
            for row in rows:
                if row.session_id in exclude:
                    continue
                if row.last_active and row.last_active > cutoff:
                    continue  # 最近还在活跃，可能正被使用
                s.delete(row)
                deleted += 1
            if deleted:
                logger.info(f"[SessionRepo] 自动清理 {deleted} 个空会话（无消息且闲置超 {min_idle_minutes} 分钟）")
            return deleted

    def copy_messages_to_session(self, source_id: str, target_id: str) -> int:
        """将源会话的所有消息复制到目标会话，返回复制条数"""
        with self._session() as s:
            source_msgs = s.query(ChatMessage).filter_by(
                session_id=source_id
            ).order_by(ChatMessage.created_at).all()
            if not source_msgs:
                return 0
            for msg in source_msgs:
                s.add(ChatMessage(
                    session_id=target_id,
                    role=msg.role,
                    content=msg.content,
                    round_num=msg.round_num,
                    tier=msg.tier,
                ))
            # 更新目标会话的消息计数
            target_session = s.query(ChatSession).filter_by(session_id=target_id).first()
            if target_session:
                target_session.message_count = len(source_msgs)
            logger.info(f"[SessionRepo] 复制 {len(source_msgs)} 条消息: {source_id[:12]} → {target_id[:12]}")
            return len(source_msgs)


# 全局单例
_session_repo: Optional[SessionRepository] = None


def get_session_repo() -> SessionRepository:
    global _session_repo
    if _session_repo is None:
        _session_repo = SessionRepository()
    return _session_repo
