"""chat_light 扩展测试：blackboard / model_runner / context_slicer / continuous_thinker"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.thinking.chat_light.blackboard import Blackboard, SessionState
from modules.thinking.chat_light.model_runner import ModelRunner
from modules.thinking.chat_light.context_slicer import ContextSlicer
from modules.thinking.chat_light.continuous_thinker import ContinuousThinker
from modules.thinking.chat_light.prompt_composer import PromptComposer


# ── Blackboard ─────────────────────────────────────────────────────────

def test_blackboard_crud():
    b = Blackboard()
    b.add_message("s1", "user", "你好")
    b.add_message("s1", "assistant", "好的")
    msgs = b.get_messages("s1")
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    b.set_metadata("s1", "k", "v")
    assert b.get_metadata("s1") == {"k": "v"}
    assert isinstance(b.get_or_create("s2"), SessionState)
    b.clear_session("s1")
    assert b.get_messages("s1") == []
    b.add_message("s3", "user", "x")
    b.clear_all()
    assert b._sessions == {}


# ── ModelRunner ────────────────────────────────────────────────────────

async def test_model_runner_run():
    client = MagicMock()
    client.chat_stream = AsyncMock()
    mr = ModelRunner(client=client)
    resp = MagicMock()
    client.chat_stream.return_value = resp
    out = await mr.run(
        messages=[{"role": "user", "content": "hi"}, {"role": "bogus", "content": "skip"}, {"role": "user", "content": ""}],
        system_prompt="sys",
        on_token=lambda t: None,
    )
    assert out is resp
    kwargs = client.chat_stream.call_args.kwargs
    msgs = kwargs["messages"]
    assert msgs[0].role == "system"
    assert len(msgs) == 2  # 非法 role 与空 content 被过滤


def test_model_runner_client_lazy():
    mr = ModelRunner()
    with patch("modules.thinking.chat_light.model_runner.LargeModelClient") as lm:
        c = mr.client
        assert c is lm.return_value
        assert mr.client is c  # 缓存


# ── ContextSlicer ──────────────────────────────────────────────────────

def _msgs(n):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"内容{i}"} for i in range(n)]


async def test_slicer_within_window():
    s = ContextSlicer(window_size=15)
    out = await s.slice(_msgs(5), memory_context="", max_chars=1000)
    assert len(out) == 5


async def test_slicer_with_history_and_overflow(monkeypatch):
    s = ContextSlicer(window_size=3)
    monkeypatch.setattr(s, "_summarize_overflow", AsyncMock(return_value="总结内容"))
    out = await s.slice(_msgs(10), memory_context="记忆", max_chars=30)
    roles = [m["role"] for m in out]
    assert "system" in roles
    assert any("总结" in m.get("content", "") for m in out if m["role"] == "system")
    assert any("记忆" in m.get("content", "") for m in out if m["role"] == "system")


async def test_slicer_summary_fallback(monkeypatch):
    s = ContextSlicer(window_size=2)
    monkeypatch.setattr(s, "_summarize_overflow", AsyncMock(return_value=""))
    out = await s.slice(_msgs(6), memory_context="", max_chars=10)
    sys_msgs = [m for m in out if m["role"] == "system"]
    assert sys_msgs and "摘要" in sys_msgs[0]["content"]


async def test_slicer_window_exceeds_max_chars(monkeypatch):
    s = ContextSlicer(window_size=10)
    monkeypatch.setattr(s, "_summarize_overflow", AsyncMock(return_value="摘要"))
    out = await s.slice(_msgs(5), memory_context="", max_chars=2)
    assert out  # 窗口超限会总结最旧消息


async def test_slicer_split_text_into_chunks():
    s = ContextSlicer(chunk_chars=10)
    chunks = s._split_text_into_chunks("a" * 45)
    assert len(chunks) >= 4
    assert s._split_text_into_chunks("") == []


async def test_slicer_summarize_overflow_empty():
    s = ContextSlicer()
    assert await s._summarize_overflow([]) == ""


async def test_slicer_summarize_chunk_empty():
    s = ContextSlicer()
    assert await s._summarize_chunk("   ") == ""


async def test_slicer_summarize_chunk_error(monkeypatch):
    s = ContextSlicer()
    client = MagicMock()
    client.generate = AsyncMock(side_effect=RuntimeError("down"))
    monkeypatch.setattr(s, "_get_client", lambda: client)
    out = await s._summarize_chunk("这段文本会被降级处理")
    assert out.endswith("...")


async def test_slicer_summarize_chunk_ok(monkeypatch):
    s = ContextSlicer()
    client = MagicMock()
    client.generate = AsyncMock(return_value="摘要：这是一个摘要")
    monkeypatch.setattr(s, "_get_client", lambda: client)
    out = await s._summarize_chunk("对话内容")
    assert out == "这是一个摘要"


# ── PromptComposer ─────────────────────────────────────────────────────

def test_prompt_composer_build_system():
    c = PromptComposer()
    out = c.build_system(memory_context="记忆内容")
    assert "记忆内容" in out


def test_prompt_composer_override(monkeypatch):
    from config.settings import Settings
    monkeypatch.setattr(Settings, "get_system_override", lambda self, role: "高级覆盖")
    c = PromptComposer()
    assert c.build_system() == "高级覆盖"


def test_prompt_composer_custom_persona(monkeypatch):
    from config.settings import Settings, settings
    monkeypatch.setattr(Settings, "get_system_override", lambda self, role: "")
    monkeypatch.setattr(Settings, "get_persona", lambda self, role: "自定义{assistant_name}人设")
    monkeypatch.setattr(Settings, "get_custom_agents", lambda self: [])
    # 确定化：orchestrator 激活，避免依赖真实 personas.yaml 编排状态
    monkeypatch.setattr(Settings, "get_agent_active", lambda self, role: True)
    monkeypatch.setattr(settings, "ASSISTANT_NAME", "小助手")
    c = PromptComposer()
    out = c.build_system()
    assert "自定义小助手人设" in out


def test_prompt_composer_persona_format_error(monkeypatch):
    from config.settings import Settings
    monkeypatch.setattr(Settings, "get_system_override", lambda self, role: "")
    monkeypatch.setattr(Settings, "get_persona", lambda self, role: "带{花括号的人设")
    monkeypatch.setattr(Settings, "get_custom_agents", lambda self: [])
    monkeypatch.setattr(Settings, "get_agent_active", lambda self, role: True)
    c = PromptComposer()
    out = c.build_system()
    assert "花括号" in out  # 原样使用


# ── ContinuousThinker ──────────────────────────────────────────────────

def test_thinker_session_lock():
    t = ContinuousThinker.__new__(ContinuousThinker)
    t._session_locks = {}
    t._session_locks_guard = MagicMock()
    lock1 = t._session_lock("s")
    lock2 = t._session_lock("s")
    assert lock1 is lock2


def test_thinker_is_new_topic():
    t = ContinuousThinker.__new__(ContinuousThinker)
    assert t._is_new_topic("完全不同的新话题", []) is False  # 无历史
    assert t._is_new_topic("", [{"role": "user", "content": "旧"}]) is False  # 空输入
    # 相同内容 → 高度重叠 → 不是新话题
    assert t._is_new_topic("旧话题内容", [{"role": "user", "content": "旧话题内容"}]) is False
    # 完全不同 → 新话题
    assert t._is_new_topic("全新完全不同的话题", [{"role": "user", "content": "旧话题内容"}]) is True


async def test_thinker_think_error_path(monkeypatch):
    fake_cons = MagicMock()
    fake_cons.think = AsyncMock(return_value="")
    monkeypatch.setattr("modules.thinking.conscience.get_conscience", lambda: fake_cons)
    monkeypatch.setattr("infra.model.small_model_client.SmallModelClient", MagicMock())
    t = ContinuousThinker()
    t._blackboard = Blackboard()
    t._runner = MagicMock()
    t._runner.run = AsyncMock(side_effect=RuntimeError("runner fail"))
    t._slicer = MagicMock()
    t._slicer.slice = AsyncMock(return_value=[])
    t._composer = MagicMock()
    t._composer.build_system = MagicMock(return_value="sys")
    t._recall_memories = AsyncMock(return_value="")
    q = asyncio.Queue()
    await t.think("s", "你好", q)
    item = await q.get()
    assert item["type"] == "error"


async def test_thinker_recall_no_prior(monkeypatch):
    t = ContinuousThinker()
    t._blackboard = Blackboard()
    repo = MagicMock()
    repo.get_recent_messages = MagicMock(return_value=[])
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: repo)
    assert await t._recall_memories("q", "sess") == ""


async def test_thinker_recall_with_db_history(monkeypatch):
    t = ContinuousThinker()
    t._blackboard = Blackboard()
    db = MagicMock()
    db.get_recent_messages = MagicMock(return_value=[{"role": "user", "content": "历史"}])
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: db)

    class FakeRetrieval:
        async def retrieve(self, query, max_results=10):
            ev = MagicMock()
            ev.time = "2026-01-01T00:00:00"
            ev.importance = 0.8
            ev.fact = "发生过的事"
            ev.lesson = ""
            return [ev]

    monkeypatch.setattr("modules.memory.depth_recall.should_trigger_deep_recall", lambda q: (False, ""))
    monkeypatch.setattr("modules.memory.event_retrieval.get_event_retrieval", lambda: FakeRetrieval())
    out = await t._recall_memories("查询", "sess")
    assert "曾经发生的事" in out


async def test_thinker_recall_exception(monkeypatch):
    t = ContinuousThinker()
    t._blackboard = Blackboard()
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    assert await t._recall_memories("q", "s") == ""


async def test_thinker_extract_memory_disabled(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "MEMORY_REDUCE_ENABLED", False)
    t = ContinuousThinker()
    await t._extract_memory("s", [{"role": "user", "content": "hi"}])  # 直接返回


async def test_thinker_extract_memory_short(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "MEMORY_REDUCE_ENABLED", True)
    t = ContinuousThinker()
    await t._extract_memory("s", [{"role": "user", "content": "短"}])  # 长度不足直接返回
