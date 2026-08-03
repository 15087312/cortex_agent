"""
EventRetrieval — 事件检索（RAG 混合检索）

评分公式（加权和 × 内容加成）:
  raw = 0.35*semantic + 0.20*importance + 0.20*recency + 0.15*utility + 0.10*frequency
  score = raw * content_bonus

因子说明:
  semantic   - FAISS 向量相似度（内积，0~1）
  importance - 离散等级: critical=1.0, high=0.70, medium=0.40, low=0.15, trivial=0.03
  recency    - exp(-λ * days)，按 type 不同衰减速率
  utility    - log(access_count + 3) / log(13)，检索次数越多越高
  frequency  - log(mention_count + 3) / log(13)，话题被提及越多越高

排序与过滤:
  1. 批内 min-max 归一化到 0~1
  2. 淘汰 score < threshold 的
  3. 降序排列
  4. 截取前 max_results 条
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
TYPE_DECAY_LAMBDA = {
    "emotion":  0.01,     # 情绪：最快衰减
    "thought":  0.003,    # 思考：中速
    "fact":     0.0005,   # 事实：慢速
    "strategy": 0.00005,  # 策略：几乎不衰减
}

# ── 评分权重 ──────────────────────────────────────────────
# 语义相关性必须主导：importance/recency/utility/frequency 只是辅助，
# 权重过高会让"重要/最近/高频"的无关事件碾压语义相关事件（用户反馈检索无关）。
SCORE_WEIGHTS = {
    "semantic": 0.60,     # 语义相关（主导）
    "importance": 0.15,   # 重要性（LLM 离散标注）
    "recency": 0.10,      # 最近被访问
    "utility": 0.08,      # 被检索次数
    "frequency": 0.07,    # 话题被提及次数
}

# 最小原始语义相似度：低于此值的事件视为不相关，直接过滤
# 余弦相似度 < 0.30 表示向量基本无关，防止"热门但无关"事件挤进结果
MIN_SEMANTIC_SIMILARITY = 0.30

SECONDS_PER_DAY = 86400.0


class EventRetrieval:
    """事件检索器"""

    _instance: "EventRetrieval" = None
    _lock = threading.Lock()

    def __init__(self):
        self._store: Optional[EventStore] = None
        self._embedder: Optional[EmbeddingEngine] = None
        self.logger = logger

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
        max_results: int = 10,
        threshold: float = 0.06,
        min_importance: float = 0.0,
        types: Optional[List[str]] = None,
        owner_id: Optional[str] = None,
    ) -> List[MemoryEvent]:
        """根据查询检索最相关的记忆事件

        Args:
            query: 查询文本
            max_results: 最多返回条数
            threshold: 归一化分数阈值，低于此值的淘汰
            min_importance: 最低重要性过滤
            types: 可选，只返回指定类型
            owner_id: 可选，只返回指定模型所有者的记忆
                      "large_primary" / "large_coder" / "supervisor_xx" / "expert_xx"
                      "large" 开头的表示 Large 系列总指挥（可看全部记忆）

        流程:
        1. 向量语义搜索
        2. 关键词搜索
        3. 因果图扩散（通过边关系找关联事件）
        4. 合并 + 打分 + 排序
        """
        # 1. 向量语义搜索
        vector_results = await self._vector_search(query, top_k=max_results * 3)

        # 2. 关键词搜索（补充候选）
        query_keywords = self._extract_keywords(query)
        keyword_results = self._keyword_search(query_keywords)

        # 3. 因果图扩散：通过边关系找关联事件
        causal_results = self._causal_search(query)

        # 4. 合并去重 + 打分
        now = datetime.now(timezone.utc)
        scored = self._calculate_all_scores(vector_results, keyword_results, now)

        # 将因果检索结果注入评分（已有则取 max，没有则新增）
        seen_ids = {ev.id for ev, _ in scored}
        for ev, causal_score in causal_results:
            if ev.id not in seen_ids:
                scored.append((ev, causal_score))
                seen_ids.add(ev.id)
            else:
                # 已有：取向量分和因果分的较高值
                for i, (existing_ev, existing_score) in enumerate(scored):
                    if existing_ev.id == ev.id:
                        scored[i] = (existing_ev, max(existing_score, causal_score))
                        break

        # 4. type 过滤 + 重要性过滤
        if types:
            types_set = set(t.lower() for t in types)
            scored = [(ev, s) for ev, s in scored if ev.type in types_set]
        if min_importance > 0:
            scored = [(ev, s) for ev, s in scored if ev.importance >= min_importance]

        # owner_id 过滤：各模型只看自己的记忆
        # large 系列（large_primary / large::large_primary）作为总指挥可以看到所有记忆
        if owner_id and not owner_id.startswith("large"):
            scored = [(ev, s) for ev, s in scored if ev.owner_id == owner_id]

        # 5. 归一化 + 阈值 + 排序 + 截断
        top_events = self._rank_and_filter(scored, threshold, max_results)

        return top_events

    async def retrieve_mixed(
        self,
        mix: Dict[str, float],
        max_results: int = 10,
        threshold: float = 0.06,
        types: Optional[List[str]] = None,
        owner_id: Optional[str] = None,
    ) -> List[MemoryEvent]:
        """按主题配比多路检索合并"""
        if not mix:
            return []

        total = sum(mix.values())
        if total <= 0:
            return []
        norm = {k: v / total for k, v in mix.items()}

        all_scored: Dict[str, Tuple[MemoryEvent, float]] = {}
        now = datetime.now(timezone.utc)

        for topic, weight in norm.items():
            if not topic.strip():
                continue
            topic_top_k = max(3, round(max_results * weight * 3))
            vector_results = await self._vector_search(topic, top_k=topic_top_k)
            kw_results = self._keyword_search(self._extract_keywords(topic))
            scored = self._calculate_all_scores(vector_results, kw_results, now)

            if types:
                ts = set(t.lower() for t in types)
                scored = [(ev, s) for ev, s in scored if ev.type in ts]

            for ev, s in scored:
                adjusted = s * weight
                if ev.id not in all_scored or adjusted > all_scored[ev.id][1]:
                    all_scored[ev.id] = (ev, adjusted)

        scored_list = list(all_scored.values())

        # owner_id 过滤（暂不支持共享记忆）
        if owner_id:
            scored_list = [(ev, s) for ev, s in scored_list if ev.owner_id == owner_id]

        return self._rank_and_filter(scored_list, threshold, max_results)

    # ------------------------------------------------------------------
    # 评分引擎
    # ------------------------------------------------------------------

    def _calculate_all_scores(
        self,
        vector_results: List[Tuple[MemoryEvent, float]],
        keyword_results: List[Tuple[MemoryEvent, float]],
        now: datetime,
    ) -> List[Tuple[MemoryEvent, float]]:
        """逐条计算最终评分"""
        seen: Dict[str, Dict[str, Any]] = {}

        for ev, sim in vector_results:
            seen[ev.id] = {"event": ev, "semantic": sim}

        for ev, _ in keyword_results:
            if ev.id not in seen:
                # 关键词匹配的基础语义分：须 >= MIN_SEMANTIC_SIMILARITY 才能过门槛，
                # 但低于向量命中，避免"纯关键词巧合"的无关事件排到语义相关前面
                seen[ev.id] = {"event": ev, "semantic": 0.35}

        scored = []
        for sid, data in seen.items():
            ev = data["event"]
            semantic = data["semantic"]

            # 原始语义相似度过滤：去除无关事件
            if semantic < MIN_SEMANTIC_SIMILARITY:
                continue

            days = self._days_since(ev.last_accessed or ev.time, now)
            λ = TYPE_DECAY_LAMBDA.get(ev.type, 0.0005)
            recency = math.exp(-λ * max(days, 0.01))

            ac = max(ev.access_count or 0, 0)
            utility = math.log(ac + 3) / math.log(13)

            mc = max(ev.mention_count or 1, 1)
            frequency = math.log(mc + 3) / math.log(13)

            has_lesson = 1.0 if ev.lesson else 0.0
            fact_len = len(ev.fact or "")
            content_bonus = 1.0 + 0.05 * (min(fact_len, 200) / 100) + 0.10 * has_lesson

            w = SCORE_WEIGHTS
            raw = (
                w["semantic"]   * semantic +
                w["importance"] * ev.importance +
                w["recency"]    * recency +
                w["utility"]    * utility +
                w["frequency"]  * frequency
            ) * content_bonus

            scored.append((ev, raw))

        return scored

    def _rank_and_filter(
        self,
        scored: List[Tuple[MemoryEvent, float]],
        threshold: float,
        max_results: int,
    ) -> List[MemoryEvent]:
        """绝对归一化 → 阈值 → 排序 → 截断"""
        if not scored:
            return []

        # 绝对归一化：除以理论最大原始分，而不是 min-max 相对归一化。
        # min-max 会让"所有候选都不相关"时最高分也变成 1.0 通过阈值（矮子里拔将军）。
        # 理论最大 raw ≈ 全因子取 1.0 × 内容加成上限 1.15。
        raw_max = sum(SCORE_WEIGHTS.values()) * 1.15
        normalized = [(ev, s / raw_max) for ev, s in scored]

        qualified = [(ev, ns) for ev, ns in normalized if ns >= threshold]
        qualified.sort(key=lambda x: x[1], reverse=True)

        store = self._get_store()
        for ev, _ in qualified[:max_results]:
            store.touch_event(ev.id)

        return [ev for ev, _ in qualified[:max_results]]

    # ------------------------------------------------------------------
    # 各维度检索
    # ------------------------------------------------------------------

    async def _vector_search(self, query: str, top_k: int = 15) -> List[Tuple[MemoryEvent, float]]:
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
        if not keywords:
            return []
        store = self._get_store()
        events = store.search_by_keywords(keywords, limit=20)
        return [(ev, 0.0) for ev in events]

    def _causal_search(self, query: str) -> List[Tuple[MemoryEvent, float]]:
        """因果关系检索：通过因果图的边关系找关联事件"""
        try:
            from modules.memory.causal_graph import CausalGraph
            graph = CausalGraph.get_instance()
            store = self._get_store()

            # 找锚点节点
            anchors = graph.find_anchor_nodes(query, top_k=3)
            if not anchors:
                return []

            # 收集锚点及其邻居的 ID
            node_ids = set()
            for node, score in anchors:
                node_ids.add(node.id)
                neighbors = graph.get_neighbors(node.id, hops=1)
                for n, _, _ in neighbors:
                    node_ids.add(n.id)

            # 通过因果节点 ID 找关联事件
            all_events = store.list_events(limit=500)
            results = []
            for ev in all_events:
                if ev.causal_node_ids and set(ev.causal_node_ids) & node_ids:
                    # 因果关联分数：0.3（低于向量但高于关键词）
                    results.append((ev, 0.3))

            return results
        except Exception as e:
            self.logger.debug(f"[因果检索] 失败: {e}")
            return []

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    @staticmethod
    def _days_since(iso_time: str, now: datetime) -> float:
        try:
            if not iso_time:
                return 0.0
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
        if not text:
            return []
        keywords = set()
        eng_words = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]{1,}', text)
        keywords.update(w.lower() for w in eng_words if len(w) >= 2)
        chn_parts = re.findall(r'[\u4e00-\u9fff]{2,}', text)
        keywords.update(chn_parts)
        # 对中文词取关键部分（4字及以上拆 bigram，与 Conscience 保持一致）
        for kw in list(chn_parts):
            if len(kw) >= 4:
                for i in range(len(kw) - 1):
                    bigram = kw[i:i+2]
                    if bigram not in keywords:
                        keywords.add(bigram)
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
