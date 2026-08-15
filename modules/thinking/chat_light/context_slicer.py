"""
ContextSlicer — 会话记忆裁剪：前 15 条全文 + 总上限字数 + 超出部分 LLM 总结

策略：
1. 保留最近 window_size(15) 条消息全文（不逐条截断）
2. 更早的历史从新到旧累计，直到总字数达到 CHAT_CONTEXT_MAX_CHARS 上限
3. 超出上限的最旧部分，调用 LLM 总结成一条系统摘要（不直接丢弃，可追溯）
4. 记忆上下文前置
"""
import asyncio
from typing import List

from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("context_slicer")

_MAX_SUMMARIZE_CONCURRENCY = 2
_SUMMARIZE_SEM = asyncio.Semaphore(_MAX_SUMMARIZE_CONCURRENCY)


class ContextSlicer:
    """会话记忆裁剪器：前 N 条全文 + 超上限旧内容 LLM 总结"""

    def __init__(self, window_size: int = 15, chunk_chars: int = 1000):
        self.window_size = window_size
        self.chunk_chars = chunk_chars
        self._client = None  # 懒建一次，复用避免每块新建 aiohttp session
        self._client_cfg = None  # 配置指纹：模型配置变更时自动重建（实时生效）

    def _get_client(self):
        from infra.model.config_fingerprint import (
            model_config_fingerprint, close_client_session,
        )
        cfg = model_config_fingerprint("large")
        if self._client is None or self._client_cfg != cfg:
            old = self._client
            from infra.model.large_model_client import LargeModelClient
            self._client = LargeModelClient()
            self._client_cfg = cfg
            close_client_session(old)
        return self._client

    async def slice(
        self,
        messages: List[dict],
        memory_context: str = "",
        max_chars: int = None,
    ) -> List[dict]:
        if max_chars is None:
            max_chars = settings.CHAT_CONTEXT_MAX_CHARS

        # 1. 分离最近 window_size 条（窗口全文）与更早历史
        if len(messages) <= self.window_size:
            window = list(messages)
            history = []
        else:
            window = list(messages[-self.window_size:])
            history = messages[:-self.window_size]

        # 2. 从新到旧累计，超出上限的最旧部分交给 LLM 总结
        to_summarize: List[dict] = []
        kept = list(window)
        kept_chars = len(memory_context or "") + sum(
            len(m.get("content", "")) for m in window
        )

        for m in reversed(history):  # 历史从最新开始往回累计
            c = len(m.get("content", ""))
            if kept_chars + c <= max_chars:
                kept.insert(0, m)
                kept_chars += c
            else:
                to_summarize.append(m)
        to_summarize.reverse()  # 旧 → 新

        # 窗口本身超上限时，从窗口最旧开始总结
        while kept_chars > max_chars and len(kept) > 1:
            oldest = kept.pop(0)
            to_summarize.insert(0, oldest)
            kept_chars -= len(oldest.get("content", ""))

        # 3. 组装结果：摘要(system) + 记忆(system) + 保留消息
        result: List[dict] = []
        if to_summarize:
            summary = await self._summarize_overflow(to_summarize)
            if summary:
                result.append({"role": "system", "content": f"[历史对话重点摘要] {summary}"})
            else:
                # 总结失败则降级：保留每条约首 30 字
                fallback = "\n".join(
                    f"[{m.get('role')}]: {str(m.get('content', ''))[:30]}"
                    for m in to_summarize
                )
                result.append({"role": "system", "content": f"[历史对话重点摘要] {fallback}"})

        if memory_context and memory_context.strip():
            result.append({
                "role": "system",
                "content": f"以下是从历史记忆中检索到的相关信息：\n\n{memory_context}",
            })
        result.extend(kept)

        logger.info(
            f"[Slicer] 窗口={len(window)} 保留={len(kept)} 总结={len(to_summarize)} "
            f"总字数={kept_chars}/{max_chars} 输出消息数={len(result)}"
        )
        return result

    async def _summarize_overflow(self, messages: List[dict]) -> str:
        """将超出上限的旧消息 LLM 总结为一条摘要（超长时分块并发生成）"""
        if not messages:
            return ""
        text = "\n".join(
            f"{m.get('role', '')}: {m.get('content', '')}" for m in messages
        )
        if len(text) <= self.chunk_chars:
            return await self._summarize_chunk(text)
        chunks = self._split_text_into_chunks(text)
        summaries = await asyncio.gather(
            *[self._summarize_chunk(c) for c in chunks],
            return_exceptions=True,
        )
        valid = [s for s in summaries if isinstance(s, str) and s]
        return "；".join(valid) if valid else ""

    def _split_text_into_chunks(self, text: str) -> List[str]:
        """将文本按 chunk_chars 切分（尽量在句子边界切）"""
        chunks = []
        start = 0
        n = len(text)
        while start < n:
            end = min(start + self.chunk_chars, n)
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

        # 摘要任务是独立单次调用，不能复用大模型默认的 orchestrator 人设（带工具/人格），
        # 否则会诱导模型按 agent 身份作答；用专用精简 system prompt。
        system_prompt = (
            "你是对话摘要助手，只做文本摘要归纳。\n"
            "规则：\n"
            "1. 不回复用户、不执行任何工具、不发起对话。\n"
            "2. 用中文概括用户的核心问题和助手给出的重要信息/结论。\n"
            "3. 总长度控制在 80 字以内，多个小话题可并列。\n"
            "4. 不包含「用户说」「助手说」等元描述，直接表达信息。\n"
            "5. 如果完全是闲聊，输出「闲聊」。"
        )

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
            client = self._get_client()
            async with _SUMMARIZE_SEM:
                summary = await client.generate(
                    prompt,
                    max_tokens=120,
                    temperature=0.3,
                    system_prompt=system_prompt,
                )
            summary = summary.strip()
            if summary.startswith("摘要："):
                summary = summary[3:].strip()
            return summary
        except Exception as e:
            logger.warning(f"历史摘要生成失败，降级处理: {e}")
            return chunk.replace("\n", " ")[:30] + "..."
