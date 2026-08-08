"""主动搭话触发记录 — 可追溯 + 统计

记录每次主动搭话（时间/会话/触发原因/内容），供前端主动搭话页展示历史。
"""
from datetime import datetime, timezone
from typing import List, Dict, Any

from modules.database.connection import get_db_manager
from modules.database.chat_models import ProactiveLog
from utils.logger import setup_logger

logger = setup_logger("proactive_repo")


def save_proactive_log(session_id: str, reason: str, content: str) -> bool:
    """记录一次主动搭话（失败不阻塞）"""
    try:
        row = ProactiveLog(
            session_id=session_id,
            reason=reason or "",
            content=(content or "")[:2000],
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db = get_db_manager()
        with db.get_session() as s:
            s.add(row)
        return True
    except Exception as e:
        logger.debug(f"[proactive_repo] 记录失败: {e}")
        return False


def query_proactive_logs(limit: int = 50, session_id: str = "") -> List[Dict[str, Any]]:
    """查询主动搭话记录（按时间倒序）"""
    try:
        db = get_db_manager()
        with db.get_session() as s:
            q = s.query(ProactiveLog)
            if session_id:
                q = q.filter(ProactiveLog.session_id == session_id)
            rows = q.order_by(ProactiveLog.created_at.desc()).limit(min(limit, 200)).all()
            return [{
                "session_id": r.session_id,
                "reason": r.reason,
                "content": r.content,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            } for r in rows]
    except Exception as e:
        logger.debug(f"[proactive_repo] 查询失败: {e}")
        return []


def count_proactive_logs() -> int:
    """主动搭话总次数"""
    try:
        db = get_db_manager()
        with db.get_session() as s:
            return s.query(ProactiveLog).count()
    except Exception:
        return 0
