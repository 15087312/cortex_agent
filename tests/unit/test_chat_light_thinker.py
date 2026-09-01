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
    # 黑板已删除（DB 为唯一真源），不再需要 _blackboard
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
    # _recall_memories 从 DB 判断历史（黑板已删除）
    out = asyncio.run(t._recall_memories("事件", "s1"))
    assert "曾经发生的事" in out


def test_extract_memory_disabled(monkeypatch):
    """真实 settings：MEMORY_REDUCE_ENABLED=False 时跳过"""
    from config.settings import settings
    old = settings.MEMORY_REDUCE_ENABLED
    settings.MEMORY_REDUCE_ENABLED = False
    try:
        t = _thinker()
        asyncio.run(t._extract_memory("s1"))  # 仅传 session_id，对话从 DB 取
    finally:
        settings.MEMORY_REDUCE_ENABLED = old


def test_extract_memory_too_short(monkeypatch):
    """真实 settings：内容过短不提炼"""
    from config.settings import settings
    monkeypatch.setattr(settings, "MEMORY_REDUCE_ENABLED", True)
    # mock DB 返回过短对话，验证门控
    repo = MagicMock()
    repo.get_messages = MagicMock(return_value=[{"role": "user", "content": "短"}])
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: repo)
    t = _thinker()
    asyncio.run(t._extract_memory("s1"))  # 不抛异常


# ── model_params 尊重激活编排角色（temperature/max_tokens）──────────────

def test_model_params_resolves_active_large_role(monkeypatch):
    """_model_params() 读取激活大模型角色的 model_params（修复：纯对话此前忽略）"""
    from config.settings import settings
    monkeypatch.setattr(type(settings), "get_model_params",
                        lambda self, role: {"max_tokens": 768, "temperature": 0.7})
    t = _thinker()
    mp = t._model_params()
    assert mp.get("max_tokens") == 768
    assert mp.get("temperature") == 0.7


def test_model_params_empty_on_error(monkeypatch):
    """_model_params() 内部异常时回退空 dict，不中断对话"""
    from config.settings import settings
    def boom(self, role):
        raise RuntimeError("boom")
    monkeypatch.setattr(type(settings), "get_model_params", boom)
    t = _thinker()
    assert t._model_params() == {}


def test_runner_passes_model_params_to_chat_stream(monkeypatch):
    """run() 传入的 max_tokens/temperature 应透传给 chat_stream"""
    from modules.thinking.chat_light.model_runner import ModelRunner

    r = ModelRunner.__new__(ModelRunner)
    r._client = MagicMock()
    r._client_cfg = ("cfg",)
    # 固定 client property：run() 走 self.client（懒建/重建逻辑），必须 mock 到该属性
    monkeypatch.setattr(ModelRunner, "client", property(lambda self: self._client))

    class _Resp:
        message = MagicMock()
    r._client.chat_stream = AsyncMock(return_value=_Resp())

    async def go():
        return await r.run(
            messages=[{"role": "user", "content": "hi"}],
            system_prompt="sys",
            max_tokens=768,
            temperature=0.7,
        )
    asyncio.run(go())
    _, kwargs = r._client.chat_stream.call_args
    assert kwargs["max_tokens"] == 768
    assert kwargs["temperature"] == 0.7


def test_runner_defaults_to_global_when_no_params(monkeypatch):
    """run() 未传 max_tokens/temperature 时回退全局 MODEL_* 配置"""
    from modules.thinking.chat_light.model_runner import ModelRunner
    from config.settings import settings

    r = ModelRunner.__new__(ModelRunner)
    r._client = MagicMock()
    r._client_cfg = ("cfg",)
    monkeypatch.setattr(ModelRunner, "client", property(lambda self: self._client))

    class _Resp:
        message = MagicMock()
    r._client.chat_stream = AsyncMock(return_value=_Resp())

    async def go():
        return await r.run(
            messages=[{"role": "user", "content": "hi"}],
            system_prompt="sys",
        )
    asyncio.run(go())
    _, kwargs = r._client.chat_stream.call_args
    assert kwargs["max_tokens"] == settings.MODEL_MAX_TOKENS
    assert kwargs["temperature"] == settings.MODEL_TEMPERATURE
