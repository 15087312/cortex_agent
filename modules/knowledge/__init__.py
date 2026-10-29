"""知识库（文档 RAG）— 只存知识的独立检索库

与事件记忆（modules.memory）的关系：并存但完全分离。
- 事件记忆回答「我经历过什么」（LLM 摘要、时间衰减、按模型归属）
- 知识库回答「世界/项目是什么样」（原文保真、带出处、不衰减、全局共享）

设计见 docs/KNOWLEDGE_BASE_DESIGN.md。
"""
from modules.knowledge.models import (  # noqa: F401
    IngestResult, KnowledgeChunk, KnowledgeDoc, KnowledgeHit,
)
from modules.knowledge.service import (  # noqa: F401
    KnowledgeService, get_knowledge_service, reset_knowledge_service,
)
from modules.knowledge.store import KnowledgeStore, get_knowledge_store  # noqa: F401

__all__ = [
    "KnowledgeDoc", "KnowledgeChunk", "KnowledgeHit", "IngestResult",
    "KnowledgeStore", "get_knowledge_store",
    "KnowledgeService", "get_knowledge_service", "reset_knowledge_service",
]
