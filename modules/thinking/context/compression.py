"""
上下文压缩引擎

5 级压缩：
- NONE: 原样返回
- LIGHT: 去空行/注释
- MODERATE: LLM 摘要旧段落（保留头尾完整）
- HEAVY: LLM 结构化压缩
- AGGRESSIVE: LLM 提取关键词和结论

额外能力：语义摘要、冗余检测、增量更新
"""
import re
import time
import threading
from typing import List, Dict, Any, Optional
from utils.logger import setup_logger
from .types import (
    CompressionLevel, EventRecord, EventType
)

logger = setup_logger("compression_engine")


class CompressionEngine:
    """
    上下文压缩引擎 — 单例

    自动选择压缩级别并压缩内容到目标 token 数。
    """

    # 粗略 token 估算比例
    # 注意：实际比例取决于具体 tokenizer，以下为保守估计（偏低以避免超出窗口）
    # Claude/GPT tokenizer 中文通常 1-2 字符/token，英文约 4 字符/token
    CHARS_PER_TOKEN_CN = 2   # 保守估计：中文 1 token ≈ 2 字符（预留安全边界）
    CHARS_PER_TOKEN_EN = 4   # 英文 1 token ≈ 4 字符

    # 截断比例: 保留头部 70%，尾部 30%
    TRUNCATE_HEAD_RATIO = 0.7
    TRUNCATE_TAIL_RATIO = 0.3

    def estimate_tokens(self, text: str) -> int:
        """粗略估算 token 数"""
        if not text:
            return 0
        # 中文字符比例估计
        cn_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        en_chars = len(text) - cn_chars
        return cn_chars // self.CHARS_PER_TOKEN_CN + en_chars // self.CHARS_PER_TOKEN_EN

    def auto_level(self, content: str, max_tokens: int) -> CompressionLevel:
        """根据内容长度和目标 token 数自动选择压缩级别"""
        tokens = self.estimate_tokens(content)
        if tokens <= max_tokens:
            return CompressionLevel.NONE
        ratio = tokens / max_tokens
        if ratio <= 1.5:
            return CompressionLevel.LIGHT
        elif ratio <= 3:
            return CompressionLevel.MODERATE
        elif ratio <= 6:
            return CompressionLevel.HEAVY
        else:
            return CompressionLevel.AGGRESSIVE

    async def compress(
        self,
        content: str,
        max_tokens: int = 8000,
        level: CompressionLevel = None
    ) -> str:
        """
        压缩内容到目标 token 数

        Args:
            content: 原始内容
            max_tokens: 目标最大 token 数
            level: 指定压缩级别 (None 则自动选择)

        Returns:
            压缩后的内容
        """
        if not content:
            return ""

        if level is None:
            level = self.auto_level(content, max_tokens)

        if level == CompressionLevel.NONE:
            return self._truncate_to_tokens(content, max_tokens)

        elif level == CompressionLevel.LIGHT:
            result = self._light_compress(content)
            return self._truncate_to_tokens(result, max_tokens)

        elif level == CompressionLevel.MODERATE:
            result = await self._moderate_compress(content)
            return self._truncate_to_tokens(result, max_tokens)

        elif level == CompressionLevel.HEAVY:
            result = await self._heavy_compress(content)
            return self._truncate_to_tokens(result, max_tokens)

        elif level == CompressionLevel.AGGRESSIVE:
            return await self._aggressive_compress(content, max_tokens)

        return content

    def _truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        """按 token 数截断（考虑中英混合内容）"""
        if not text or max_tokens <= 0:
            return text

        # 计算中英文比例，动态调整截断阈值
        cn_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        en_chars = len(text) - cn_chars

        # 估算当前文本的 token 数
        estimated_tokens = cn_chars // self.CHARS_PER_TOKEN_CN + en_chars // self.CHARS_PER_TOKEN_EN
        if estimated_tokens <= max_tokens:
            return text

        # 根据中英文比例计算目标字符数
        if cn_chars > 0 and en_chars > 0:
            # 混合内容：加权平均
            total_chars = len(text)
            cn_ratio = cn_chars / total_chars
            avg_chars_per_token = (
                cn_ratio * self.CHARS_PER_TOKEN_CN +
                (1 - cn_ratio) * self.CHARS_PER_TOKEN_EN
            )
            chars_limit = int(max_tokens * avg_chars_per_token)
        elif cn_chars > 0:
            # 纯中文
            chars_limit = max_tokens * self.CHARS_PER_TOKEN_CN
        else:
            # 纯英文
            chars_limit = max_tokens * self.CHARS_PER_TOKEN_EN

        if len(text) <= chars_limit:
            return text

        # 保留头部和尾部
        head_size = int(chars_limit * self.TRUNCATE_HEAD_RATIO)
        tail_size = int(chars_limit * self.TRUNCATE_TAIL_RATIO)
        head = text[:head_size]
        tail = text[-tail_size:]
        return head + "\n\n... [内容已截断] ...\n\n" + tail

    # ========================================================================
    # 5 级压缩实现
    # ========================================================================

    def _light_compress(self, text: str) -> str:
        """轻量压缩：去空行、去连续空白"""
        # 合并连续空行
        text = re.sub(r'\n{3,}', '\n\n', text)
        # 去行首尾空白
        lines = [line.strip() for line in text.split('\n')]
        # 去空行
        lines = [l for l in lines if l]
        return '\n'.join(lines)

    async def _moderate_compress(self, text: str) -> str:
        """中等压缩：摘要化旧信息"""
        text = self._light_compress(text)

        paragraphs = text.split('\n\n')
        if len(paragraphs) <= 5:
            return text

        # 保留前 2 段和后 2 段完整，中间摘要
        head = '\n\n'.join(paragraphs[:2])
        tail = '\n\n'.join(paragraphs[-2:])
        middle_summary = await self._summarize_paragraphs(paragraphs[2:-2])
        return head + '\n\n' + middle_summary + '\n\n' + tail

    async def _heavy_compress(self, text: str) -> str:
        """重度压缩：LLM 结构化摘要，回退到规则提取"""
        # 尝试 LLM 结构化压缩
        target_tokens = max(300, self.estimate_tokens(text) // 6)
        llm_result = await self._llm_summarize(
            text,
            target_tokens=target_tokens,
            instruction=(
                "将以下内容压缩为结构化摘要。格式：\n"
                "【关键决策】列出重要决策和结论\n"
                "【工具结果】列出工具调用的关键输出\n"
                "【待办事项】列出未完成的任务\n"
                "【上下文】保留必要的背景信息"
            ),
        )
        if llm_result:
            return llm_result

        # 回退：规则提取
        sections = re.split(r'\n(?:#{1,3}|【|\[)', text)
        compressed = []
        for section in sections:
            section = section.strip()
            if not section:
                continue
            sentences = re.split(r'[。.!！?？]', section)
            key_sentences = [s.strip() for s in sentences[:2] if s.strip()]
            if key_sentences:
                compressed.append('。'.join(key_sentences) + '。')
        return '\n\n'.join(compressed)

    async def _aggressive_compress(self, text: str, max_tokens: int) -> str:
        """激进压缩：LLM 提取核心要点，回退到关键词提取"""
        # 尝试 LLM 极限压缩
        llm_result = await self._llm_summarize(
            text,
            target_tokens=max_tokens,
            instruction=(
                "将以下内容极限压缩，只保留最核心的信息。"
                "用 bullet points 列出关键事实和结论，每条不超过一句话。"
                "丢弃所有过程描述、重复内容和次要细节。"
            ),
        )
        if llm_result:
            return self._truncate_to_tokens(llm_result, max_tokens)

        # 回退：关键词提取
        words = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', text)
        word_freq = {}
        for w in words:
            word_freq[w] = word_freq.get(w, 0) + 1

        top_keywords = sorted(word_freq.items(), key=lambda x: -x[1])[:20]
        kw_str = ' | '.join([f"{k}({v})" for k, v in top_keywords])

        conclusion_lines = []
        for line in text.split('\n'):
            if any(marker in line for marker in
                   ['结论', '总结', '因此', '所以', '综上', '建议', 'conclusion', 'summary']):
                conclusion_lines.append(line.strip()[:200])

        result = f"[核心关键词] {kw_str}"
        if conclusion_lines:
            result += '\n\n[关键结论]\n' + '\n'.join(conclusion_lines[:3])

        return self._truncate_to_tokens(result, max_tokens)

    # ========================================================================
    # 语义摘要
    # ========================================================================

    def summarize_events(self, events: List[EventRecord], max_summary_tokens: int = 500) -> str:
        """
        将事件列表压缩为语义摘要

        策略：按类型分组，每组取代表性事件
        """
        if not events:
            return "无事件"

        # 按时间排序（最新在前）
        sorted_events = sorted(events, key=lambda e: e.timestamp, reverse=True)

        # 按类型分组
        groups: Dict[str, List[EventRecord]] = {}
        for e in sorted_events:
            et = e.event_type.value if isinstance(e.event_type, EventType) else str(e.event_type)
            groups.setdefault(et, []).append(e)

        parts = []
        for evt_type, evts in groups.items():
            count = len(evts)
            samples = evts[:3]  # 每组取最新 3 个
            sample_texts = []
            for s in samples:
                content_preview = str(s.content)[:80] if s.content else "(无内容)"
                sample_texts.append(f"  - [{s.source_role}] {content_preview}")
            parts.append(f"[{evt_type}] ({count} 条)\n" + '\n'.join(sample_texts))

        summary = '\n\n'.join(parts)
        return self._truncate_to_tokens(summary, max_summary_tokens)

    async def _summarize_paragraphs(self, paragraphs: List[str]) -> str:
        """用 LLM 摘要段落内容，失败时回退到规则提取"""
        if not paragraphs:
            return ""

        content = '\n\n'.join(paragraphs)
        target_tokens = max(200, self.estimate_tokens(content) // 4)

        # 尝试 LLM 摘要
        llm_result = await self._llm_summarize(
            content,
            target_tokens=target_tokens,
            instruction="压缩以下对话历史为简明摘要，保留关键决策、工具调用结果、重要结论。不要丢失实质性信息。",
        )
        if llm_result:
            return f"【历史摘要】\n{llm_result}"

        # 回退：规则提取
        summaries = []
        for para in paragraphs[:5]:
            para = para.strip()
            if not para:
                continue
            sentences = [s.strip() for s in re.split(r'[。.!！?？]', para) if s.strip()]
            if sentences:
                core = sentences[0]
                if len(sentences) > 1 and len(core) < 30:
                    core = "。".join([sentences[0], sentences[1]])
                summaries.append(core[:100])

        if summaries:
            return "【中间段落摘要】" + "；".join(summaries)
        else:
            total_chars = sum(len(p) for p in paragraphs)
            return f"[中间 {len(paragraphs)} 段已压缩，共 {total_chars} 字符]"

    async def _llm_summarize(self, content: str, target_tokens: int = 500, instruction: str = "") -> Optional[str]:
        """调用小模型进行摘要压缩，失败返回 None"""
        try:
            from infra.model.small_model_client import SmallModelClient
            from config.settings import settings

            client = SmallModelClient(
                model_name=settings.SMALL_MODEL_NAME,
                max_tokens=target_tokens,
                temperature=0.1,
                api_key=settings.SMALL_MODEL_API_KEY or settings.LARGE_MODEL_API_KEY,
                api_url=settings.SMALL_MODEL_API_URL or settings.LARGE_MODEL_API_URL,
            )

            prompt = (
                f"{instruction}\n\n"
                f"目标长度：约 {target_tokens} tokens\n"
                f"---\n{content}"
            )

            result = await client.generate(prompt, max_tokens=target_tokens)
            await client.close()

            if result and isinstance(result, str) and len(result.strip()) > 20:
                return result.strip()
            return None
        except Exception as e:
            logger.debug(f"[LLM摘要] 调用失败，回退到规则压缩: {e}")
            return None

    # ========================================================================
    # 冗余检测
    # ========================================================================

    def is_redundant(
        self,
        new_content: str,
        existing_contents: List[str],
        threshold: float = 0.85
    ) -> bool:
        """
        检测新内容是否与已有内容高度冗余

        使用 Jaccard 相似度（基于字符 n-gram）
        """
        if not new_content or not existing_contents:
            return False

        def ngrams(text: str, n: int = 50) -> set:
            if len(text) < n:
                return {text}
            return {text[i:i + n] for i in range(len(text) - n + 1)}

        new_ng = ngrams(new_content)
        if not new_ng:
            return False

        for existing in existing_contents:
            ex_ng = ngrams(existing)
            if not ex_ng:
                continue
            intersection = len(new_ng & ex_ng)
            union = len(new_ng | ex_ng)
            if union > 0 and intersection / union > threshold:
                return True

        return False

    def detect_incremental_update(self, old_content: str, new_content: str) -> Optional[str]:
        """
        检测增量更新，只返回变更部分

        Returns:
            变更摘要 或 None（无显著变更）
        """
        if old_content == new_content:
            return None

        if not old_content:
            return new_content[:200] + "..."

        # 简单 diff：提取新增行
        old_lines = set(old_content.split('\n'))
        new_lines = set(new_content.split('\n'))
        added = new_lines - old_lines

        if not added:
            return None

        return "新增内容:\n" + '\n'.join(list(added)[:10])


# 模块级工厂函数
import threading as _threading

_instance = None
_init_lock = _threading.Lock()


def get_compression_engine() -> CompressionEngine:
    global _instance
    if _instance is None:
        with _init_lock:
            if _instance is None:
                _instance = CompressionEngine()
    return _instance
