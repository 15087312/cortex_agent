"""知识检索 — 向量路 + 关键词路，RRF 融合后做上下文整理

为什么用 RRF 而不是加权和：余弦相似度 ∈ [-1, 1]，BM25/词频无界，两者尺度不可比，
直接加权会让某一路碾压另一路。Reciprocal Rank Fusion 只看名次，天然免疫尺度差异。

后处理顺序（顺序有意义，不要调换）：
    RRF 排序 → 相邻块合并（补全被切断的上下文）→ 单文档限流 → token 预算裁剪
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from config.settings import settings
from utils.logger import setup_logger
from modules.knowledge.chunker import count_tokens
from modules.knowledge.models import KnowledgeHit
from modules.knowledge.store import KnowledgeStore, is_enabled

logger = setup_logger("knowledge_retrieval")

RRF_K = 60
_MAX_OVERLAP_SCAN = 400


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> Dict[str, float]:
    """多路名次融合：score = Σ 1/(k + rank)，rank 从 1 开始。"""
    scores: Dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


def _merge_texts(left: str, right: str) -> str:
    """拼接相邻块，去掉块间重叠（按字符最长后缀=前缀匹配）。"""
    limit = min(len(left), len(right), _MAX_OVERLAP_SCAN)
    for size in range(limit, 15, -1):
        if left.endswith(right[:size]):
            return left + right[size:]
    return left + "\n" + right


class KnowledgeRetrieval:
    """知识检索器（无状态；存储可注入便于测试）"""

    def __init__(self, store: Optional[KnowledgeStore] = None):
        self._store = store

    @property
    def store(self) -> KnowledgeStore:
        if self._store is None:
            self._store = KnowledgeStore.get_instance()
        return self._store

    # @link:block Knowledge_Search = Retrieve
    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        path_prefix: str = "",
    ) -> List[KnowledgeHit]:
        """检索知识片段，返回按相关性排序、带完整出处的命中列表。"""
        query = (query or "").strip()
        if not query or not is_enabled():
            return []
        top_k = int(top_k or getattr(settings, "KNOWLEDGE_TOP_K", 5) or 5)
        top_k = max(1, min(20, top_k))
        pool = max(top_k * 3, 12)
        min_semantic = float(getattr(settings, "KNOWLEDGE_MIN_SEMANTIC", 0.30))

        vec_hits: List[tuple] = []
        try:
            vec = self.store.embed_query(query)
            if vec:
                vec_hits = self.store.search_vector(vec, pool)
        except Exception as e:
            logger.warning(f"[knowledge] 向量路失败，仅用关键词路: {e}")
        kw_hits = self.store.search_keyword(query, pool)

        # 绝对相关性门槛：向量分低且关键词未命中 → 丢弃（避免无关注入）
        kw_ids = {cid for cid, _ in kw_hits}
        vec_ids = [cid for cid, score in vec_hits if score >= min_semantic or cid in kw_ids]
        scores = reciprocal_rank_fusion([vec_ids, [cid for cid, _ in kw_hits]])
        if not scores:
            return []

        candidates = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[: pool * 2]
        chunks = self.store.get_chunks([cid for cid, _ in candidates])
        if not chunks:
            return []
        score_of = dict(candidates)
        meta = self.store.doc_meta_map([c.doc_id for c in chunks])

        hits: List[KnowledgeHit] = []
        for c in chunks:
            info = meta.get(c.doc_id) or {}
            source_path = info.get("source_path", "")
            if path_prefix and not source_path.startswith(path_prefix):
                continue
            hits.append(KnowledgeHit(
                chunk_id=c.id, doc_id=c.doc_id,
                title=info.get("title", ""), heading_path=c.heading_path,
                text=c.text, score=score_of.get(c.id, 0.0),
                source_path=source_path,
                char_start=c.char_start, char_end=c.char_end,
                seq=c.seq, token_count=c.token_count,
            ))

        hits = self._merge_adjacent(hits)
        hits = self._limit_per_doc(hits, int(getattr(settings, "KNOWLEDGE_MAX_CHUNKS_PER_DOC", 3)))
        return self._fit_budget(hits, top_k)

    def _merge_adjacent(self, hits: List[KnowledgeHit]) -> List[KnowledgeHit]:
        """合并同一文档内 seq 连续的命中，避免返回半句话。"""
        by_doc: Dict[str, List[KnowledgeHit]] = {}
        for h in hits:
            by_doc.setdefault(h.doc_id, []).append(h)

        merged: List[KnowledgeHit] = []
        for doc_id, items in by_doc.items():
            items.sort(key=lambda h: (h.seq, h.char_start))
            current = items[0]
            for nxt in items[1:]:
                if nxt.seq == current.seq + 1:
                    current.text = _merge_texts(current.text, nxt.text)
                    current.char_end = max(current.char_end, nxt.char_end)
                    current.score = max(current.score, nxt.score)
                    current.seq = nxt.seq
                    current.token_count = count_tokens(current.text)
                else:
                    merged.append(current)
                    current = nxt
            merged.append(current)
        merged.sort(key=lambda h: h.score, reverse=True)
        return merged

    @staticmethod
    def _limit_per_doc(hits: List[KnowledgeHit], per_doc: int) -> List[KnowledgeHit]:
        counts: Dict[str, int] = {}
        kept: List[KnowledgeHit] = []
        for h in hits:
            if counts.get(h.doc_id, 0) >= per_doc:
                continue
            counts[h.doc_id] = counts.get(h.doc_id, 0) + 1
            kept.append(h)
        return kept

    @staticmethod
    def _fit_budget(hits: List[KnowledgeHit], top_k: int) -> List[KnowledgeHit]:
        """按 token 预算裁剪（至少保留一条，避免查询必然返回空）。"""
        budget = int(getattr(settings, "KNOWLEDGE_MAX_INJECT_TOKENS", 1200))
        kept: List[KnowledgeHit] = []
        used = 0
        for h in hits:
            if len(kept) >= top_k:
                break
            cost = h.token_count or count_tokens(h.text)
            if kept and used + cost > budget:
                continue
            kept.append(h)
            used += cost
            if used >= budget:
                break
        return kept

    # @link:block Knowledge_Format = Write
    def format(self, hits: Sequence[KnowledgeHit]) -> str:
        """渲染为可引用文本（供 prompt 注入）。"""
        if not hits:
            return ""
        lines = ["【参考资料】（来自知识库，引用时请标注文档与章节）"]
        for i, h in enumerate(hits, start=1):
            # 标题已经单独展示，heading_path 里重复的一级标题要剥掉，避免「《X》§X > 2. Y」
            heading = h.heading_path or ""
            if h.title and (heading == h.title or heading.startswith(h.title + " > ")):
                heading = "" if heading == h.title else heading[len(h.title) + 3:]
            head = f"[{i}] 《{h.title or '未命名文档'}》"
            if heading:
                head += f" §{heading}"
            head += f"  ({h.source_path})"
            body = "\n".join("    " + ln for ln in h.text.strip().splitlines())
            lines.append(head)
            lines.append(body)
        return "\n".join(lines)


def format_hits(hits: Sequence[KnowledgeHit]) -> str:
    return KnowledgeRetrieval().format(hits)


def should_retrieve(query: str) -> bool:
    """知识型意图判定：避免「午饭吃什么」也去查文档。

    命中任一条件才自动检索：知识型意图词 / 文档路径引用。
    模型仍可绕过此判定直接调用 knowledge_search 工具。
    """
    if not query or not is_enabled() or not getattr(settings, "KNOWLEDGE_AUTO_INJECT", True):
        return False
    q = query.lower()
    for pat in _KNOWLEDGE_INTENT_PATTERNS:
        if pat in q:
            return True
    # 文档引用：路径样式字符串（含 / 且带常见扩展名）
    for ext in (".md", ".py", ".txt", ".json", ".yaml", ".yml", ".toml"):
        if ext in q and "/" in q:
            return True
    return False


_KNOWLEDGE_INTENT_PATTERNS = (
    "文档", "规范", "接口", "配置", "怎么用", "如何使用", "定义", "参数",
    "架构", "设计", "说明", "手册", "协议", "字段", "格式",
    "api", "readme", "docs/", "文档里", "按文档",
)


__all__ = [
    "KnowledgeRetrieval", "format_hits", "reciprocal_rank_fusion", "should_retrieve",
]
