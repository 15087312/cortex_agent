"""知识库门面 — 工具层与 REST 的唯一入口

工具层（infra.tool_manager）通过 service_registry 能力 "knowledge_base" 拿到本对象，
不直接 import modules；这样保持 infra → modules 的单向依赖倒置约定。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from config.settings import settings
from utils.logger import setup_logger
from modules.knowledge.ingestor import KnowledgeIngestor
from modules.knowledge.retrieval import KnowledgeRetrieval, should_retrieve
from modules.knowledge.store import KnowledgeStore, is_enabled

logger = setup_logger("knowledge_service")


class KnowledgeService:
    """知识库门面（检索 / 摄取 / 列举 / 删除 / 统计）"""

    def __init__(
        self,
        store: Optional[KnowledgeStore] = None,
        ingestor: Optional[KnowledgeIngestor] = None,
        retrieval: Optional[KnowledgeRetrieval] = None,
    ):
        self._store = store
        self._ingestor = ingestor
        self._retrieval = retrieval

    @property
    def store(self) -> KnowledgeStore:
        if self._store is None:
            self._store = KnowledgeStore.get_instance()
        return self._store

    @property
    def ingestor(self) -> KnowledgeIngestor:
        if self._ingestor is None:
            self._ingestor = KnowledgeIngestor(store=self.store)
        return self._ingestor

    @property
    def retrieval(self) -> KnowledgeRetrieval:
        if self._retrieval is None:
            self._retrieval = KnowledgeRetrieval(store=self.store)
        return self._retrieval

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: Optional[int] = None, path_prefix: str = "") -> List[Dict[str, Any]]:
        if not is_enabled():
            return []
        hits = self.retrieval.search(query, top_k=top_k, path_prefix=path_prefix)
        return [h.to_dict() for h in hits]

    def search_text(self, query: str, top_k: Optional[int] = None, path_prefix: str = "") -> str:
        """渲染为可引用文本（空结果返回空串）。"""
        if not is_enabled():
            return ""
        hits = self.retrieval.search(query, top_k=top_k, path_prefix=path_prefix)
        return self.retrieval.format(hits)

    def should_retrieve(self, query: str) -> bool:
        return should_retrieve(query)

    def list_docs(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        return [d.to_dict() for d in self.store.list_documents(limit=limit, offset=offset)]

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def ingest(self, path: str = "") -> Dict[str, Any]:
        """摄取文件/目录；path 为空时按白名单全量增量摄取。"""
        if not is_enabled():
            return {"error": "知识库已关闭（KNOWLEDGE_ENABLED=false）"}
        result = self.ingestor.ingest_path(path) if path else self.ingestor.ingest_roots()
        return result.to_dict()

    def delete(self, doc_id: str) -> Dict[str, Any]:
        if not is_enabled():
            return {"error": "知识库已关闭（KNOWLEDGE_ENABLED=false）"}
        return self.store.delete_document(doc_id)

    def reindex(self, full: bool = False) -> Dict[str, Any]:
        if not is_enabled():
            return {"error": "知识库已关闭（KNOWLEDGE_ENABLED=false）"}
        return self.ingestor.reindex(full=full)

    def stats(self) -> Dict[str, Any]:
        data = self.store.stats()
        data["roots"] = [str(p) for p in self.ingestor.roots()]
        data["auto_inject"] = bool(getattr(settings, "KNOWLEDGE_AUTO_INJECT", True))
        return data


_service: Optional[KnowledgeService] = None


def get_knowledge_service() -> KnowledgeService:
    global _service
    if _service is None:
        _service = KnowledgeService()
    return _service


def reset_knowledge_service() -> None:
    """释放门面缓存（测试 / 配置变更用）。"""
    global _service
    _service = None


__all__ = ["KnowledgeService", "get_knowledge_service", "reset_knowledge_service"]
