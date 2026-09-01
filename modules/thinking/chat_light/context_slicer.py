"""ContextSlicer — 会话历史裁剪（chatonly 纯对话）

与 agent 模式共用公共层 modules.thinking.context.dialog_memory：
- DB 为唯一真源直读对话历史（不再需要 Blackboard 做中间缓存）
- 按 token 预算从最新往回截断（替代原先的条数 + 字符数口径）
- 超窗口的旧消息仅"不注入模型"，不删除；重要信息由长期记忆机制
  （EventReducer 提炼 + EventRetrieval 召回）兜底

原「旧消息 LLM 摘要」能力已按决策移除，与 agent 模式行为保持一致。
"""
from typing import List

from utils.logger import setup_logger
from modules.thinking.context.dialog_memory import budget_trim, load_dialog_from_db

logger = setup_logger("context_slicer")


class ContextSlicer:
    """会话历史裁剪器：DB 直读 + token 预算截断"""

    def __init__(self, window_size: int = None, chunk_chars: int = 1000):
        # window_size / chunk_chars 已废弃（窗口改由 token 预算控制），
        # 保留形参仅为兼容既有调用方。
        self.window_size = window_size
        self.chunk_chars = chunk_chars

    async def slice(
        self,
        messages: List[dict],
        memory_context: str = "",
        max_chars: int = None,
        session_id: str = None,
    ) -> List[dict]:
        """裁剪会话历史，返回可直接送入模型的 messages。

        session_id 给定时以 DB 为唯一真源直读；DB 无记录才回退到传入的
        messages（兼容无 repo 场景）。max_chars 已废弃，仅保留签名兼容。
        """
        dialog = load_dialog_from_db(session_id) if session_id else []
        source = dialog or messages or []

        kept = budget_trim(list(source))

        result: List[dict] = []
        if memory_context and memory_context.strip():
            result.append({
                "role": "system",
                "content": f"以下是从历史记忆中检索到的相关信息：\n\n{memory_context}",
            })
        result.extend(kept)

        logger.info(
            f"[Slicer] 来源={'DB' if dialog else '入参'} "
            f"注入={len(kept)} 条（窗口外 {len(source) - len(kept)} 条不注入）"
        )
        return result
