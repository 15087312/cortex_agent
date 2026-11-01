"""工具调用可视化事件（Phase 0/2）。

把工具执行变成**结构化**事件而不是一句中文摘要：前端据此渲染工具轨迹
（名称 / 参数 / 状态 / 耗时 / 结果预览）与工具卡片，不再靠正则猜文本。

本模块只做纯函数：脱敏、截断、载荷构造。发送与落库由 `ModelRunner` 负责
（Phase 1 起经 `session_events` 事件日志持久化，刷新后可回放；
见 docs/VISUALIZATION_DESIGN.md）。Phase 2 起载荷额外带：

- `view`：卡片渲染意图（terminal / diff / read / search / web / generic），
  由 `modules/thinking/tool_view.py` 计算，实时与回放共用；
- `meta`（仅结果事件）：卡片需要但结果预览装不下的数据（终端输出、文件行、
  来源列表、diff 行），随事件落库以便回放重建。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from modules.thinking.tool_view import build_meta, present_call, present_result

# 参数里可能出现的凭据类键名 → 值替换为掩码（只脱敏，不删除键，保留结构）
_SENSITIVE_KEY = re.compile(
    r"(api[_-]?key|access[_-]?key|secret|token|password|passwd|authorization|cookie|credential)",
    re.IGNORECASE,
)

_MAX_ARGS_CHARS = 600          # 整个参数对象的展示上限
_MAX_ARG_VALUE_CHARS = 200     # 单个参数值的展示上限
_MAX_RESULT_PREVIEW = 400      # 结果预览上限
_MAX_ERROR_CHARS = 300
_MAX_DEPTH = 4
_MAX_LIST_ITEMS = 20

_MASK = "***"

# 工具执行状态：与前端轨迹徽标一一对应
STATUS_RUNNING = "running"
STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_TIMEOUT = "timeout"
STATUS_DENIED = "denied"
STATUS_INVALID = "invalid"
STATUS_INTERRUPTED = "interrupted"


def preview(value: Any, limit: int = _MAX_RESULT_PREVIEW) -> str:
    """转成单行预览并截断，超长显式标注，避免前端拿到整段输出。"""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"…(+{len(text) - limit} chars)"


def _sanitize_value(value: Any, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        return preview(value, _MAX_ARG_VALUE_CHARS)
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, val in value.items():
            key_str = str(key)
            if _SENSITIVE_KEY.search(key_str):
                out[key_str] = _MASK
            else:
                out[key_str] = _sanitize_value(val, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        items: List[Any] = [_sanitize_value(v, depth + 1) for v in list(value)[:_MAX_LIST_ITEMS]]
        if len(value) > _MAX_LIST_ITEMS:
            items.append(f"…(+{len(value) - _MAX_LIST_ITEMS} items)")
        return items
    if isinstance(value, str):
        return preview(value, _MAX_ARG_VALUE_CHARS)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return preview(value, _MAX_ARG_VALUE_CHARS)


def sanitize_args(args: Any) -> Any:
    """参数脱敏：凭据置掩码、字符串截断、容器限深限量。

    非 dict 入参（模型给了非法 JSON 字符串）原样转预览，保证事件始终可发。
    """
    if args is None:
        return {}
    if not isinstance(args, (dict, list, tuple)):
        return preview(args, _MAX_ARG_VALUE_CHARS)
    cleaned = _sanitize_value(args)
    # 整个参数对象再有上限：字典本身很大时不至于把一帧撑爆
    try:
        serialized = json.dumps(cleaned, ensure_ascii=False, default=str)
    except Exception:
        return preview(str(cleaned), _MAX_ARGS_CHARS)
    if len(serialized) > _MAX_ARGS_CHARS:
        return preview(serialized, _MAX_ARGS_CHARS)
    return cleaned


def _event_data(call_id: str, name: str, *, tier: str, model_id: str, identity_name: str) -> Dict[str, Any]:
    return {
        "call_id": str(call_id or name or ""),
        "name": str(name or ""),
        "tier": tier or "",
        "model_id": model_id or "",
        "identity_name": identity_name or "",
    }


def build_call_payload(
    call_id: str,
    name: str,
    args: Any,
    *,
    tier: str = "",
    model_id: str = "",
    identity_name: str = "",
) -> Dict[str, Any]:
    """工具调用开始：前端据此立刻出现“运行中”轨迹行。"""
    data = _event_data(call_id, name, tier=tier, model_id=model_id, identity_name=identity_name)
    safe_args = sanitize_args(args)
    data.update({
        "phase": "call",
        "status": STATUS_RUNNING,
        "args": safe_args,
        "duration_ms": 0,
    })
    view = present_call(name, safe_args if isinstance(safe_args, dict) else {})
    if view:
        data["view"] = view
    return data


def build_result_payload(
    call_id: str,
    name: str,
    result: Any,
    *,
    status: str = STATUS_OK,
    duration_ms: float = 0.0,
    error: str = "",
    tier: str = "",
    model_id: str = "",
    identity_name: str = "",
    args: Optional[Any] = None,
) -> Dict[str, Any]:
    """工具执行结束：状态、耗时、结果预览、错误信息 + 卡片 meta/view。

    `args` 用于现算卡片（命令原文、路径、查询词）；`meta` 是回放重建卡片的
    唯一额外输入，随事件落库。
    """
    data = _event_data(call_id, name, tier=tier, model_id=model_id, identity_name=identity_name)
    result_text = "" if result is None else (result if isinstance(result, str) else str(result))
    data.update({
        "phase": "result",
        "status": status,
        "duration_ms": round(float(duration_ms or 0.0), 1),
        "result_chars": len(result_text),
        "result_preview": preview(result_text),
        "error": preview(error, _MAX_ERROR_CHARS),
    })
    safe_args = sanitize_args(args) if args is not None else {}
    view_meta = build_meta(name, safe_args if isinstance(safe_args, dict) else {}, result_text, status)
    if view_meta:
        data["meta"] = view_meta
    view = present_result(
        name,
        safe_args if isinstance(safe_args, dict) else {},
        status=status,
        meta=view_meta,
    )
    if view:
        data["view"] = view
    return data


__all__ = [
    "STATUS_RUNNING", "STATUS_OK", "STATUS_ERROR", "STATUS_TIMEOUT",
    "STATUS_DENIED", "STATUS_INVALID", "STATUS_INTERRUPTED",
    "preview", "sanitize_args", "build_call_payload", "build_result_payload",
]
