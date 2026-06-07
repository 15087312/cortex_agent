"""
记忆检索层 — 只负责从 Memory Layer 取候选上下文

职责边界：
- 负责热/温/冷记忆的分层检索、时间窗口、初筛和裁剪
- 不负责注意力排序/资源分配
- 不负责 prompt 构建

Memory Layer: 存储、索引、检索
Retrieval Layer: 返回候选上下文
Attention Layer: 对候选上下文排序/选择
Context Manager: 组装 WorkingContext / prompt
"""
import time
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from utils.logger import setup_logger


@dataclass
class RetrievalResult:
    """检索层输出 — 候选上下文，不代表最终进入工作上下文"""
    recent_memory: List[Dict[str, Any]] = field(default_factory=list)
    warm_memory: List[Dict[str, Any]] = field(default_factory=list)
    long_term_matches: List[Dict[str, Any]] = field(default_factory=list)
    total_candidate_tokens: int = 0
    stats: Dict[str, Any] = field(default_factory=dict)


class MemoryRetriever:
    """分层记忆检索器

    三层：
    1. 热记忆 recent_memory：最近 30 分钟，原始事件，轻裁剪
    2. 温记忆 warm_memory：30 分钟 ~ 7 天，任务相关候选
    3. 冷记忆 long_term_matches：长期知识/摘要/事件，语义/关键词检索候选
    """

    def __init__(
        self,
        memory_manager=None,
        recent_window_minutes: int = 30,
        warm_window_days: int = 7,
        max_candidate_tokens: int = 12000,
        scopes: Optional[List[str]] = None,
        owner: Optional[str] = None,
    ):
        self.memory = memory_manager
        self.recent_window_minutes = recent_window_minutes
        self.warm_window_days = warm_window_days
        self.max_candidate_tokens = max_candidate_tokens
        self.scopes = scopes or ["shared", "global"]
        self.owner = owner
        self.logger = setup_logger("memory_retriever")

    def set_memory_manager(self, memory_manager) -> None:
        self.memory = memory_manager

    async def retrieve(self, query: str) -> RetrievalResult:
        """返回候选记忆，不做最终 attention 选择"""
        start = time.time()
        if not self.memory:
            return RetrievalResult(stats={"error": "memory_manager_missing"})

        recent = await self._retrieve_recent()
        warm = await self._retrieve_warm(query, recent)
        long_term = await self._retrieve_long_term(query)
        recent, warm, long_term = self._trim_candidates(recent, warm, long_term)

        total_tokens = (
            self._estimate_tokens(recent)
            + self._estimate_tokens(warm)
            + self._estimate_tokens(long_term)
        )
        return RetrievalResult(
            recent_memory=recent,
            warm_memory=warm,
            long_term_matches=long_term,
            total_candidate_tokens=total_tokens,
            stats={
                "recent_count": len(recent),
                "warm_count": len(warm),
                "long_term_count": len(long_term),
                "duration_ms": (time.time() - start) * 1000,
            },
        )

    async def _retrieve_recent(self) -> List[Dict[str, Any]]:
        try:
            context = self.memory.get_context(limit=100)
            cutoff = datetime.now() - timedelta(minutes=self.recent_window_minutes)
            recent = []
            for item in context:
                if not self._is_visible_memory(item):
                    continue
                ts = self._parse_timestamp(item.get("timestamp"))
                if ts and ts >= cutoff:
                    recent.append({
                        "content": item.get("text", ""),
                        "role": item.get("role", "unknown"),
                        "timestamp": item.get("timestamp"),
                        "metadata": item.get("metadata", {}),
                        "source": "hot_memory",
                    })
            return recent
        except Exception as e:
            self.logger.debug("热记忆检索失败 (非致命): %s", e)
            return []

    async def _retrieve_warm(
        self, query: str, recent_memories: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        try:
            context = self.memory.get_context(limit=500)
            now = datetime.now()
            recent_cutoff = now - timedelta(minutes=self.recent_window_minutes)
            warm_cutoff = now - timedelta(days=self.warm_window_days)
            keywords = set(self._extract_keywords(query))
            warm = []
            for item in context:
                if not self._is_visible_memory(item):
                    continue
                ts = self._parse_timestamp(item.get("timestamp"))
                if not ts or not (warm_cutoff <= ts < recent_cutoff):
                    continue
                content = item.get("text", "")
                if not self._matches_query(content, keywords):
                    continue
                warm.append({
                    "content": content,
                    "role": item.get("role", "unknown"),
                    "timestamp": item.get("timestamp"),
                    "metadata": item.get("metadata", {}),
                    "source": "warm_memory",
                })
            return warm
        except Exception as e:
            self.logger.debug("温记忆检索失败 (非致命): %s", e)
            return []

    async def _retrieve_long_term(self, query: str) -> List[Dict[str, Any]]:
        try:
            memory_types = ["dialog", "thought", "summary", "event"]
            all_memories = []
            for mem_type in memory_types:
                try:
                    results = self.memory.search_memories_by_category(
                        query=query,
                        category=mem_type,
                        time_range="30d",
                        limit=20,
                    )
                    for item in results:
                        if not self._is_visible_memory(item):
                            continue
                        text = self._extract_result_text(item)
                        if not text:
                            continue
                        all_memories.append({
                            "content": text,
                            "type": mem_type,
                            "timestamp": item.get("timestamp", 0),
                            "metadata": item.get("metadata", {}),
                            "source": "long_term",
                            "memory_id": item.get("id", ""),
                        })
                except Exception as e:
                    self.logger.debug("冷记忆类别 %s 检索失败: %s", mem_type, e)

            unique = []
            seen = set()
            for mem in all_memories:
                content_hash = hash(mem.get("content", ""))
                if content_hash in seen:
                    continue
                seen.add(content_hash)
                unique.append(mem)
            return unique
        except Exception as e:
            self.logger.debug("冷记忆检索失败 (非致命): %s", e)
            return []

    def _trim_candidates(self, recent, warm, long_term):
        recent_tokens = self._estimate_tokens(recent)
        if recent_tokens >= self.max_candidate_tokens:
            return recent, [], []

        remaining = self.max_candidate_tokens - recent_tokens
        selected_warm = []
        warm_tokens = 0
        for mem in warm:
            token_count = self._estimate_tokens([mem])
            if warm_tokens + token_count > remaining * 0.6:
                break
            selected_warm.append(mem)
            warm_tokens += token_count

        remaining -= warm_tokens
        selected_long = []
        long_tokens = 0
        for mem in long_term:
            token_count = self._estimate_tokens([mem])
            if long_tokens + token_count > remaining:
                break
            selected_long.append(mem)
            long_tokens += token_count

        return recent, selected_warm, selected_long

    def _matches_query(self, content: str, keywords: set) -> bool:
        if not keywords:
            return True
        lowered = str(content).lower()
        return any(str(k).lower() in lowered for k in keywords)

    def _is_visible_memory(self, memory: Dict[str, Any]) -> bool:
        metadata = memory.get("metadata") or memory.get("extra_data") or {}
        scope = metadata.get("scope", "shared")
        if scope not in self.scopes:
            return False

        if scope != "private":
            return True

        owner = metadata.get("owner") or memory.get("owner")
        visible_to = metadata.get("visible_to") or []
        if isinstance(visible_to, str):
            visible_to = [visible_to]

        return bool(self.owner and (owner == self.owner or self.owner in visible_to))

    def _extract_keywords(self, text: str) -> List[str]:
        if not text:
            return []
        try:
            import jieba.analyse
            kws = jieba.analyse.extract_tags(text, topK=10, withWeight=False)
            if kws:
                return kws
        except Exception as e:
            self.logger.debug("jieba 分词不可用，回退到正则分词: %s", e)
        cleaned = re.sub(r'[^\w\s一-鿿]', ' ', text)
        words = re.split(r'\s+', cleaned)
        stop_words = {'的', '了', '是', '在', '我', '有', '和', '就', '不', '你'}
        result = []
        for word in words:
            word = word.strip()
            if len(word) > 1 and word not in stop_words and word not in result:
                result.append(word)
        return result[:10]

    def _extract_result_text(self, item: Dict[str, Any]) -> str:
        raw = item.get("content", {})
        if isinstance(raw, dict):
            inner = raw.get("content", {})
            if isinstance(inner, dict):
                return str(inner.get("text", inner.get("content", "")))
            return str(inner or raw.get("text", ""))
        return str(raw or "")

    def _parse_timestamp(self, ts: Any) -> Optional[datetime]:
        if ts is None:
            return None
        if isinstance(ts, datetime):
            return ts
        if isinstance(ts, (int, float)):
            try:
                return datetime.fromtimestamp(ts)
            except Exception as e:
                self.logger.debug("时间戳转换失败: %s", e)
                return None
        if isinstance(ts, str):
            try:
                return datetime.fromisoformat(ts.replace('Z', '+00:00'))
            except Exception as e:
                self.logger.debug("ISO 时间解析失败，尝试其他格式: %s", e)
            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"]:
                try:
                    return datetime.strptime(ts, fmt)
                except Exception as e:
                    self.logger.debug("时间格式 '%s' 解析失败: %s", fmt, e)
                    continue
        return None

    def _estimate_tokens(self, memories: List[Dict[str, Any]]) -> int:
        chars = 0
        ascii_chars = 0
        for mem in memories:
            content = str(mem.get("content") or mem.get("text") or "")
            for char in content:
                if ord(char) > 127:
                    chars += 1
                else:
                    ascii_chars += 1
        return int(chars / 3.0 + ascii_chars / 4.0)
