"""会话投影（Phase 4）— 派生状态的整值快照，按 key 覆盖写。

设计见 docs/VISUALIZATION_DESIGN.md §4.3：

- `todos` / `runners` / `tokenUsage` / `contextPressure` 不再靠零散事件拼状态，
  而是各自一份 `{key, value, seq}` 的整值快照；
- 每个 key 只保留**最新一版**（upsert，不追加），刷新/重开会话读一次即可恢复，
  不会像事件那样随时间无限增长；
- seq 与 `session_events` 共用同一个每会话计数器：客户端按 `higher-seq-wins`
  丢旧帧，重连补发或回放读到的旧快照不会盖掉更新的状态。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, DateTime, Integer, String, Text, func

from modules.database.connection import Base, get_db_manager
from utils.logger import setup_logger

logger = setup_logger("session_projections")

# 单个投影的 value JSON 上限（runners 含每个模型的状态文本，比事件预览大一些；
# 超限时截断而不是丢帧——宁可少几个字段，也不要前端拿不到这份状态）
_MAX_VALUE_CHARS = 20000


def _utcnow() -> datetime:
    """naive UTC now（与 chat_models / session_events 一致）"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SessionProjection(Base):
    """会话投影（主键 (session_id, key)；每个 key 只有一行，覆盖写）"""

    __tablename__ = "session_projections"

    session_id = Column(String(100), primary_key=True)
    key = Column(String(40), primary_key=True)
    value_json = Column(Text, nullable=False, default="{}")
    seq = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=_utcnow, index=True)


class ProjectionRepository:
    """投影的读取与覆盖写。seq 来自事件仓储的每会话计数器。"""

    def __init__(self):
        self._db = get_db_manager()

    def _session(self):
        return self._db.get_session()

    def upsert(self, session_id: str, key: str, value: Any,
               seq: Optional[int] = None) -> Dict[str, Any]:
        """写入/覆盖一个投影，返回 `{key, value, seq}`。

        seq 未显式给定 → 向事件仓储申请（与事件同一序号空间，客户端可统一比较）。
        """
        if seq is None:
            seq = self._allocate_seq(session_id)
        assigned = int(seq)
        payload = _dumps(value)
        with self._session() as s:
            row = s.query(SessionProjection).filter(
                SessionProjection.session_id == session_id,
                SessionProjection.key == key,
            ).first()
            if row is None:
                s.add(SessionProjection(
                    session_id=session_id,
                    key=key,
                    value_json=payload,
                    seq=assigned,
                    updated_at=_utcnow(),
                ))
            else:
                row.value_json = payload
                row.seq = assigned
                row.updated_at = _utcnow()
        return {"key": key, "value": _loads(payload), "seq": assigned}

    def _max_seq(self, session_id: str) -> int:
        with self._session() as s:
            return int(s.query(func.max(SessionProjection.seq)).filter(
                SessionProjection.session_id == session_id
            ).scalar() or 0)

    def _allocate_seq(self, session_id: str) -> int:
        """申请一个严格大于该会话旧投影的 seq。

        事件计数器在进程重启后从事件表的 max(seq) 重建，而刚重启时事件表可能
        落后于投影表（例如上一轮只更新了 todo）。直接用事件计数器会和旧投影
        撞号 —— 前端 `higher-seq-wins` 会把新快照当旧帧丢掉。因此这里取
        “事件计数器 vs 投影水位 + 1”的较大值，必要时向事件仓储连续申请（中间
        的空号对客户端的“按序补发”无影响）。
        """
        seq = _next_seq(session_id)
        current = self._max_seq(session_id)
        while seq <= current:
            seq = _next_seq(session_id)
        return seq

    def get(self, session_id: str, key: str) -> Optional[Dict[str, Any]]:
        """读一个投影的当前值；不存在返回 None。"""
        with self._session() as s:
            row = s.query(SessionProjection).filter(
                SessionProjection.session_id == session_id,
                SessionProjection.key == key,
            ).first()
            if row is None:
                return None
            return {"key": row.key, "value": _loads(row.value_json), "seq": int(row.seq or 0)}

    def all(self, session_id: str) -> Dict[str, Dict[str, Any]]:
        """读该会话的全部投影：key → {key, value, seq}。"""
        with self._session() as s:
            rows = s.query(SessionProjection).filter(
                SessionProjection.session_id == session_id,
            ).all()
            return {
                row.key: {"key": row.key, "value": _loads(row.value_json), "seq": int(row.seq or 0)}
                for row in rows
            }

    def list_frames(self, session_id: str) -> List[Dict[str, Any]]:
        """按 seq 升序返回投影帧（同 WS `session_projection` 的 data 形状）。"""
        frames = list(self.all(session_id).values())
        frames.sort(key=lambda f: f["seq"])
        return frames

    def clear(self, session_id: str) -> int:
        """删除会话全部投影（会话删除时调用），返回删除条数。"""
        try:
            with self._session() as s:
                return int(s.query(SessionProjection).filter(
                    SessionProjection.session_id == session_id
                ).delete(synchronize_session=False) or 0)
        except Exception as e:
            logger.debug(f"[Projection] 清理投影失败: {e}")
            return 0


def _next_seq(session_id: str) -> int:
    """申请一个 seq（与事件共用序号空间；仓储不可用时退回时间戳，保证单调）"""
    try:
        from modules.database.session_events import get_session_event_repo
        return get_session_event_repo().next_seq(session_id)
    except Exception as e:
        logger.debug(f"[Projection] 申请 seq 失败，退回时间戳: {e}")
        import time
        return int(time.time() * 1000)


def _dumps(value: Any) -> str:
    try:
        payload = json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)
    except Exception:
        payload = json.dumps({"_unserializable": str(value)[:500]}, ensure_ascii=False)
    return payload[:_MAX_VALUE_CHARS]


def _loads(raw: Any) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


_projection_repo: Optional[ProjectionRepository] = None
_projection_repo_lock = threading.Lock()


def get_projection_repo() -> ProjectionRepository:
    """获取全局 ProjectionRepository 单例"""
    global _projection_repo
    if _projection_repo is None:
        with _projection_repo_lock:
            if _projection_repo is None:
                _projection_repo = ProjectionRepository()
    return _projection_repo


def reset_projection_repo() -> None:
    """测试用：重置单例"""
    global _projection_repo
    with _projection_repo_lock:
        _projection_repo = None
