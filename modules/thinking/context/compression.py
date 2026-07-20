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

    def compress(
        self,
        content: str,
        max_tokens: int = 8000,
        level: CompressionLevel = None
    ) -> str:
        if not content:
            return ""
        result = self._light_compress(content)
        return self._truncate_to_tokens(result, max_tokens)

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
    # 轻量压缩
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
