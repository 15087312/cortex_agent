"""会话投影发射器（Phase 4）— 把派生状态作为整值快照推给前端。

设计见 docs/VISUALIZATION_DESIGN.md §4.3：

- 帧形状 `{"type": "session_projection", "event": "projection", "data": {key, value, seq}}`，
  `seq` 与事件日志共用每会话计数器；
- 四个 key：`todos` / `runners` / `tokenUsage` / `contextPressure`；
- 投影**不进** `session_events`（每次状态变化追加一行会撑大表），只 upsert 到
  `session_projections`，刷新时读一次即恢复；
- 所有发射函数都在思考循环里被调用，异常一律吞掉（状态显示不能中断对话）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.logger import setup_logger

logger = setup_logger("projections")

# 投影 key（前端按 key 覆盖，未知 key 直接忽略）
PROJECTION_KEYS = ("todos", "runners", "tokenUsage", "contextPressure")


# ── 发射 ──────────────────────────────────────────────────────────────

def emit_projection(session_id: str, key: str, value: Any) -> Optional[Dict[str, Any]]:
    """覆盖写一个投影并推送整值快照帧；失败返回 None（不抛）。"""
    if not session_id or not key:
        return None
    try:
        from modules.database.session_projections import get_projection_repo
        frame = get_projection_repo().upsert(session_id, key, value)
    except Exception as e:
        logger.debug(f"[Projection] 写入失败 ({key}): {e}")
        return None
    _push_frame(session_id, frame)
    return frame


def _push_frame(session_id: str, frame: Dict[str, Any]) -> None:
    """推送 `session_projection` 帧（无连接时静默跳过）。"""
    try:
        from modules.thinking.api_stream import _build_event, connection_manager
        envelope = _build_event(
            session_id=session_id,
            msg_type="session_projection",
            event="projection",
            content="",
            role="system",
            data=frame,
        )
        envelope["_seq"] = int(frame.get("seq") or 0)
        connection_manager.send_json_from_thread(session_id, envelope)
    except Exception as e:
        logger.debug(f"[Projection] 推送失败: {e}")


def push_current(session_id: str) -> int:
    """把当前全部投影补发给该会话（WS 连上时调用），返回补发条数。"""
    sent = 0
    for frame in current_projections(session_id):
        _push_frame(session_id, frame)
        sent += 1
    return sent


def current_projections(session_id: str) -> List[Dict[str, Any]]:
    """当前投影帧列表（按 seq 升序）；仓储不可用返回空列表。"""
    if not session_id:
        return []
    try:
        from modules.database.session_projections import get_projection_repo
        return get_projection_repo().list_frames(session_id)
    except Exception as e:
        logger.debug(f"[Projection] 读取失败: {e}")
        return []


def clear_projections(session_id: str) -> None:
    """会话删除时清理投影。"""
    try:
        from modules.database.session_projections import get_projection_repo
        get_projection_repo().clear(session_id)
    except Exception as e:
        logger.debug(f"[Projection] 清理失败: {e}")


# ── 构建：todos ────────────────────────────────────────────────────────

def build_todos(session_id: str) -> Dict[str, Any]:
    """todo 列表整值快照（与 `/management/todos` 同源，前端不再需要额外拉取）。"""
    todos: List[Any] = []
    try:
        from infra.tool_manager.tools.todo import _load_todos
        todos = _load_todos(session_id) or []
    except Exception as e:
        logger.debug(f"[Projection] 读取 todo 失败: {e}")
    if not isinstance(todos, list):
        todos = []
    done = sum(1 for t in todos if isinstance(t, dict) and t.get("status") == "completed")
    return {"todos": todos, "done": done, "total": len(todos)}


# ── 构建：runners / contextPressure ───────────────────────────────────

def build_runners(session_id: str) -> Dict[str, Any]:
    """在线模型状态整值快照（含每模型上下文占用）。

    形状与 `status/thinking_progress` 帧的 data 同构，前端可用同一套扁平化逻辑。
    """
    from modules.thinking.api_stream import _resolve_context_window
    large_window = _resolve_context_window("large")
    snapshot: Dict[str, Any] = {
        "large_model": None, "active_supervisors": [], "active_experts": [],
        "context_tokens": 0, "context_window_size": large_window,
    }
    try:
        from modules.thinking.core.model_runner import (
            _runner_managers,
            _runner_managers_lock,
        )
        with _runner_managers_lock:
            rm = _runner_managers.get(session_id)
        if not rm:
            return snapshot
        for info in rm.list_runners():
            tier = info.get("tier", "")
            entry = {
                "name": info.get("name") or info.get("role", ""),
                "role": info.get("role", ""),
                "model_id": info.get("model_id", ""),
                "status": info.get("status", ""),
                "status_detail": info.get("status_detail", ""),
                "round": info.get("round", 0),
                "supervisor": info.get("supervisor", ""),
                "context_tokens": info.get("context_tokens", 0),
                "context_window_size": info.get("context_window_size", 0),
            }
            if tier == "large":
                snapshot["large_model"] = entry
                if entry["context_tokens"]:
                    snapshot["context_tokens"] = entry["context_tokens"]
                    snapshot["context_window_size"] = entry["context_window_size"] or large_window
            elif tier == "supervisor":
                snapshot["active_supervisors"].append(entry)
            elif tier == "expert":
                snapshot["active_experts"].append(entry)
    except Exception as e:
        logger.debug(f"[Projection] 收集模型状态失败: {e}")
    return snapshot


def build_context_pressure(runners: Dict[str, Any]) -> Dict[str, Any]:
    """上下文占用投影：`{tokens, window, ratio}`，`window` 未知时 ratio 记 0。

    `ratio` 是给进度条用的 0–1 值（超窗时截到 1.0）；真实超窗情况由 `tokens > window`
    表达，前端需要时自行判断。
    """
    data = runners if isinstance(runners, dict) else {}
    tokens = _int(data.get("context_tokens"))
    window = _int(data.get("context_window_size"))
    ratio = 0.0
    if window > 0:
        ratio = min(max(tokens / window, 0.0), 1.0)
    return {"tokens": tokens, "window": window, "ratio": round(ratio, 4)}


def emit_runner_state(session_id: str) -> Dict[str, Any]:
    """回合末发射 `runners` + `contextPressure`（一次收集，两次写入）。"""
    runners = build_runners(session_id)
    emit_projection(session_id, "runners", runners)
    emit_projection(session_id, "contextPressure", build_context_pressure(runners))
    return runners


# ── 构建：tokenUsage ──────────────────────────────────────────────────

def record_token_usage(session_id: str, tier: str,
                       usage: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """累加一次模型响应的 token 用量并发射 `tokenUsage` 投影。

    读改写投影（而不是记在 runner 实例上）：主管/专家 runner 任务结束即销毁，
    计数必须落在会话级存储里才准确；并发由仓储串行写保证最终一致。
    """
    usage = usage if isinstance(usage, dict) else {}
    prompt = _int(usage.get("prompt_tokens"))
    completion = _int(usage.get("completion_tokens"))
    total = _int(usage.get("total_tokens")) or (prompt + completion)
    if not (prompt or completion or total):
        return None
    value = _usage_value(_read_value(session_id, "tokenUsage"))
    value["prompt"] += prompt
    value["completion"] += completion
    value["total"] += total
    tier_key = str(tier or "large")
    bucket = value["by_tier"].setdefault(tier_key, {"prompt": 0, "completion": 0, "total": 0})
    bucket["prompt"] += prompt
    bucket["completion"] += completion
    bucket["total"] += total
    frame = emit_projection(session_id, "tokenUsage", value)
    return frame["value"] if frame else value


def _read_value(session_id: str, key: str) -> Any:
    try:
        from modules.database.session_projections import get_projection_repo
        row = get_projection_repo().get(session_id, key)
        return row.get("value") if row else None
    except Exception as e:
        logger.debug(f"[Projection] 读取 {key} 失败: {e}")
        return None


def _usage_value(raw: Any) -> Dict[str, Any]:
    value: Dict[str, Any] = {"prompt": 0, "completion": 0, "total": 0, "by_tier": {}}
    if not isinstance(raw, dict):
        return value
    for field in ("prompt", "completion", "total"):
        value[field] = _int(raw.get(field))
    by_tier = raw.get("by_tier")
    if isinstance(by_tier, dict):
        for tier_key, stats in by_tier.items():
            if not isinstance(stats, dict):
                continue
            value["by_tier"][str(tier_key)] = {
                "prompt": _int(stats.get("prompt")),
                "completion": _int(stats.get("completion")),
                "total": _int(stats.get("total")),
            }
    return value


def _int(raw: Any) -> int:
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0
