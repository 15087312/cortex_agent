"""chat_light 扩展测试：model_runner / context_slicer / continuous_thinker

注意：Blackboard 已删除（DB 为唯一真源），相关测试随之移除；
ContextSlicer 的 LLM 摘要能力已移除，改为公共层 token 预算截断。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.thinking.chat_light.model_runner import ModelRunner
from modules.thinking.chat_light.context_slicer import ContextSlicer
from modules.thinking.chat_light.continuous_thinker import ContinuousThinker
from modules.thinking.chat_light.prompt_composer import PromptComposer


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
    # client property 函数内 import：patch 源模块属性即可 mock 构造
    with patch("infra.model.large_model_client.LargeModelClient") as lm:
        c = mr.client
        assert c is lm.return_value
        assert mr.client is c  # 缓存


# ── ContextSlicer ──────────────────────────────────────────────────────

def _msgs(n):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"内容{i}"} for i in range(n)]


async def test_slicer_db_first(monkeypatch):
    """slice 以 DB 为唯一真源：给出 session_id 时直读 DB，忽略传入 messages"""
    s = ContextSlicer()
    # mock 已导入到 slicer 模块的 load_dialog_from_db，返回 DB 会话
    monkeypatch.setattr(
        "modules.thinking.chat_light.context_slicer.load_dialog_from_db",
        lambda sid, limit=100: [{"role": "user", "content": "DB历史"}],
    )
    out = await s.slice(_msgs(5), memory_context="", session_id="s1")
    # DB 内容被采用，而非传入的 5 条
    assert any(m.get("content") == "DB历史" for m in out)


async def test_slicer_session_id_missing_fallback_to_args():
    """无 session_id 时回退到传入 messages（兼容无 repo 场景）"""
    s = ContextSlicer()
    out = await s.slice(_msgs(3), memory_context="")
    assert len(out) == 3


async def test_slicer_memory_prepends_system(monkeypatch):
    """记忆上下文作为 system 消息前置"""
    s = ContextSlicer()
    monkeypatch.setattr(
        "modules.thinking.context.dialog_memory.load_dialog_from_db",
        lambda sid, limit=100: [],
    )
    out = await s.slice([], memory_context="回忆内容", session_id="s1")
    assert out and out[0]["role"] == "system"
    assert "回忆内容" in out[0]["content"]


async def test_slicer_budget_trim_applied(monkeypatch):
    """超 token 预算的旧消息被截断（不注入），仅保留最新"""
    s = ContextSlicer()
    monkeypatch.setattr(
        "modules.thinking.chat_light.context_slicer.load_dialog_from_db",
        lambda sid, limit=100: _msgs(20),
    )
    # 强制窗口极小 → 只留最新 1 条
    monkeypatch.setattr(
        "modules.thinking.chat_light.context_slicer.budget_trim",
        lambda msgs, ratio=0.8, window_size=None: [msgs[-1]],
    )
    out = await s.slice([], memory_context="", session_id="s1")
    # 除记忆 system 外只有最新 1 条
    real = [m for m in out if m["role"] != "system"]
    assert len(real) == 1
    assert real[0]["content"] == "内容19"


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
    repo = MagicMock()
    repo.get_recent_messages = MagicMock(return_value=[])
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: repo)
    assert await t._recall_memories("q", "sess") == ""


async def test_thinker_recall_with_db_history(monkeypatch):
    t = ContinuousThinker()
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
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    assert await t._recall_memories("q", "s") == ""


async def test_thinker_extract_memory_disabled(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "MEMORY_REDUCE_ENABLED", False)
    t = ContinuousThinker()
    await t._extract_memory("s")  # 开关关闭 → 直接返回，不读 DB


async def test_thinker_extract_memory_short(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "MEMORY_REDUCE_ENABLED", True)
    t = ContinuousThinker()
    # mock DB 返回过短对话，验证门控
    repo = MagicMock()
    repo.get_messages = MagicMock(return_value=[{"role": "user", "content": "短"}])
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: repo)
    await t._extract_memory("s")  # 长度不足直接返回
