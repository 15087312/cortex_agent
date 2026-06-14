"""
EventRetrieval — 事件检索（RAG 混合检索）

最终排序公式：

  score = semantic_similarity
          * type_decay(type, last_accessed)    # 类型相关的遗忘曲线
          * reinforcement(access_count)         # 成功召回的强化
          * importance                          # 原始重要性

type_decay = exp(-λ * days)  其中 λ 由 type 决定：
  emotion:  0.01    → 30 天衰减到 0.74，一年消失
  thought:  0.003   → 180 天衰减到 0.58
  fact:     0.0005  → 365 天衰减到 0.83
  strategy: 0.00005 → 10 年仅衰减到 0.83

reinforcement = log(access_count + 1)  被召回越多权重越高

永不修改存储，只在检索时动态算分。
"""
import math
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from modules.memory.event_store import EventStore, MemoryEvent
from modules.memory.embedding import EmbeddingEngine
from utils.logger import setup_logger

logger = setup_logger("event_retrieval")

# ── 类型衰减系数 ──────────────────────────────────────────────
# λ 越大衰减越快
TYPE_DECAY_LAMBDA = {
    "emotion":  0.01,     # 情绪：最快衰减
    "thought":  0.003,    # 思考：中速
    "fact":     0.0005,   # 事实：慢速
    "strategy": 0.00005,  # 策略：几乎不衰减
}

SECONDS_PER_DAY = 86400.0


class EventRetrieval:
    """事件检索器"""

    _instance: "EventRetrieval" = None
    _lock = threading.Lock()

    def __init__(self):
        self._store: Optional[EventStore] = None
        self._embedder: Optional[EmbeddingEngine] = None

    @classmethod
    def get_instance(cls) -> "EventRetrieval":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # 主检索方法
    # ------------------------------------------------------------------

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        min_importance: float = 0.0,
    ) -> List[MemoryEvent]:
        """根据查询检索最相关的记忆事件

        1. 向量语义搜索得到候选集
        2. 合并关键词命中候选
        3. 用「遗忘曲线 × 强化 × 重要性」算最终分
        4. 返回时 touch 每条事件（更新 last_accessed + access_count）
        """
        # 1. 向量语义搜索
        vector_results = await self._vector_search(query, top_k=top_k * 3)

        # 2. 关键词搜索（补充候选）
        query_keywords = self._extract_keywords(query)
        keyword_results = self._keyword_search(query_keywords)

        # 3. 用新公式评分
        now = datetime.now(timezone.utc)
        scored = self._score_events(vector_results, keyword_results, now)

        # 4. 过滤
        if min_importance > 0:
            scored = [(ev, s) for ev, s in scored if ev.importance >= min_importance]

        top_events = [ev for ev, _ in scored[:top_k]]

        # 5. touch 每条结果（更新 last_accessed + access_count）
        store = self._get_store()
        for ev in top_events:
            store.touch_event(ev.id)

        return top_events

    # ------------------------------------------------------------------
    # 评分引擎（核心）
    # ------------------------------------------------------------------

    def _score_events(
        self,
        vector_results: List[Tuple[MemoryEvent, float]],
        keyword_results: List[Tuple[MemoryEvent, float]],
        now: datetime,
    ) -> List[Tuple[MemoryEvent, float]]:
        """用遗忘曲线公式重新评分"""
        scores: Dict[str, Dict[str, Any]] = {}

        # 向量分
        for ev, score in vector_results:
            scores[ev.id] = {"event": ev, "vector": score}

        # 关键词候选补充（没有向量分的给基础分）
        for ev, _ in keyword_results:
            if ev.id not in scores:
                scores[ev.id] = {"event": ev, "vector": 0.1}

        scored_list = []
        for sid, data in scores.items():
            ev = data["event"]
            semantic = data["vector"]

            # 时间衰减：用 last_accessed 而非 created_at
            days = self._days_since(ev.last_accessed or ev.time, now)
            lam = TYPE_DECAY_LAMBDA.get(ev.type, 0.0005)
            time_decay = math.exp(-lam * days)

            # 增强因子
            reinforcement = math.log(ev.access_count + 1) + 1.0  # +1 保证最小值 1

            # 最终分
            final_score = semantic * time_decay * reinforcement * ev.importance

            scored_list.append((ev, final_score))

        scored_list.sort(key=lambda x: x[1], reverse=True)
        return scored_list

    # ------------------------------------------------------------------
    # 各维度检索
    # ------------------------------------------------------------------

    async def _vector_search(self, query: str, top_k: int = 15) -> List[Tuple[MemoryEvent, float]]:
        """FAISS 向量语义搜索"""
        embedder = self._get_embedder()
        store = self._get_store()

        query_embedding = embedder.embed(query)
        if query_embedding is None:
            return []

        results = store.search_by_vector(query_embedding, top_k=top_k)
        if not results:
            return []

        output = []
        for event_id, score in results:
            event = store.get_event(event_id)
            if event:
                output.append((event, score))
        return output

    def _keyword_search(self, keywords: List[str]) -> List[Tuple[MemoryEvent, float]]:
        """关键词精确匹配"""
        if not keywords:
            return []
        store = self._get_store()
        events = store.search_by_keywords(keywords, limit=20)
        return [(ev, 0.8) for ev in events]

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    @staticmethod
    def _days_since(iso_time: str, now: datetime) -> float:
        """计算 ISO 时间戳距今的天数"""
        try:
            if not iso_time:
                return 0.0
            # 兼容带时区和不带时区的 ISO 格式
            if "+" in iso_time or iso_time.endswith("Z"):
                t = datetime.fromisoformat(iso_time)
            else:
                t = datetime.fromisoformat(iso_time).replace(tzinfo=timezone.utc)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            delta = now - t
            return max(0.0, delta.total_seconds() / SECONDS_PER_DAY)
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        """简单分词提取关键词"""
        if not text:
            return []
        keywords = set()
        eng_words = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]{1,}', text)
        keywords.update(w.lower() for w in eng_words if len(w) >= 2)
        chn_parts = re.findall(r'[\u4e00-\u9fff]{2,}', text)
        keywords.update(chn_parts)
        return list(keywords)

    def _get_store(self) -> EventStore:
        if self._store is None:
            self._store = EventStore.get_instance()
        return self._store

    def _get_embedder(self) -> EmbeddingEngine:
        if self._embedder is None:
            self._embedder = EmbeddingEngine.get_instance()
        return self._embedder


# 模块级快捷函数
_retrieval_instance: Optional[EventRetrieval] = None
_retrieval_lock = threading.Lock()


def get_event_retrieval() -> EventRetrieval:
    global _retrieval_instance
    if _retrieval_instance is None:
        with _retrieval_lock:
            if _retrieval_instance is None:
                _retrieval_instance = EventRetrieval()
    return _retrieval_instance
