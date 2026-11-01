"""会话事件日志（Phase 1）— 让刷新后的过程视图与运行中一致。

设计见 docs/VISUALIZATION_DESIGN.md §4.1/§4.3：

- WS 帧本身就是事件，服务端 append 时分配每会话单调递增的 `seq`（客户端永不生成）；
- 只有“需要回放”的事件类型落库（progress / thinking_progress / ack 等瞬态事件只进
  进程内环形缓冲，避免每秒心跳把表撑大）；
- 前端刷新或重新打开会话时按 `seq` 拉取，用与运行时相同的处理函数重建工具轨迹。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, func
from sqlalchemy.exc import IntegrityError

from modules.database.connection import Base, get_db_manager
from utils.logger import setup_logger

logger = setup_logger("session_events")

# 落库的事件类型（envelope.type）。其余（progress / thinking_progress / ack /
# ping / pong / sync_complete / chunk）是瞬态状态，刷新后没有重建价值。
#
# `user_message` 只存用户消息的 id（不存正文）：回放时用它给工具事件定位
# “发生在哪条消息之后”，刷新后工具卡片才能回到原来的位置。
DURABLE_TYPES = frozenset({
    "thinking", "message", "user_message", "tool", "mental", "error", "done", "todo",
})

_MAX_CONTENT_CHARS = 8000     # 单条事件正文上限（思考文本/消息正文）
_MAX_DATA_CHARS = 20000       # data JSON 上限（工具结果预览已在上游截断）
_MAX_LIST_LIMIT = 2000        # 单次读取上限，避免一帧过大


def _utcnow() -> datetime:
    """naive UTC now（与 chat_models 一致）"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SessionEvent(Base):
    """会话事件（append-only；主键 (session_id, seq)）"""

    __tablename__ = "session_events"

    session_id = Column(String(100), primary_key=True)
    seq = Column(Integer, primary_key=True)
    type = Column(String(40), nullable=False, default="")
    event = Column(String(60), nullable=False, default="")
    role = Column(String(20), default="")
    content = Column(Text, default="")
    data_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=_utcnow, index=True)

    __table_args__ = (
        Index("ix_session_events_type", "session_id", "type", "seq"),
    )


class SessionEventRepository:
    """事件写入与读取。seq 由服务端分配，按 session 隔离。"""

    def __init__(self):
        self._db = get_db_manager()
        self._lock = threading.Lock()
        self._next: Dict[str, int] = {}   # session_id → 下一个可用 seq

    def _session(self):
        return self._db.get_session()

    def _max_seq(self, session_id: str) -> int:
        with self._session() as s:
            value = s.query(func.max(SessionEvent.seq)).filter(
                SessionEvent.session_id == session_id
            ).scalar()
        return int(value or 0)

    def _reserve_seq(self, session_id: str) -> int:
        """在当前进程内预留 seq（跨进程写入时由主键冲突兜底重试）。"""
        with self._lock:
            nxt = self._next.get(session_id)
            if nxt is None:
                nxt = self._max_seq(session_id) + 1
            self._next[session_id] = nxt + 1
            return nxt

    def next_seq(self, session_id: str) -> int:
        """分配下一个 seq，不写事件。

        给非事件载荷（Phase 4 的 `session_projection` 投影）用：与事件共用同一个
        每会话计数器，客户端才能把两类帧放在一个序号空间里比较新旧。
        """
        return self._reserve_seq(session_id)

    def append(
        self,
        session_id: str,
        *,
        type: str = "",
        event: str = "",
        role: str = "",
        content: str = "",
        data: Optional[Dict[str, Any]] = None,
        seq: Optional[int] = None,
        created_at: Optional[datetime] = None,
    ) -> int:
        """写入一条事件，返回其 seq（seq 冲突时按库内最大值重试一次）。"""
        try:
            payload = json.dumps(data or {}, ensure_ascii=False, default=str)
        except Exception:
            payload = json.dumps({"_unserializable": str(data)[:500]}, ensure_ascii=False)
        if len(payload) > _MAX_DATA_CHARS:
            payload = payload[:_MAX_DATA_CHARS]
        text = content or ""
        if len(text) > _MAX_CONTENT_CHARS:
            text = text[:_MAX_CONTENT_CHARS]

        assigned = int(seq) if seq else self._reserve_seq(session_id)
        for attempt in range(2):
            try:
                with self._session() as s:
                    s.add(SessionEvent(
                        session_id=session_id,
                        seq=assigned,
                        type=type or "",
                        event=event or "",
                        role=role or "",
                        content=text,
                        data_json=payload,
                        created_at=created_at or _utcnow(),
                    ))
                return assigned
            except IntegrityError:
                if attempt:
                    raise
                # 其他写入者已占用该 seq → 对齐库内水位后重试
                with self._lock:
                    self._next[session_id] = self._max_seq(session_id) + 1
                assigned = self._reserve_seq(session_id)
        return assigned

    def append_envelope(self, session_id: str, envelope: Dict[str, Any],
                        persist: Optional[bool] = None) -> int:
        """给 WS envelope 分配 seq，必要时落库，返回 seq（并写回 envelope['_seq']）。"""
        msg_type = str(envelope.get("type") or "")
        durable = (msg_type in DURABLE_TYPES) if persist is None else bool(persist)
        seq = self._reserve_seq(session_id)
        envelope["_seq"] = seq
        if durable:
            try:
                data = envelope.get("data")
                self.append(
                    session_id,
                    type=msg_type,
                    event=str(envelope.get("event") or ""),
                    role=str(envelope.get("role") or ""),
                    content=envelope.get("content") or "",
                    data=data if isinstance(data, dict) else {"value": data},
                    seq=seq,
                )
            except Exception as e:
                # 落库失败不影响实时推送（刷新后少了这一条，但不阻塞对话）
                logger.debug(f"[SessionEvent] 事件落库失败 (非致命): {e}")
        return seq

    def last_seq(self, session_id: str) -> int:
        """已分配的最大 seq（含尚未落库的瞬态事件）。"""
        with self._lock:
            reserved = self._next.get(session_id, 1) - 1
        return max(reserved, self._max_seq(session_id))

    def list_events(self, session_id: str, after_seq: int = 0,
                    limit: int = 500) -> List[Dict[str, Any]]:
        """按 seq 升序读取事件，返回与 WS 帧同构的 envelope 列表。"""
        after = int(after_seq or 0)
        size = max(1, min(int(500 if limit is None else limit), _MAX_LIST_LIMIT))
        with self._session() as s:
            rows = s.query(SessionEvent).filter(
                SessionEvent.session_id == session_id,
                SessionEvent.seq > after,
            ).order_by(SessionEvent.seq.asc()).limit(size).all()
            out: List[Dict[str, Any]] = []
            for row in rows:
                out.append({
                    "type": row.type,
                    "event": row.event,
                    "session_id": row.session_id,
                    "role": row.role or "",
                    "content": row.content or "",
                    "data": _loads(row.data_json),
                    "_seq": row.seq,
                    "timestamp": row.created_at.timestamp() if row.created_at else 0.0,
                })
        return out

    def clear(self, session_id: str) -> int:
        """删除会话的全部事件（会话删除时调用），返回删除条数。"""
        with self._lock:
            self._next.pop(session_id, None)
        try:
            with self._session() as s:
                return int(s.query(SessionEvent).filter(
                    SessionEvent.session_id == session_id
                ).delete(synchronize_session=False) or 0)
        except Exception as e:
            logger.debug(f"[SessionEvent] 清理事件失败: {e}")
            return 0


def _loads(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {"value": value}


_event_repo: Optional[SessionEventRepository] = None
_event_repo_lock = threading.Lock()


def get_session_event_repo() -> SessionEventRepository:
    """获取全局 SessionEventRepository 单例"""
    global _event_repo
    if _event_repo is None:
        with _event_repo_lock:
            if _event_repo is None:
                _event_repo = SessionEventRepository()
    return _event_repo


def reset_session_event_repo() -> None:
    """测试用：重置单例"""
    global _event_repo
    with _event_repo_lock:
        _event_repo = None
