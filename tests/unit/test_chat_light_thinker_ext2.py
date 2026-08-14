"""chat_light continuous_thinker 补测：conscience 异常 / token 队列满 / reasoning 异常 / 记忆召回回退 / 记忆提取边界"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from modules.thinking.chat_light.continuous_thinker import ContinuousThinker
from modules.thinking.chat_light.blackboard import Blackboard


def _thinker():
    t = ContinuousThinker.__new__(ContinuousThinker)
    t._blackboard = Blackboard()
    t._session_locks = {}
    t._session_locks_guard = MagicMock()
    t._runner = MagicMock()
    t._slicer = MagicMock()
    t._composer = MagicMock()
    return t


# ── think：conscience 异常 / token 队列满 / reasoning 异常 ─────────────

async def test_think_conscience_error(monkeypatch):
    t = _thinker()
    t._recall_memories = AsyncMock(return_value="")
    t._slicer.slice = AsyncMock(return_value=[])
    t._composer.build_system = MagicMock(return_value="sys")
    t._extract_memory = AsyncMock()
    monkeypatch.setattr("modules.thinking.conscience.get_conscience",
                        lambda: (_ for _ in ()).throw(RuntimeError("cons down")))
    response = MagicMock()
    response.message.reasoning_content = ""
    response.message.content = "回复"
    t._runner.run = AsyncMock(return_value=response)
    q = asyncio.Queue()
    await t.think("s1", "你好", q)
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert any(i["type"] == "done" for i in items)


async def test_think_on_token_queue_full_called(monkeypatch):
    t = _thinker()
    t._recall_memories = AsyncMock(return_value="")
    t._slicer.slice = AsyncMock(return_value=[])
    t._composer.build_system = MagicMock(return_value="sys")
    t._extract_memory = AsyncMock()
    fake_cons = MagicMock()
    fake_cons.think = AsyncMock(return_value="")
    monkeypatch.setattr("modules.thinking.conscience.get_conscience", lambda: fake_cons)
    monkeypatch.setattr("infra.model.small_model_client.SmallModelClient", MagicMock())

    class FullQueue:
        def put_nowait(self, item):
            raise asyncio.QueueFull()

        async def put(self, item):
            pass

    q = FullQueue()
    response = MagicMock()
    response.message.reasoning_content = ""
    response.message.content = "回复"

    def fake_run(messages, system_prompt, on_token=None):
        if on_token:
            on_token("助手")  # 触发 QueueFull → 104-105 捕获
        return response

    t._runner.run = fake_run
    await t.think("s1", "你好", q)  # 不抛


async def test_think_reasoning_error(monkeypatch):
    t = _thinker()
    t._recall_memories = AsyncMock(return_value="")
    t._slicer.slice = AsyncMock(return_value=[])
    t._composer.build_system = MagicMock(return_value="sys")
    t._extract_memory = AsyncMock()
    fake_cons = MagicMock()
    fake_cons.think = AsyncMock(return_value="")
    monkeypatch.setattr("modules.thinking.conscience.get_conscience", lambda: fake_cons)
    monkeypatch.setattr("infra.model.small_model_client.SmallModelClient", MagicMock())

    class BoomMessage:
        @property
        def reasoning_content(self):
            raise RuntimeError("reasoning down")

        content = "回复"

    response = MagicMock()
    response.message = BoomMessage()
    t._runner.run = AsyncMock(return_value=response)
    q = asyncio.Queue()
    await t.think("s1", "你好", q)
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert any(i["type"] == "done" for i in items)


# ── _recall_memories：黑板异常 / DB 兜底 / 外层异常 ───────────────────

async def test_recall_blackboard_none_db_fallback(monkeypatch):
    t = _thinker()
    t._blackboard = None
    db_repo = MagicMock()
    db_repo.get_recent_messages = MagicMock(return_value=[
        {"role": "user", "content": "历史消息内容"},
    ])
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: db_repo)
    monkeypatch.setattr("modules.memory.depth_recall.should_trigger_deep_recall", lambda q: (False, ""))

    class FakeRetrieval:
        async def retrieve(self, query, max_results=10):
            return []

    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval", lambda: FakeRetrieval())
    out = await t._recall_memories("q", "s1")
    assert out == ""  # 深度未触发 + 浅层为空 → 空串


async def test_recall_blackboard_error(monkeypatch):
    t = _thinker()
    bb = MagicMock()
    bb.get_messages = MagicMock(side_effect=RuntimeError("bb down"))
    t._blackboard = bb
    db_repo = MagicMock()
    db_repo.get_recent_messages = MagicMock(return_value=[])
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: db_repo)
    out = await t._recall_memories("q", "s1")
    assert out == ""  # 黑板异常 + DB 无历史 → 空


async def test_recall_outer_exception(monkeypatch):
    t = _thinker()
    t._blackboard.add_message("s1", "user", "历史对话内容")
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "modules.memory.result_fusion":
            raise ImportError("no fusion")
        return real_import(name, *a, **k)

    with patch("builtins.__import__", fake_import):
        out = await t._recall_memories("q", "s1")
    assert out == ""  # 导入失败 → 外层 except 兜底


# ── _extract_memory：非对话消息 / 空事件 / 异常 ───────────────────────

async def test_extract_memory_skip_non_dialog(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "MEMORY_REDUCE_ENABLED", True)
    t = _thinker()
    reducer = MagicMock()
    reducer.reduce = AsyncMock(return_value=[])
    monkeypatch.setattr("modules.memory.event_reducer.EventReducer", lambda **kw: reducer)
    t._runner.client = MagicMock()
    messages = [{"role": "system", "content": "x" * 60}]  # 非 user/assistant → 跳过
    await t._extract_memory("s1", messages)
    reducer.reduce.assert_not_awaited()


async def test_extract_memory_events_empty(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "MEMORY_REDUCE_ENABLED", True)
    t = _thinker()
    reducer = MagicMock()
    reducer.reduce = AsyncMock(return_value=[])
    monkeypatch.setattr("modules.memory.event_reducer.EventReducer", lambda **kw: reducer)
    t._runner.client = MagicMock()
    messages = [{"role": "user", "content": "a" * 60}, {"role": "assistant", "content": "b" * 60}]
    await t._extract_memory("s1", messages)  # 无事件 → 不记日志
    reducer.reduce.assert_awaited_once()


async def test_extract_memory_reduce_error(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "MEMORY_REDUCE_ENABLED", True)
    t = _thinker()
    reducer = MagicMock()
    reducer.reduce = AsyncMock(side_effect=RuntimeError("reduce boom"))
    monkeypatch.setattr("modules.memory.event_reducer.EventReducer", lambda **kw: reducer)
    t._runner.client = MagicMock()
    messages = [{"role": "user", "content": "a" * 60}, {"role": "assistant", "content": "b" * 60}]
    await t._extract_memory("s1", messages)  # 异常 → warning
