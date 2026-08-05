"""黑板协作观察持久化 — 落库追溯 + 历史查询

agent 的 CognitiveBlackboard 内存仅保留 MAX_OBSERVATIONS(200) 条（优先新），
溢出清理的旧观察通过本模块写入 blackboard_observations 表，供追溯与历史查询。
"""
import json
from datetime import datetime
from typing import List, Dict, Any, Optional

from modules.database.connection import get_db_manager
from modules.database.chat_models import BlackboardObservation
from utils.logger import setup_logger

logger = setup_logger("blackboard_repo")


def save_observation(session_id: str, obs) -> bool:
    """将一条黑板 Observation 落库（失败不阻塞，仅记录）"""
    try:
        created = getattr(obs, "created_at", None)
        if isinstance(created, (int, float)) and created:
            created_dt = datetime.fromtimestamp(created)
        else:
            created_dt = datetime.utcnow()
        row = BlackboardObservation(
            session_id=session_id,
            observation_id=getattr(obs, "observation_id", "") or "",
            tier=getattr(obs, "tier", "") or "",
            content=getattr(obs, "content", "") or "",
            created_at=created_dt,
            metadata_json=json.dumps(getattr(obs, "metadata", {}) or {}, ensure_ascii=False),
        )
        db = get_db_manager()
        with db.get_session() as s:
            s.add(row)
        return True
    except Exception as e:
        logger.debug(f"[blackboard_repo] 观察落库失败: {e}")
        return False


def query_observations(
    session_id: str = "",
    query: str = "",
    start: str = "",
    end: str = "",
    limit: int = 50,
    tier: str = "",
) -> List[Dict[str, Any]]:
    """按条件查询黑板协作观察（内存溢出清理后仍可追溯）"""
    try:
        db = get_db_manager()
        with db.get_session() as s:
            q = s.query(BlackboardObservation)
            if session_id:
                q = q.filter(BlackboardObservation.session_id == session_id)
            if tier:
                q = q.filter(BlackboardObservation.tier == tier)
            if start:
                try:
                    q = q.filter(BlackboardObservation.created_at >= datetime.fromisoformat(start))
                except Exception:
                    pass
            if end:
                try:
                    q = q.filter(BlackboardObservation.created_at <= datetime.fromisoformat(end))
                except Exception:
                    pass
            if query:
                q = q.filter(BlackboardObservation.content.like(f"%{query}%"))
            rows = q.order_by(BlackboardObservation.created_at.desc()).limit(limit).all()
            return [{
                "session_id": r.session_id,
                "observation_id": r.observation_id,
                "tier": r.tier,
                "content": r.content,
                "created_at": r.created_at.isoformat() if r.created_at else "",
                "metadata": json.loads(r.metadata_json or "{}"),
            } for r in rows]
    except Exception as e:
        logger.debug(f"[blackboard_repo] 查询观察失败: {e}")
        return []
