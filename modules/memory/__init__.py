"""
事件记忆系统

架构：
  会话结束 → EventReducer (LLM) → MemoryEvent → EventStore (SQLite + FAISS)
  用户提问 → EventRetrieval (RAG) → 相关事件 → EventStrategy → 策略注入 prompt

事件结构:
  {id, fact, thought, lesson, keywords, importance, time, session_id}
"""
from modules.memory.event_store import EventStore, MemoryEvent
from modules.memory.event_reducer import EventReducer, get_reducer
from modules.memory.event_retrieval import EventRetrieval, get_event_retrieval
from modules.memory.event_strategy import EventStrategy, format_strategy_for_prompt
from modules.memory.embedding import EmbeddingEngine
