"""桌宠独立记忆检索 — 独立于全局 event_retrieval

桌宠记忆存于 data/pet.db 的 pet_memory 表（见 store.py）。检索策略：

1. 优先向量：复用全局 EmbeddingEngine 做语义向量（embedding 是基础设施，共享合理），
   在桌宠自身记忆池（量小）内做余弦相似度排序；
2. 向量不可用（模型未加载等）时降级为关键词命中 + 时间倒序。

语义检索只发生在桌宠自己的记忆条目上，不触碰全局事件记忆库。
"""
import math
import threading
from typing import Any, Dict, List, Optional

from modules.desktop_pet.store import PetStore
from utils.logger import setup_logger

logger = setup_logger("pet_memory")

MIN_SIM = 0.35


class PetMemory:
    """桌宠独立记忆检索（单例，线程安全）"""

    _instance: Optional["PetMemory"] = None
    _lock = threading.Lock()

    def __init__(self, store: Optional[PetStore] = None):
        self._store = store or PetStore.get_instance()

    @classmethod
    def get_instance(cls) -> "PetMemory":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 检索 ──

    async def retrieve(self, query: str = "", max_results: int = 3) -> List[Dict[str, Any]]:
        """按语义相似度返回桌宠记忆（默认给最近记忆）"""
        try:
            pool = self._store.search_memory(limit=200)
        except Exception:
            return []
        if not pool:
            return []
        query = (query or "").strip()

        # 向量检索
        try:
            from modules.memory.embedding import EmbeddingEngine
            engine = EmbeddingEngine.get_instance()
            qv = engine.embed(query) if query else None
            if qv is not None:
                rows = self._rank_by_vector(pool, qv)
                if rows:
                    return rows[: max(1, int(max_results))]
        except Exception as e:
            logger.debug(f"[PetMemory] 向量检索不可用，降级关键词: {e}")

        # 降级：关键词 + 时间倒序
        if query:
            keys = [k for k in query.lower().split() if len(k) > 1]
            scored = []
            for m in pool:
                text = str(m.get("text", "")).lower()
                s = sum(1 for k in keys if k in text)
                if s:
                    scored.append((s, m))
            scored.sort(key=lambda x: (x[0], x[1].get("created_at", 0)), reverse=True)
            return [m for _, m in scored[: max(1, int(max_results))]]
        return pool[: max(1, int(max_results))]

    # ── 向量排序 ──

    def _rank_by_vector(self, pool: List[Dict[str, Any]], qv: List[float]) -> List[Dict[str, Any]]:
        scored = []
        for m in pool:
            text = str(m.get("text", ""))
            if not text:
                continue
            try:
                from modules.memory.embedding import EmbeddingEngine
                mv = EmbeddingEngine.get_instance().embed(text)
            except Exception:
                mv = None
            if not mv:
                continue
            sim = self._cosine(qv, mv)
            if sim >= MIN_SIM:
                scored.append((sim, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored]

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)
