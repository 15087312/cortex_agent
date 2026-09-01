"""chat_light continuous_thinker 扩展测试：think 完整流程 / 记忆召回深分支"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.thinking.chat_light.continuous_thinker import ContinuousThinker


def _thinker():
    t = ContinuousThinker.__new__(ContinuousThinker)
    # 黑板已删除（DB 为唯一真源），不再需要 _blackboard 实例
    t._session_locks = {}
    t._session_locks_guard = MagicMock()
    t._runner = MagicMock()
    t._slicer = MagicMock()
    t._composer = MagicMock()
    return t


async def test_think_happy_path_with_mental(monkeypatch):
    t = _thinker()
    t._recall_memories = AsyncMock(return_value="记忆")
    t._slicer.slice = AsyncMock(return_value=[{"role": "user", "content": "你好"}])
    t._composer.build_system = MagicMock(return_value="sys")
    t._extract_memory = AsyncMock()

    # 良知返回内心独白
    fake_cons = MagicMock()
    fake_cons.think = AsyncMock(return_value="内心独白")
    monkeypatch.setattr("modules.thinking.conscience.get_conscience", lambda: fake_cons)
    monkeypatch.setattr("infra.model.small_model_client.SmallModelClient", MagicMock())

    # runner 返回带 reasoning 的响应
    response = MagicMock()
    response.message.reasoning_content = "推理过程"
    response.message.content = "助手回复"

    async def fake_run(messages, system_prompt, on_token=None, max_tokens=None, temperature=None):
        if on_token:
            on_token("助手")
        return response

    t._runner.run = fake_run

    q = asyncio.Queue()
    await t.think("s1", "你好", q)
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    types = {i["type"] for i in items}
    assert "mental" in types
    assert "message" in types
    assert "thinking" in types  # reasoning 推送
    assert "done" in types


async def test_think_on_token_queue_full(monkeypatch):
    t = _thinker()
    t._recall_memories = AsyncMock(return_value="")
    t._slicer.slice = AsyncMock(return_value=[])
    t._composer.build_system = MagicMock(return_value="sys")
    t._extract_memory = AsyncMock()

    class SmallQueue:
        def __init__(self):
            self.full = True
            self.items = []

        def put_nowait(self, item):
            if self.full:
                raise asyncio.QueueFull()
            self.items.append(item)

        async def put(self, item):
            self.items.append(item)

    q = SmallQueue()
    response = MagicMock()
    response.message.reasoning_content = ""
    response.message.content = "回复"
    t._runner.run = AsyncMock(return_value=response)
    await t.think("s1", "你好", q)
    assert q.items  # done 已放入


async def test_recall_deep_and_shallow(monkeypatch):
    t = _thinker()
    # 历史对话改由 DB 判断：mock 出有历史，触发记忆召回
    db_repo = MagicMock()
    db_repo.get_recent_messages = MagicMock(return_value=[{"role": "user", "content": "之前的历史对话内容"}])
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: db_repo)
    from modules.memory.event_store import MemoryEvent

    class FakeRetrieval:
        async def retrieve(self, query, max_results=10):
            return [MemoryEvent(fact="发生过的事件", lesson="经验教训", time="2026-01-01T00:00:00", importance=0.8)]

    deep_result = MagicMock()
    deep_result.success = True
    deep_result.fallback = False
    scheduler = MagicMock()
    scheduler.deep_recall = AsyncMock(return_value=deep_result)
    format_result = MagicMock(return_value="深度回忆结论")

    monkeypatch.setattr("modules.memory.depth_recall.should_trigger_deep_recall", lambda q: (True, "logic"))
    monkeypatch.setattr("modules.memory.depth_recall.DepthRecallScheduler", lambda: scheduler)
    monkeypatch.setattr("modules.memory.result_fusion.format_deep_recall_result", format_result)
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval", lambda: FakeRetrieval())

    out = await t._recall_memories("为什么", "s1")
    assert "深度回忆结论" in out
    assert "曾经发生的事" in out


async def test_recall_deep_fails_shallow_ok(monkeypatch):
    t = _thinker()
    db_repo = MagicMock()
    db_repo.get_recent_messages = MagicMock(return_value=[{"role": "user", "content": "历史对话"}])
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: db_repo)
    monkeypatch.setattr("modules.memory.depth_recall.should_trigger_deep_recall", lambda q: (True, "x"))
    monkeypatch.setattr("modules.memory.depth_recall.DepthRecallScheduler", lambda: (_ for _ in ()).throw(RuntimeError("deep fail")))
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval", lambda: (_ for _ in ()).throw(RuntimeError("retr fail")))
    out = await t._recall_memories("q", "s1")
    assert out == ""


async def test_extract_memory_success(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "MEMORY_REDUCE_ENABLED", True)
    t = _thinker()
    reducer = MagicMock()
    reducer.reduce = AsyncMock(return_value=[MagicMock()])
    client = MagicMock()
    t._runner = MagicMock()
    t._runner.client = client
    monkeypatch.setattr("modules.memory.event_reducer.EventReducer", lambda **kw: reducer)
    # 对话从 DB 取
    repo = MagicMock()
    repo.get_messages = MagicMock(return_value=[
        {"role": "user", "content": "a" * 60}, {"role": "assistant", "content": "b" * 60}])
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: repo)
    await t._extract_memory("s1")
    reducer.reduce.assert_awaited_once()
