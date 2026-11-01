"""可应答请求待办表（Phase 4.b）— 审批/提问的整快照与恢复链路。

为什么需要：
- 用户没点「批准/拒绝」或没回答之前，工具调用（或提问）一直挂在 Future 上等；
- 这两类请求目前只以一次 `thinking` 事件（`role='security'`）出现，前端刷新时
  会跳过 security 行 → 横幅消失、按钮点不到，回合就永久挂住；
- 这里维护一份会话级待办表，任何变化都推**整快照**（`session_request` 帧），
  刷新/重连后前端按快照重建横幅即可继续作答。

为什么不落库：等待中的 Future 在进程内存里，进程重启后这些请求本来也无法
resolve，持久化只会让前端看到一批点不动的死按钮。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from utils.logger import setup_logger

logger = setup_logger("pending_requests")

KIND_APPROVAL = "approval"
KIND_QUESTION = "question"

# 审批类事件的动作串（由 security gate 的 event_type 传入，中文文案在 UI 侧固定）
_APPROVAL_ACTION_MARK = "等待用户审批"
_QUESTION_ACTION = "user_intent_request"


class PendingRequestRegistry:
    """会话级待处理请求（审批 / 提问）。线程安全：思考循环登记、WS 线程 resolve。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._by_session: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def register(self, session_id: str, request_id: str, kind: str,
                 **fields: Any) -> Dict[str, Any]:
        """登记一个待处理请求（同 request_id 幂等，重复登记只刷新字段）。"""
        record = {
            "request_id": request_id,
            "kind": kind,
            "session_id": session_id,
            "created_at": time.time(),
        }
        record.update({k: v for k, v in fields.items() if v is not None})
        with self._lock:
            bucket = self._by_session.setdefault(session_id, {})
            previous = bucket.get(request_id)
            if previous:                      # 重复登记：保留首次时间与已确定的 kind
                record["created_at"] = previous.get("created_at", record["created_at"])
                record["kind"] = previous.get("kind", kind)
                record = {**previous, **record}
            bucket[request_id] = record
        return dict(record)

    def resolve(self, session_id: str, request_id: str,
                outcome: Optional[Dict[str, Any]] = None) -> bool:
        """从待办表移除（返回是否真的存在过）。"""
        with self._lock:
            bucket = self._by_session.get(session_id) or {}
            record = bucket.pop(request_id, None)
            if not bucket:
                self._by_session.pop(session_id, None)
        if record is None:
            return False
        logger.debug(f"[Pending] 请求已结束: {session_id[:8]} {request_id} {outcome or {}}")
        return True

    def snapshot(self, session_id: str) -> List[Dict[str, Any]]:
        """当前待处理请求列表（按登记时间升序）。"""
        with self._lock:
            bucket = self._by_session.get(session_id) or {}
            records = [dict(r) for r in bucket.values()]
        records.sort(key=lambda r: r.get("created_at") or 0)
        return records

    def has(self, session_id: str, request_id: str) -> bool:
        with self._lock:
            return request_id in (self._by_session.get(session_id) or {})

    def clear(self, session_id: str) -> int:
        """清空会话的待办（断线 / 停止 / 会话删除）。"""
        with self._lock:
            return len(self._by_session.pop(session_id, {}) or {})

    def reset(self) -> None:
        """测试用：清空全部会话"""
        with self._lock:
            self._by_session.clear()


_registry: Optional[PendingRequestRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> PendingRequestRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = PendingRequestRegistry()
    return _registry


def reset_registry() -> None:
    """测试用：重置单例"""
    global _registry
    with _registry_lock:
        _registry = None


# ── 与事件流对接 ──────────────────────────────────────────────────────

def register_from_stage_event(session_id: str, formatted: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从 `_format_scheduler_event` 的结果里识别可应答请求并登记。

    只认 `event_type == 'security'` 且带 `request_id` 的两类动作：安全审批
    （`等待用户审批`）与模型提问（`user_intent_request`）。登记成功即推整快照，
    前端据此显示/恢复横幅。
    """
    data = (formatted or {}).get("data") or {}
    if data.get("event_type") != "security":
        return None
    payload = data.get("payload") or {}
    request_id = str(payload.get("request_id") or "")
    if not request_id:
        return None
    action = str(data.get("action") or "")
    if _APPROVAL_ACTION_MARK in action:
        record = get_registry().register(
            session_id, request_id, KIND_APPROVAL,
            target=str(data.get("target") or payload.get("caller") or ""),
            detail=str(payload.get("detail") or ""),
        )
    elif _QUESTION_ACTION in action:
        options = payload.get("options")
        record = get_registry().register(
            session_id, request_id, KIND_QUESTION,
            question=str(payload.get("question") or payload.get("detail") or ""),
            options=list(options) if isinstance(options, (list, tuple)) else [],
            context=str(payload.get("context") or ""),
        )
    else:
        return None
    broadcast(session_id)
    return record


def resolve_and_broadcast(session_id: str, request_id: str,
                          outcome: Optional[Dict[str, Any]] = None) -> bool:
    """结束一个待办并推快照（WS 收到响应后调用）。"""
    if not session_id or not request_id:
        return False
    changed = get_registry().resolve(session_id, request_id, outcome)
    if changed:
        broadcast(session_id)
    return changed


def broadcast(session_id: str) -> List[Dict[str, Any]]:
    """把当前待办整快照推给前端（`session_request` 帧，无 seq：幂等快照）。"""
    requests = get_registry().snapshot(session_id)
    try:
        from modules.thinking.api_stream import _build_event, connection_manager
        envelope = _build_event(
            session_id=session_id,
            msg_type="session_request",
            event="requests_snapshot",
            content="",
            role="system",
            data={"requests": requests},
        )
        connection_manager.send_json_from_thread(session_id, envelope)
    except Exception as e:
        logger.debug(f"[Pending] 推送待办快照失败 (非致命): {e}")
    return requests


def clear(session_id: str) -> int:
    """清空会话待办（断线、停止、会话清理）"""
    return get_registry().clear(session_id)
