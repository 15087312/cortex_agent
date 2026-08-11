"""
EventRetrieval — 事件检索（纯向量语义检索 + 因果图扩散）

召回通路（全部候选都必须过真实语义门槛，无硬编码过关分）:
  1. FAISS 向量语义搜索（唯一主通路）
  2. 因果图扩散出的候选事件，同样用真实余弦相似度过滤

评分公式（加权和 × 内容加成）:
  raw = 0.60*semantic + 0.15*importance + 0.10*recency + 0.08*utility + 0.07*frequency
  score = raw * content_bonus

因子说明:
  semantic   - 真实余弦相似度（内积，0~1），低于 MIN_SEMANTIC_SIMILARITY 直接过滤
  importance - 离散等级: critical=1.0, high=0.70, medium=0.40, low=0.15, trivial=0.03
  recency    - exp(-λ * days)，按 type 不同衰减速率
  utility    - log(access_count + 3) / log(13)，检索次数越多越高
  frequency  - log(mention_count + 3) / log(13)，话题被提及越多越高

排序与过滤:
  1. 绝对归一化（除以理论最大分，避免"矮子里拔将军"）
  2. 淘汰 normalized score < threshold 的
  3. 降序排列
  4. 截取前 max_results 条
"""
import math
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

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
        start_time: str = "",
        end_time: str = "",
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
            start_time: 可选，只返回该时间之后的事件（ISO "2026-07-01" 或完整 ISO）
            end_time: 可选，只返回该时间之前的事件（同上，含当天）

        流程:
        1. 向量语义搜索（唯一召回通路）
        2. 因果图扩散：候选事件同样过真实语义门槛
        3. 合并 + 打分 + 排序
        """
        # 0. 向量化查询（一次，向量搜索与因果扩散共用）
        query_embedding = self._get_embedder().embed(query)
        if query_embedding is None:
            return []

        # 1. 向量语义搜索（唯一召回通路）
        vector_results = await self._vector_search(query_embedding, top_k=max_results * 3)

        # 2. 因果图扩散：候选事件同样过真实语义门槛（不再硬编码 0.3 过关分）
        causal_events = self._causal_search(query)
        causal_results = self._compute_similarities(query_embedding, causal_events)

        # 3. 合并去重（同事件取较高语义分）
        merged: Dict[str, Tuple[MemoryEvent, float]] = {}
        for ev, semantic in list(vector_results) + causal_results:
            if ev.id not in merged or semantic > merged[ev.id][1]:
                merged[ev.id] = (ev, semantic)

        # 4. 逐条打分
        now = datetime.now(timezone.utc)
        scored = self._calculate_all_scores(list(merged.values()), now)

        # 5. type 过滤 + 重要性过滤
        if types:
            types_set = set(t.lower() for t in types)
            scored = [(ev, s) for ev, s in scored if ev.type in types_set]
        if min_importance > 0:
            scored = [(ev, s) for ev, s in scored if ev.importance >= min_importance]
        # 时间范围过滤
        if start_time or end_time:
            scored = [(ev, s) for ev, s in scored
                      if self._in_time_range(ev.time, start_time, end_time)]

        # owner_id 过滤：各模型只看自己的记忆
        # large 系列（large_primary / large::large_primary）作为总指挥可以看到所有记忆
        if owner_id and not owner_id.startswith("large"):
            scored = [(ev, s) for ev, s in scored if ev.owner_id == owner_id]

        # 6. 归一化 + 阈值 + 排序 + 截断
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

        embedder = self._get_embedder()
        all_scored: Dict[str, Tuple[MemoryEvent, float]] = {}
        now = datetime.now(timezone.utc)

        for topic, weight in norm.items():
            if not topic.strip():
                continue
            query_embedding = embedder.embed(topic)
            if query_embedding is None:
                continue
            topic_top_k = max(3, round(max_results * weight * 3))
            vector_results = await self._vector_search(query_embedding, top_k=topic_top_k)
            scored = self._calculate_all_scores(vector_results, now)

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
        candidates: List[Tuple[MemoryEvent, float]],
        now: datetime,
    ) -> List[Tuple[MemoryEvent, float]]:
        """逐条计算最终评分

        candidates: 已带真实语义相似度的候选 [(event, semantic)]。
        低于 MIN_SEMANTIC_SIMILARITY 的事件直接过滤——不再有硬编码过关分。
        """
        scored = []
        for ev, semantic in candidates:
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

    async def _vector_search(self, query_embedding: List[float], top_k: int = 15) -> List[Tuple[MemoryEvent, float]]:
        """向量语义搜索（唯一召回通路）"""
        store = self._get_store()
        results = store.search_by_vector(query_embedding, top_k=top_k)
        if not results:
            return []
        output = []
        for event_id, score in results:
            event = store.get_event(event_id)
            if event:
                output.append((event, score))
        return output

    def _compute_similarities(
        self, query_embedding: List[float], events: List[MemoryEvent]
    ) -> List[Tuple[MemoryEvent, float]]:
        """对候选事件计算与查询的真实余弦相似度（从 FAISS 取事件向量）

        与向量通路共用同一 0.30 语义门槛，避免因果关联事件被硬编码分带进结果。
        """
        if not events:
            return []
        store = self._get_store()
        results = []
        for ev in events:
            vec = store.get_embedding(ev.id)
            if vec is None:
                continue
            sim = sum(q * v for q, v in zip(query_embedding, vec))
            if sim >= MIN_SEMANTIC_SIMILARITY:
                results.append((ev, sim))
        return results

    def _causal_search(self, query: str) -> List[MemoryEvent]:
        """因果关系检索：通过因果图的边关系找候选事件（仅召回，仍需过语义门槛）"""
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

            # 通过因果节点 ID 找候选事件
            all_events = store.list_events(limit=500)
            candidates = []
            for ev in all_events:
                if ev.causal_node_ids and set(ev.causal_node_ids) & node_ids:
                    candidates.append(ev)

            return candidates
        except Exception as e:
            self.logger.debug(f"[因果检索] 失败: {e}")
            return []

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_dt(s: str) -> Optional[datetime]:
        """解析 ISO 时间串为 aware datetime；纯日期视为当天 00:00 UTC。"""
        if not s:
            return None
        try:
            s = s.strip()
            if len(s) == 10:  # YYYY-MM-DD
                return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
            t = datetime.fromisoformat(s)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return t
        except (ValueError, TypeError):
            return None

    @classmethod
    def _in_time_range(cls, iso_time: str, start: str, end: str) -> bool:
        """判断事件时间是否在 [start, end] 闭区间；空边界表示不限。"""
        t = cls._parse_dt(iso_time)
        if t is None:
            return True
        s = cls._parse_dt(start)
        if s and t < s:
            return False
        e = cls._parse_dt(end)
        if e:
            # 纯日期 end 视为含当天整天（次日 00:00 为界）
            if len(end.strip()) == 10:
                from datetime import timedelta
                e = e + timedelta(days=1)
            if t >= e:
                return False
        return True

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
