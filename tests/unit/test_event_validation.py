"""event_validation 单测：结构校验、批内去重、失败隔离

覆盖约定：
- 校验只做截断/丢弃/去重，不改写已有事件（不引入 status/supersedes）。
- 单条不合格或落盘失败，不得影响同批其他事件。
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.memory.event_reducer import EventReducer
from modules.memory.event_store import EventStore, MemoryEvent
from modules.memory.event_validation import (
    MAX_FACT_CHARS,
    MAX_KEYWORDS,
    sanitize_batch,
    sanitize_event,
)


def _text(n=20):
    return "这是一段用于测试记忆提炼的对话内容。" * n


@pytest.fixture
def store(tmp_path):
    return EventStore(
        db_path=str(tmp_path / "ev.db"),
        faiss_index_path=str(tmp_path / "ev.faiss"),
        id_map_path=str(tmp_path / "ev.json"),
    )


# ── 单条校验 ────────────────────────────────────────────────


def test_rejects_empty_fact():
    result = sanitize_event(MemoryEvent(fact=""))
    assert result.event is None
    assert "过短" in result.reasons[0]


def test_rejects_whitespace_only_fact():
    result = sanitize_event(MemoryEvent(fact="    "))
    assert result.event is None


def test_truncates_overlong_fact_at_sentence_end():
    # 句读落在后半段（下标 180）时才优先在句读处收尾
    fact = "很长的内容" * 36 + "。" + "尾巴" * 20
    result = sanitize_event(MemoryEvent(fact=fact))
    assert result.event is not None
    assert len(result.event.fact) <= MAX_FACT_CHARS
    assert result.event.fact.endswith("。")
    assert any("fact 超长" in r for r in result.reasons)


def test_truncates_overlong_fact_without_punctuation():
    fact = "细" * 300
    result = sanitize_event(MemoryEvent(fact=fact))
    assert result.event is not None
    assert len(result.event.fact) <= MAX_FACT_CHARS
    assert result.event.fact.endswith("…")


def test_normalizes_unknown_type():
    result = sanitize_event(MemoryEvent(fact="正常事件", type="weird"))
    assert result.event is not None
    assert result.event.type == "fact"
    assert any("白名单" in r for r in result.reasons)


def test_clamps_importance_out_of_range():
    result = sanitize_event(MemoryEvent(fact="正常事件", importance=3.5))
    assert result.event is not None
    assert result.event.importance == 1.0
    assert any("越界" in r for r in result.reasons)


def test_drops_keywords_absent_from_source():
    result = sanitize_event(
        MemoryEvent(fact="改了记忆系统的检索", keywords=["记忆系统", "编造的关键词"]),
        conversation_text="我们改了记忆系统的检索，效果不错",
    )
    assert result.event is not None
    assert result.event.keywords == ["记忆系统"]
    assert any("未出现在原文" in r for r in result.reasons)


def test_keeps_keywords_without_source_text():
    """没有原文可比对时不做该核对（如降级摘要路径）。"""
    result = sanitize_event(MemoryEvent(fact="正常事件", keywords=["任意词"]))
    assert result.event is not None
    assert result.event.keywords == ["任意词"]


def test_caps_keyword_count():
    kws = [f"关键词{i}" for i in range(12)]
    text = " ".join(kws)
    result = sanitize_event(
        MemoryEvent(fact="正常事件", keywords=kws), conversation_text=text
    )
    assert result.event is not None
    assert len(result.event.keywords) == MAX_KEYWORDS


def test_sanitize_never_raises_on_garbage():
    """防御：校验本身不能成为新的失败源。"""
    weird = MemoryEvent(fact="正常事件")
    weird.keywords = "不是列表"  # type: ignore[assignment]
    result = sanitize_event(weird)
    assert result.event is not None  # 非法 keywords 视作空列表，不当成崩溃


# ── 批内去重 ────────────────────────────────────────────────


def test_batch_dedupes_same_fact():
    events = [
        MemoryEvent(fact="同一件事情"),
        MemoryEvent(fact="同一件事情"),  # 完全相同
        MemoryEvent(fact=" 同一件事情 "),  # 仅空白差异
        MemoryEvent(fact="另一件事情"),
    ]
    accepted, rejected = sanitize_batch(events)
    assert len(accepted) == 2
    assert len(rejected) == 2
    assert all("重复" in r.reasons[0] for r in rejected)


# ── 与 EventReducer 的集成：失败隔离 ────────────────────────


async def test_bad_event_does_not_block_others(store):
    client = MagicMock()
    client.generate = AsyncMock(return_value=json.dumps({
        "events": [
            {"fact": "第一条有效事件", "importance": "high"},
            {"fact": "   ", "importance": "medium"},  # 校验层应丢弃
            {"fact": "第三条有效事件", "importance": "low"},
        ],
    }))
    saved = await EventReducer(model_client=client, store=store).reduce("s1", _text())
    assert len(saved) == 2
    assert store.count_events() == 2


async def test_save_failure_does_not_kill_batch(store, monkeypatch):
    """单条落盘抛异常时，其余事件仍应保存。"""
    client = MagicMock()
    client.generate = AsyncMock(return_value=json.dumps({
        "events": [
            {"fact": "会成功的第一条", "importance": "high"},
            {"fact": "会失败的第二条", "importance": "medium"},
            {"fact": "会成功的第三条", "importance": "low"},
        ],
    }))

    real_save = store.save_event
    calls = {"n": 0}

    def flaky(event):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("database is locked")
        return real_save(event)

    monkeypatch.setattr(store, "save_event", flaky)

    saved = await EventReducer(model_client=client, store=store).reduce("s1", _text())

    assert len(saved) == 2
    assert [e.fact for e in saved] == ["会成功的第一条", "会成功的第三条"]
    assert store.count_events() == 2


async def test_overlong_fact_stored_truncated(store):
    client = MagicMock()
    long_fact = "背景说明。" + "细节" * 200
    client.generate = AsyncMock(return_value=json.dumps({
        "events": [{"fact": long_fact, "importance": "medium"}],
    }))
    saved = await EventReducer(model_client=client, store=store).reduce("s1", _text())
    assert len(saved) == 1
    assert len(saved[0].fact) <= MAX_FACT_CHARS
