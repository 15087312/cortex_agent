"""
EventRetrieval — hybrid RAG retrieval (semantic + keyword + causal).
Ported from reference: removed owner_id filter.
"""
import math
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.memory.event_store import EventStore, MemoryEvent
from backend.memory.embedding import EmbeddingEngine
from backend.utils.logger import setup_logger

logger = setup_logger("event_retrieval")

TYPE_DECAY_LAMBDA = {
    "emotion": 0.01,
    "thought": 0.003,
    "fact": 0.0005,
    "strategy": 0.00005,
}

SCORE_WEIGHTS = {
    "semantic": 0.35,
    "importance": 0.20,
    "recency": 0.20,
    "utility": 0.15,
    "frequency": 0.10,
}

MIN_SEMANTIC_SIMILARITY = 0.20
SECONDS_PER_DAY = 86400.0


class EventRetrieval:
    """Event retriever with hybrid RAG."""

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

    async def retrieve(
        self,
        query: str,
        max_results: int = 10,
        threshold: float = 0.06,
        min_importance: float = 0.0,
        types: Optional[List[str]] = None,
    ) -> List[MemoryEvent]:
        vector_results = await self._vector_search(query, top_k=max_results * 3)
        query_keywords = self._extract_keywords(query)
        keyword_results = self._keyword_search(query_keywords)
        causal_results = self._causal_search(query)

        now = datetime.now(timezone.utc)
        scored = self._calculate_all_scores(vector_results, keyword_results, now)

        seen_ids = {ev.id for ev, _ in scored}
        for ev, causal_score in causal_results:
            if ev.id not in seen_ids:
                scored.append((ev, causal_score))
                seen_ids.add(ev.id)
            else:
                for i, (existing_ev, existing_score) in enumerate(scored):
                    if existing_ev.id == ev.id:
                        scored[i] = (existing_ev, max(existing_score, causal_score))
                        break

        if types:
            types_set = set(t.lower() for t in types)
            scored = [(ev, s) for ev, s in scored if ev.type in types_set]
        if min_importance > 0:
            scored = [(ev, s) for ev, s in scored if ev.importance >= min_importance]

        return self._rank_and_filter(scored, threshold, max_results)

    def _calculate_all_scores(
        self,
        vector_results: List[Tuple[MemoryEvent, float]],
        keyword_results: List[Tuple[MemoryEvent, float]],
        now: datetime,
    ) -> List[Tuple[MemoryEvent, float]]:
        seen: Dict[str, Dict[str, Any]] = {}

        for ev, sim in vector_results:
            seen[ev.id] = {"event": ev, "semantic": sim}

        for ev, _ in keyword_results:
            if ev.id not in seen:
                seen[ev.id] = {"event": ev, "semantic": 0.1}

        scored = []
        for sid, data in seen.items():
            ev = data["event"]
            semantic = data["semantic"]

            if semantic < MIN_SEMANTIC_SIMILARITY:
                continue

            days = self._days_since(ev.last_accessed or ev.time, now)
            lam = TYPE_DECAY_LAMBDA.get(ev.type, 0.0005)
            recency = math.exp(-lam * max(days, 0.01))

            ac = max(ev.access_count or 0, 0)
            utility = math.log(ac + 3) / math.log(13)

            mc = max(ev.mention_count or 1, 1)
            frequency = math.log(mc + 3) / math.log(13)

            has_lesson = 1.0 if ev.lesson else 0.0
            fact_len = len(ev.fact or "")
            content_bonus = 1.0 + 0.05 * (min(fact_len, 200) / 100) + 0.10 * has_lesson

            w = SCORE_WEIGHTS
            raw = (
                w["semantic"] * semantic +
                w["importance"] * ev.importance +
                w["recency"] * recency +
                w["utility"] * utility +
                w["frequency"] * frequency
            ) * content_bonus

            scored.append((ev, raw))

        return scored

    def _rank_and_filter(
        self,
        scored: List[Tuple[MemoryEvent, float]],
        threshold: float,
        max_results: int,
    ) -> List[MemoryEvent]:
        if not scored:
            return []

        scores = [s for _, s in scored]
        smin, smax = min(scores), max(scores)

        if smax - smin < 0.0001:
            normalized = [(ev, 0.5) for ev, _ in scored]
        else:
            normalized = [(ev, (s - smin) / (smax - smin)) for ev, s in scored]

        qualified = [(ev, ns) for ev, ns in normalized if ns >= threshold]
        qualified.sort(key=lambda x: x[1], reverse=True)

        store = self._get_store()
        for ev, _ in qualified[:max_results]:
            store.touch_event(ev.id)

        return [ev for ev, _ in qualified[:max_results]]

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
        try:
            from backend.memory.causal_graph import CausalGraph
            graph = CausalGraph.get_instance()
            store = self._get_store()

            anchors = graph.find_anchor_nodes(query, top_k=3)
            if not anchors:
                return []

            node_ids = set()
            for node, score in anchors:
                node_ids.add(node.id)
                neighbors = graph.get_neighbors(node.id, hops=1)
                for n, _, _ in neighbors:
                    node_ids.add(n.id)

            all_events = store.list_events(limit=500)
            results = []
            for ev in all_events:
                if ev.causal_node_ids and set(ev.causal_node_ids) & node_ids:
                    results.append((ev, 0.3))

            return results
        except Exception as e:
            logger.debug(f"Causal search failed: {e}")
            return []

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


_retrieval_instance: Optional[EventRetrieval] = None
_retrieval_lock = threading.Lock()


def get_event_retrieval() -> EventRetrieval:
    global _retrieval_instance
    if _retrieval_instance is None:
        with _retrieval_lock:
            if _retrieval_instance is None:
                _retrieval_instance = EventRetrieval()
    return _retrieval_instance
