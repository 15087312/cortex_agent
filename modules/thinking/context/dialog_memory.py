"""对话记忆公共层 — agent（多模型）与 chatonly（纯对话）共用同一套会话历史读取逻辑。

两种模式共用本模块读取【会话内对话历史】；跨会话长期记忆由 modules/memory
（EventStore / EventRetrieval / EventReducer）专门机制承担，与本层无关。

设计原则：
- DB（chat_messages）是唯一真源，不再需要任何内存黑板做中间缓存
- 超窗口的旧消息仅"不注入模型"，不删除 —— 前端仍可见、重连可恢复，避免上下文跳变
- 窗口按 token 预算（模型上下文窗口 × ratio）从最新往回截断，而非条数/字符数硬编码

说明：本层不做 LLM 摘要压缩。会话历史超出窗口后模型看不到早期内容，
但重要信息由长期记忆机制（EventReducer 提炼 + EventRetrieval 召回）兜底。
"""
from typing import Any, Dict, List, Optional

from utils.logger import setup_logger

logger = setup_logger("dialog_memory")

# 非真实对话的 role：只写 DB 供前端过程面板展示，不参与模型上下文
NON_DIALOG_ROLES = frozenset({"thought", "process", "mental"})


def _context_window_size() -> int:
    """解析大模型输入上下文窗口（token 数），未配置返回 0（表示不截断）"""
    try:
        from config.settings import settings
        window = int(getattr(settings, "CONTEXT_WINDOW_SIZE", 0) or 0)
        if window > 0:
            return window
    except Exception:
        pass
    try:
        from config.settings import settings
        return int(settings.get_context_length("large") or 0)
    except Exception:
        return 0


def load_dialog_from_db(
    session_id: str,
    limit: int = 100,
    repo: Any = None,
) -> List[Dict[str, Any]]:
    """从 DB 读取真实对话（唯一真源）

    过滤 thought/process/mental（只写 DB 供前端展示，不进模型上下文），
    并把 created_at(ISO) 统一转换为 timestamp(epoch)，与内存 messages 字段对齐。

    repo 可注入（测试/隔离场景）；省略时取模块级 get_session_repo()。
    """
    if not session_id:
        return []
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


def budget_trim(
    messages: List[Dict[str, Any]],
    ratio: float = 0.8,
    window_size: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """按 token 预算从最新往回保留消息（纯函数，不修改任何存储）

    窗口控制只作用于【读取视图】：超窗口的旧消息仅"不注入模型"，
    DB 不受影响（旧实现的破坏性裁剪会导致前端与模型看到不同历史、
    且重连后被裁消息复活）。
    """
    if not messages:
        return messages

    if window_size is None:
        window_size = _context_window_size()
    if not window_size or window_size <= 0:
        return messages  # 窗口未知 → 不截断

    try:
        from modules.thinking.context.compression import get_compression_engine
        engine = get_compression_engine()
    except Exception:
        return messages

    threshold = int(window_size * ratio)
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

    dropped = len(messages) - len(kept)
    if dropped > 0:
        logger.info(
            f"[dialog_memory] token 预算 {threshold}，注入最近 {len(kept)} 条，"
            f"窗口外 {dropped} 条仅展示不注入（DB 不受影响）"
        )
    return kept


def get_dialog(
    session_id: str,
    fallback: Optional[List[Dict[str, Any]]] = None,
    limit: int = 100,
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
