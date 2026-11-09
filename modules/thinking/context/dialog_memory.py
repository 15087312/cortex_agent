"""对话记忆公共层 — agent（多模型）与 chatonly（纯对话）共用同一套会话历史读取逻辑。

两种模式共用本模块读取【会话内对话历史】；跨会话长期记忆由 modules/memory
（EventStore / EventRetrieval / EventReducer）专门机制承担，与本层无关。

设计原则：
- DB（chat_messages）是唯一真源，不再需要任何内存黑板做中间缓存
- 超窗口的旧消息仅"不注入模型"，不删除 —— 前端仍可见、重连可恢复，避免上下文跳变
- 窗口按 token 预算（模型上下文窗口 × ratio）从最新往回截断，而非条数/字符数硬编码

说明：本层不做 LLM 摘要压缩。会话历史超出窗口后模型看不到早期内容，
但重要信息由长期记忆机制（EventReducer 提炼 + EventRetrieval 召回）兜底。

注入口径由设置页「上下文管理」三项配置决定（见 Settings）：
- CONTEXT_LIMIT_MODE = ratio    不设条数/token 上限，占用到窗口的 CONTEXT_COMPRESS_RATIO 才裁剪
- CONTEXT_LIMIT_MODE = messages 最多注入 CHAT_CONTEXT_MAX_MESSAGES 条（0 = 不限）
- CONTEXT_LIMIT_MODE = tokens   最多注入 CHAT_CONTEXT_MAX_TOKENS 个 token（0 = 不限）
"""
from typing import Any, Dict, List, Optional

from utils.logger import setup_logger

logger = setup_logger("dialog_memory")

# 非真实对话的 role：只写 DB 供前端过程面板展示，不参与模型上下文
NON_DIALOG_ROLES = frozenset({"thought", "process", "mental"})

# DB 单次读取上限：仅防御失控读取，真正注入量由 budget_trim 决定
MAX_HISTORY_SCAN = 2000


def context_config() -> Dict[str, Any]:
    """读取上下文控制配置（缺失/异常时回落到 ratio 80%）。

    配置读取失败不能让上下文构造失败：异常一律按默认口径继续。
    """
    try:
        from config.settings import settings
        mode = str(getattr(settings, "CONTEXT_LIMIT_MODE", "ratio") or "ratio").lower()
        if mode not in ("ratio", "messages", "tokens"):
            mode = "ratio"
        return {
            "mode": mode,
            "max_messages": int(getattr(settings, "CHAT_CONTEXT_MAX_MESSAGES", 0) or 0),
            "max_tokens": int(getattr(settings, "CHAT_CONTEXT_MAX_TOKENS", 0) or 0),
            "ratio": float(getattr(settings, "CONTEXT_COMPRESS_RATIO", 0.8) or 0.8),
        }
    except Exception as _swallowed_err:
        logger.debug(f"[context_config] ignored Exception: {_swallowed_err}")
        return {"mode": "ratio", "max_messages": 0, "max_tokens": 0, "ratio": 0.8}


def history_read_limit() -> int:
    """DB 读取条数上限。

    条数模式且配置了正数上限 → 只读该条数；token / 窗口比例 / 不限口径 →
    读足够多（MAX_HISTORY_SCAN），由 budget_trim 按预算裁剪，避免读取先于
    裁剪把历史截短（此前固定 100/50 条，导致长会话永远看不到更早的消息）。
    """
    cfg = context_config()
    if cfg["mode"] == "messages" and cfg["max_messages"] > 0:
        return cfg["max_messages"]
    return MAX_HISTORY_SCAN


def _context_window_size() -> int:
    """解析大模型输入上下文窗口（token 数），未配置返回 0（表示不截断）"""
    try:
        from config.settings import settings
        window = int(getattr(settings, "CONTEXT_WINDOW_SIZE", 0) or 0)
        if window > 0:
            return window
    except Exception as _swallowed_err:
        logger.debug(f"[_context_window_size] ignored Exception: {_swallowed_err}")
    try:
        from config.settings import settings
        return int(settings.get_context_length("large") or 0)
    except Exception:
        return 0


def load_dialog_from_db(
    session_id: str,
    limit: Optional[int] = None,
    repo: Any = None,
) -> List[Dict[str, Any]]:
    """从 DB 读取真实对话（唯一真源）

    过滤 thought/process/mental（只写 DB 供前端展示，不进模型上下文），
    并把 created_at(ISO) 统一转换为 timestamp(epoch)，与内存 messages 字段对齐。

    repo 可注入（测试/隔离场景）；省略时取模块级 get_session_repo()。
    limit 省略时按设置页的上下文口径决定（见 history_read_limit）。
    """
    if not session_id:
        return []
    if limit is None:
        limit = history_read_limit()
    try:
        if repo is None:
            from modules.database.session_repo import get_session_repo
            repo = get_session_repo()
        if repo is None:
            return []
        rows = repo.get_messages(session_id, limit=limit)
    except Exception as e:
        logger.debug(f"[dialog_memory] 读取对话失败: {e}")
        return []

    dialog: List[Dict[str, Any]] = []
    for m in rows:
        role = m.get("role", "")
        if role in NON_DIALOG_ROLES:
            continue
        content = m.get("content", "")
        if not content:
            continue
        ts = 0.0
        created = m.get("created_at", "")
        if created:
            try:
                from datetime import datetime as _dt
                ts = _dt.fromisoformat(created).timestamp()
            except Exception:
                ts = 0.0
        dialog.append({
            "role": role,
            "content": content,
            "timestamp": ts,
            "id": m.get("id", ""),
        })
    return dialog


def _keep_from_newest(messages: List[Dict[str, Any]], threshold: int) -> List[Dict[str, Any]]:
    """按 token 阈值从最新往回保留消息；token 计数不可用时原样返回。"""
    try:
        from modules.thinking.context.compression import get_compression_engine
        engine = get_compression_engine()
    except Exception:
        return messages

    kept: List[Dict[str, Any]] = []
    total = 0
    for m in reversed(messages):
        cost = engine.estimate_tokens(str(m.get("content", "")))
        # 至少保留最新 1 条，即使单条就超预算
        if kept and total + cost > threshold:
            break
        kept.append(m)
        total += cost
        if total > threshold:
            break
    kept.reverse()
    return kept


# @link:block DialogBudget_Guard = Loop(Guard)
def budget_trim(
    messages: List[Dict[str, Any]],
    ratio: Optional[float] = None,
    window_size: Optional[int] = None,
    mode: Optional[str] = None,
    max_messages: Optional[int] = None,
    max_tokens: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """按设置页配置的上下文口径从最新往回保留消息（纯函数，不修改任何存储）

    口径（CONTEXT_LIMIT_MODE）：
    - messages：最多保留 max_messages 条（0 = 不限）
    - tokens：保留到累计 token 超过 max_tokens（0 = 不限）
    - ratio：保留窗口 × ratio 的 token 预算（窗口未知/为 0 = 不限）

    窗口控制只作用于【读取视图】：超预算的旧消息仅"不注入模型"，
    DB 不受影响（旧实现的破坏性裁剪会导致前端与模型看到不同历史、
    且重连后被裁消息复活）。

    参数缺省时从 settings 读取；显式传入仅供测试/特殊调用覆盖。
    """
    if not messages:
        return messages

    cfg = context_config()
    mode = (mode or cfg["mode"]).lower()
    if mode not in ("ratio", "messages", "tokens"):
        mode = "ratio"

    if mode == "messages":
        limit = cfg["max_messages"] if max_messages is None else int(max_messages)
        if limit <= 0:
            return messages  # 不限条数
        kept = messages[-limit:]
    elif mode == "tokens":
        threshold = cfg["max_tokens"] if max_tokens is None else int(max_tokens)
        if threshold <= 0:
            return messages  # 不限 token
        kept = _keep_from_newest(messages, threshold)
    else:
        if ratio is None:
            ratio = cfg["ratio"]
        if window_size is None:
            window_size = _context_window_size()
        if not window_size or window_size <= 0:
            return messages  # 窗口未知 → 不截断
        threshold = int(window_size * ratio)
        kept = _keep_from_newest(messages, threshold)

    dropped = len(messages) - len(kept)
    if dropped > 0:
        logger.info(
            f"[dialog_memory] 口径={mode}，注入最近 {len(kept)} 条，"
            f"预算外 {dropped} 条仅展示不注入（DB 不受影响）"
        )
    return kept


def get_dialog(
    session_id: str,
    fallback: Optional[List[Dict[str, Any]]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """获取会话真实对话：DB 优先（唯一真源），fallback（内存缓存）兜底

    与前端取数同源，保证前端展示的对话过程 = 注入模型的对话历史数据来源一致。
    """
    dialog = load_dialog_from_db(session_id, limit=limit)
    if dialog:
        return dialog
    if not fallback:
        return []
    return [m for m in fallback if m.get("role") not in NON_DIALOG_ROLES]
