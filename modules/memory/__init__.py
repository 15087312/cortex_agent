"""
事件记忆系统 + 因果树深度回忆

架构：
   会话结束 → EventReducer (LLM) → MemoryEvent → EventStore (SQLite + FAISS)
   用户提问 → EventRetrieval (RAG) → 相关事件 → 注入 prompt
   深度分析 → CausalGraph (因果图) → CausalTree (树下钻) → 事件池精准召回

事件结构:
   {id, fact, thought, lesson, keywords, importance, time, session_id, causal_node_ids}
"""
from modules.memory.event_store import EventStore, MemoryEvent
from modules.memory.event_reducer import EventReducer, get_reducer
from modules.memory.event_retrieval import EventRetrieval, get_event_retrieval
from modules.memory.embedding import EmbeddingEngine
from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
from modules.memory.causal_tree import CausalTree, CausalChain, CausalTreeResult
from modules.memory.depth_recall import DepthRecallScheduler, DeepRecallResult, should_trigger_deep_recall, classify_intent
from modules.memory.result_fusion import ResultFusion, format_deep_recall_result

__all__ = [
    "EventStore", "MemoryEvent",
    "EventReducer", "get_reducer",
    "EventRetrieval", "get_event_retrieval",
    "EmbeddingEngine",
    "CausalGraph", "CausalNode", "CausalEdge",
    "CausalTree", "CausalChain", "CausalTreeResult",
    "DepthRecallScheduler", "DeepRecallResult", "should_trigger_deep_recall", "classify_intent",
    "ResultFusion", "format_deep_recall_result",
]
