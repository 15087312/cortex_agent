"""
ContextSlicer — 滑动窗口 + 分块摘要的上下文管理

策略：
1. 保留最近 15 条消息作为当前窗口（全文保留）
2. 更早的历史消息拼接成文本，每满 1000 字调用一次 LLM 生成重点摘要
3. 所有历史摘要合并成一条系统消息，插入窗口最前面
4. 结合记忆上下文与 token 预算做最终裁剪
"""
import asyncio
from typing import List

from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("context_slicer")

CHARS_PER_TOKEN = 3
_MAX_SUMMARIZE_CONCURRENCY = 2
_SUMMARIZE_SEM = asyncio.Semaphore(_MAX_SUMMARIZE_CONCURRENCY)


class ContextSlicer:
    """上下文裁剪器：滑动窗口 + 历史分块摘要"""

    _tiktoken_enc = None

    def __init__(self, window_size: int = 15, chunk_chars: int = 1000):
        self.window_size = window_size
        self.chunk_chars = chunk_chars

    async def slice(
        self,
        messages: List[dict],
        memory_context: str = "",
        max_tokens: int = None,
    ) -> List[dict]:
        if max_tokens is None:
            max_tokens = settings.CHAT_CONTEXT_MAX_TOKENS

        # 1. 分离最近 window_size 条和更早的历史
        if len(messages) <= self.window_size:
            window = list(messages)
            history = []
        else:
            window = list(messages[-self.window_size:])
            history = messages[:-self.window_size]

        # 2. 将历史消息拼接成文本，按 chunk_chars 分块，并发生成摘要
        num_chunks = 0
        if history:
            history_text = ""
            for m in history:
                role = m.get("role", "")
                content = m.get("content", "")
                history_text += f"{role}: {content}\n"

            chunks = self._split_text_into_chunks(history_text)
            num_chunks = len(chunks)

            summaries = await asyncio.gather(
                *[self._summarize_chunk(chunk) for chunk in chunks],
                return_exceptions=True,
            )

            valid_summaries = [s for s in summaries if isinstance(s, str) and s]
            if valid_summaries:
                combined = "；".join(valid_summaries)
                window.insert(0, {
                    "role": "system",
                    "content": f"[历史对话重点摘要] {combined}",
                })

        result_messages = window

        # 3. 按 token 预算从后向前裁剪
        trimmed = []
        used_tokens = 0

        # 记忆上下文预先计入 token
        if memory_context and memory_context.strip():
            mem_tokens = self._estimate_tokens(memory_context)
            used_tokens += mem_tokens

        for msg in reversed(result_messages):
            msg_tokens = self._estimate_tokens(msg.get("content", ""))
            if used_tokens + msg_tokens > max_tokens * 0.8:
                break
            trimmed.insert(0, msg)
            used_tokens += msg_tokens

        # 记忆上下文始终放在最前面
        if memory_context and memory_context.strip():
            trimmed.insert(0, {
                "role": "system",
                "content": f"以下是从历史记忆中检索到的相关信息：\n\n{memory_context}",
            })

        logger.info(
            f"[Slicer] 窗口大小={self.window_size} 历史块数={num_chunks} "
            f"输出消息数={len(trimmed)} 预估 tokens={used_tokens}"
        )
        return trimmed

    def _split_text_into_chunks(self, text: str) -> List[str]:
        """将文本按 chunk_chars 切分成多个块（尽量保持句子完整）"""
        chunks = []
        start = 0
        n = len(text)
        while start < n:
            end = min(start + self.chunk_chars, n)
            # 向后寻找最近的换行或标点作为切割点，避免截断句子
            if end < n:
                search_start = max(start, end - 50)
                best = end
                for i in range(end, search_start - 1, -1):
                    if text[i] in "\n。！？.!?":
                        best = i + 1
                        break
                end = best
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            start = end
        return chunks

    async def _summarize_chunk(self, chunk: str) -> str:
        """为一段对话文本生成重点摘要"""
        if not chunk.strip():
            return ""

        prompt = (
            "你是一个对话摘要助手。请阅读以下对话片段，提取最关键的信息。\n\n"
            "要求：\n"
            "- 用中文概括用户的核心问题、助手给出的重要信息或结论\n"
            "- 如果包含多个小话题，可以并列摘要，但总长度控制在 80 字以内\n"
            "- 不要包含「用户说」「助手说」等元描述，直接表达信息\n"
            "- 如果完全是闲聊，输出「闲聊」\n\n"
            "对话：\n"
            f"{chunk}\n\n"
            "摘要："
        )

        try:
            from infra.model.large_model_client import LargeModelClient
            client = LargeModelClient()
            async with _SUMMARIZE_SEM:
                summary = await client.generate(prompt, max_tokens=120, temperature=0.3)
            summary = summary.strip()
            if summary.startswith("摘要："):
                summary = summary[3:].strip()
            return summary
        except Exception as e:
            logger.warning(f"历史摘要生成失败，降级处理: {e}")
            return chunk.replace("\n", " ")[:30] + "..."

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        if not text:
            return 0
        try:
            if ContextSlicer._tiktoken_enc is None:
                import tiktoken
                ContextSlicer._tiktoken_enc = tiktoken.get_encoding("cl100k_base")
            return len(ContextSlicer._tiktoken_enc.encode(text))
        except Exception:
            return max(1, len(text) // CHARS_PER_TOKEN)
