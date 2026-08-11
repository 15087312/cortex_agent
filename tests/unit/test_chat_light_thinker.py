"""chat_light/continuous_thinker 测试（此前 26% 覆盖）：单模型思考循环"""
import asyncio
import math
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.thinking.chat_light.continuous_thinker import ContinuousThinker


def _thinker():
    t = ContinuousThinker.__new__(ContinuousThinker)
    t._runner = MagicMock()
    t._slicer = MagicMock()
    t._blackboard = MagicMock()
    t._composer = MagicMock()
    t._session_locks = {}
    t._session_locks_guard = threading.Lock()
    return t


@pytest.fixture
def mem_store(tmp_path, monkeypatch):
    """真实临时记忆库（确定性嵌入）"""
    import modules.database.connection as conn
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "clt.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", __import__("threading").RLock())
    from modules.memory.event_store import EventStore
    from modules.memory.embedding import EmbeddingEngine
    store = EventStore(
        db_path=str(tmp_path / "er.db"),
        faiss_index_path=str(tmp_path / "er.faiss"),
        id_map_path=str(tmp_path / "er_id.json"),
    )
    eng = EmbeddingEngine()
    eng._loaded = True
    eng._attempted = True
    eng.dim = 16

    # 词袋式确定性嵌入：同词同向量，异词正交。
    # 事件自动向量化文本含 keywords（"过去的事件 事件"），查询"事件"可稳定命中。
    _vocab = {}

    def _embed(text):
        import re
        tokens = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z0-9_]+', text or "") or [text or ""]
        vec = [0.0] * 16
        for t in tokens:
            if t not in _vocab:
                _vocab[t] = len(_vocab) % 16
            vec[_vocab[t]] += 1.0
        norm = math.sqrt(sum(x * x for x in vec))
        if norm < 1e-12:
            return [0.0] * 16
        return [x / norm for x in vec]
    eng.embed = _embed
    eng.embed_batch = lambda texts: [_embed(t) for t in texts]
    monkeypatch.setattr(EmbeddingEngine, "get_instance", classmethod(lambda cls: eng))
    return store


def test_session_lock_same_session():
    t = _thinker()
    async def go():
        l1 = t._session_lock("s1")
        l2 = t._session_lock("s1")
        l3 = t._session_lock("s2")
        return l1, l2, l3
    l1, l2, l3 = asyncio.run(go())
    assert l1 is l2
    assert l1 is not l3


def test_is_new_topic():
    history = [{"role": "user", "content": "排序算法，帮我写一个"}]
    assert ContinuousThinker._is_new_topic("市场行情，如何分析", history) is True
    assert ContinuousThinker._is_new_topic("排序算法，继续优化", history) is False
    assert ContinuousThinker._is_new_topic("abc", []) is False  # 无历史


def test_get_blackboard():
    t = _thinker()
    assert t.get_blackboard() is t._blackboard


def test_recall_memories_no_history(tmp_path, monkeypatch):
    """真实 DB 无历史：返回空"""
    import modules.database.connection as conn
    from modules.database.session_repo import SessionRepository
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "clt.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", __import__("threading").RLock())
    conn.get_db_manager().initialize()
    import modules.database.session_repo as sr
    monkeypatch.setattr(sr, "get_session_repo", lambda: SessionRepository())
    t = _thinker()
    t._blackboard.get_messages.return_value = []
    assert asyncio.run(t._recall_memories("q", "s1")) == ""


def test_recall_memories_with_history(mem_store, tmp_path, monkeypatch):
    """真实 DB 有历史 + 真实检索：返回相关记忆"""
    import modules.database.connection as conn
    from modules.database.session_repo import SessionRepository
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "clt.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", __import__("threading").RLock())
    conn.get_db_manager().initialize()
    from modules.database.session_repo import SessionRepository as SR
    repo = SR()
    repo.create_session("s1")
    repo.save_message("s1", "user", "之前聊过")
    import modules.database.session_repo as sr
    monkeypatch.setattr(sr, "get_session_repo", lambda: repo)
    from modules.memory.event_store import MemoryEvent
    mem_store.save_event(MemoryEvent(fact="过去的事件", importance=0.8, keywords=["事件"]))
    from modules.memory.event_retrieval import EventRetrieval
    ret = EventRetrieval()
    ret._store = mem_store
    import modules.memory.event_retrieval as er_mod
    import modules.memory.depth_recall as dr_mod
    monkeypatch.setattr(er_mod, "get_event_retrieval", lambda: ret)
    monkeypatch.setattr(dr_mod, "should_trigger_deep_recall", lambda q: (False, None))
    t = _thinker()
    t._blackboard.get_messages.return_value = [{"role": "user", "content": "之前聊过"}]
    out = asyncio.run(t._recall_memories("事件", "s1"))
    assert "曾经发生的事" in out


def test_extract_memory_disabled(monkeypatch):
    """真实 settings：MEMORY_REDUCE_ENABLED=False 时跳过"""
    from config.settings import settings
    old = settings.MEMORY_REDUCE_ENABLED
    settings.MEMORY_REDUCE_ENABLED = False
    try:
        t = _thinker()
        asyncio.run(t._extract_memory("s1", []))
    finally:
        settings.MEMORY_REDUCE_ENABLED = old


def test_extract_memory_too_short():
    """真实 settings：内容过短不提炼"""
    t = _thinker()
    asyncio.run(t._extract_memory("s1", [{"role": "user", "content": "短"}]))
    # 不抛异常
