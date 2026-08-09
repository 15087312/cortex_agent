"""chat_light/continuous_thinker 测试（此前 26% 覆盖）：单模型思考循环"""
import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

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


def test_think_success(monkeypatch):
    import modules.thinking.conscience as cons_mod
    fake_cons = MagicMock()
    fake_cons.think = AsyncMock(return_value="")
    monkeypatch.setattr(cons_mod, "get_conscience", lambda: fake_cons)
    import config.settings as cfg_mod
    import importlib
    cfg = importlib.import_module("config.settings")
    monkeypatch.setattr(cfg, "settings", MagicMock())
    t = _thinker()
    t._recall_memories = AsyncMock(return_value="记忆")
    t._slicer.slice = AsyncMock(return_value=[{"role": "user", "content": "hi"}])
    t._composer.build_system.return_value = "system"
    t._blackboard.add_message = MagicMock()
    t._blackboard.get_messages.return_value = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "你好"}]
    t._extract_memory = AsyncMock()
    runner = MagicMock()
    resp = MagicMock()
    resp.message = MagicMock(content="你好", reasoning_content="")
    async def fake_run(messages, system_prompt, on_token):
        on_token("你")
        on_token("好")
        return resp
    runner.run = fake_run
    t._runner = runner
    q = asyncio.Queue()

    async def go():
        await t.think("s1", "hi", q)
    asyncio.run(go())
    # 流式 token 入队 + done 信号
    kinds = set()
    while not q.empty():
        item = q.get_nowait()
        kinds.add(item.get("type"))
    assert "message" in kinds
    assert "done" in kinds
    assert t._blackboard.add_message.call_count == 2  # user + assistant


def test_recall_memories_no_history(monkeypatch):
    t = _thinker()
    t._blackboard.get_messages.return_value = []
    import modules.database.session_repo as sr
    repo = MagicMock()
    repo.get_recent_messages.return_value = []
    monkeypatch.setattr(sr, "get_session_repo", lambda: repo)
    assert asyncio.run(t._recall_memories("q", "s1")) == ""


def test_recall_memories_with_history(monkeypatch):
    t = _thinker()
    t._blackboard.get_messages.return_value = [{"role": "user", "content": "之前聊过"}]
    class Ev:
        time = "2025-01-01 10:00"
        importance = 0.8
        fact = "过去的事件"
        lesson = "经验"
    retrieval = MagicMock()
    retrieval.retrieve = AsyncMock(return_value=[Ev()])
    import modules.memory.event_retrieval as er_mod
    import modules.memory.depth_recall as dr_mod
    import modules.memory.result_fusion as rf_mod
    monkeypatch.setattr(er_mod, "get_event_retrieval", lambda: retrieval)
    monkeypatch.setattr(dr_mod, "should_trigger_deep_recall", lambda q: (False, None))
    out = asyncio.run(t._recall_memories("q", "s1"))
    assert "曾经发生的事" in out
    assert "过去的事件" in out


def test_extract_memory_disabled(monkeypatch):
    t = _thinker()
    import modules.thinking.chat_light.continuous_thinker as mod
    monkeypatch.setattr(mod, "settings", type("S", (), {"MEMORY_REDUCE_ENABLED": False})())
    asyncio.run(t._extract_memory("s1", []))
    # 不抛异常


def test_extract_memory_too_short(monkeypatch):
    t = _thinker()
    import modules.thinking.chat_light.continuous_thinker as mod
    monkeypatch.setattr(mod, "settings", type("S", (), {"MEMORY_REDUCE_ENABLED": True})())
    asyncio.run(t._extract_memory("s1", [{"role": "user", "content": "短"}]))
    # 不抛异常


def test_extract_memory_reducer(monkeypatch):
    t = _thinker()
    import modules.thinking.chat_light.continuous_thinker as mod
    monkeypatch.setattr(mod, "settings", type("S", (), {"MEMORY_REDUCE_ENABLED": True})())
    t._runner.client = MagicMock()
    reducer = MagicMock()
    reducer.reduce = AsyncMock(return_value=[{"id": "e1"}])
    import modules.memory.event_reducer as er_mod
    monkeypatch.setattr(er_mod, "EventReducer", lambda model_client: reducer)
    msgs = [{"role": "user", "content": "长内容" * 20}]
    asyncio.run(t._extract_memory("s1", msgs))
    reducer.reduce.assert_awaited_once()
