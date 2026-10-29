"""纯对话知识库自动注入（M6）— 意图门控、异常兜底、与事件记忆合并"""
import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

from modules.thinking.chat_light.continuous_thinker import ContinuousThinker


def _thinker():
    t = ContinuousThinker.__new__(ContinuousThinker)
    t._session_locks = {}
    t._session_locks_guard = threading.Lock()
    t._runner = MagicMock()
    t._slicer = MagicMock()
    t._composer = MagicMock()
    return t


def _patch_knowledge(monkeypatch, *, should: bool = True, text: str = "", error=None):
    """替换意图判定与知识库门面，返回 stub service 便于断言调用。"""
    import modules.knowledge.retrieval as ret_mod
    import modules.knowledge.service as svc_mod

    monkeypatch.setattr(ret_mod, "should_retrieve", lambda q: should)
    svc = MagicMock()
    if error is not None:
        svc.search_text = MagicMock(side_effect=error)
    else:
        svc.search_text = MagicMock(return_value=text)
    monkeypatch.setattr(svc_mod, "get_knowledge_service", lambda: svc)
    return svc


def _patch_think_env(monkeypatch, t, response_text="回复"):
    """关闭 conscience / 感知等外部依赖，保留待测的记忆合并逻辑。"""
    monkeypatch.setattr(
        "modules.thinking.conscience.get_conscience",
        lambda: (_ for _ in ()).throw(RuntimeError("cons down")),
    )
    monkeypatch.setattr(
        "modules.thinking.context.sources.perception_source.PerceptionSource.collect",
        AsyncMock(return_value=None),
    )
    t._slicer.slice = AsyncMock(return_value=[])
    t._extract_memory = AsyncMock()
    response = MagicMock()
    response.message.reasoning_content = ""
    response.message.content = response_text
    t._runner.run = AsyncMock(return_value=response)


# ── 意图门控 ─────────────────────────────────────────────────────────────

def test_recall_knowledge_skips_non_knowledge_query(monkeypatch):
    svc = _patch_knowledge(monkeypatch, should=False)
    assert _thinker()._recall_knowledge("今天午饭吃什么") == ""
    svc.search_text.assert_not_called()


def test_recall_knowledge_returns_reference_block(monkeypatch):
    _patch_knowledge(monkeypatch, text="  【参考资料】（来自知识库）\n[1] 《鉴权设计》§1  ")
    out = _thinker()._recall_knowledge("这个接口的参数是什么")
    assert out.startswith("【参考资料】")
    assert out.endswith("《鉴权设计》§1")


def test_recall_knowledge_empty_when_no_hits(monkeypatch):
    _patch_knowledge(monkeypatch, text="")
    assert _thinker()._recall_knowledge("文档里怎么写的") == ""


def test_recall_knowledge_swallows_errors(monkeypatch):
    _patch_knowledge(monkeypatch, error=RuntimeError("kb down"))
    assert _thinker()._recall_knowledge("文档里怎么写的") == ""


def test_recall_knowledge_swallows_intent_errors(monkeypatch):
    import modules.knowledge.retrieval as ret_mod

    monkeypatch.setattr(
        ret_mod, "should_retrieve",
        lambda q: (_ for _ in ()).throw(RuntimeError("intent down")),
    )
    assert _thinker()._recall_knowledge("文档里怎么写的") == ""


# ── 与事件记忆合并注入 ───────────────────────────────────────────────────

async def test_think_injects_knowledge_on_first_message(monkeypatch):
    """新会话第一条消息没有事件记忆，知识库也必须注入（不依赖会话历史）。"""
    t = _thinker()
    t._recall_memories = AsyncMock(return_value="")
    t._recall_knowledge = MagicMock(return_value="【参考资料】（来自知识库）\n[1] 《鉴权设计》")
    captured = {}
    t._composer.build_system = MagicMock(
        side_effect=lambda mc: captured.setdefault("mc", mc) or "sys"
    )
    _patch_think_env(monkeypatch, t)

    await t.think("s1", "接口参数是什么", asyncio.Queue())

    assert "【参考资料】" in captured["mc"]


async def test_think_merges_event_memory_before_knowledge(monkeypatch):
    t = _thinker()
    t._recall_memories = AsyncMock(return_value="【曾经发生的事】上个月定过鉴权方案")
    t._recall_knowledge = MagicMock(return_value="【参考资料】（来自知识库）\n[1] 《鉴权设计》")
    captured = {}

    def _build(mc):
        captured["mc"] = mc
        return "sys"

    t._composer.build_system = MagicMock(side_effect=_build)
    _patch_think_env(monkeypatch, t)

    await t.think("s1", "鉴权文档里怎么写的", asyncio.Queue())

    text = captured["mc"]
    assert "曾经发生的事" in text
    assert "【参考资料】" in text
    # 两类记忆分开成段，避免模型把文档当成自己的经历
    assert text.index("曾经发生的事") < text.index("【参考资料】")
