"""chat_light/context_slicer 补充测试：LLM 总结、裁剪边界、客户端复用"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import modules.thinking.chat_light.context_slicer as slicer_mod
from modules.thinking.chat_light.context_slicer import ContextSlicer


def _msgs(n, prefix="m"):
    return [{"role": "user", "content": f"{prefix}{i}"} for i in range(n)]


def _patch_max_chars(monkeypatch, v):
    monkeypatch.setattr(
        slicer_mod, "settings", type("S", (), {"CHAT_CONTEXT_MAX_CHARS": v})()
    )


@pytest.fixture
def sem(monkeypatch):
    """每个 async 测试用独立事件循环，替换模块级 Semaphore 防跨 loop 绑定报错"""
    s = asyncio.Semaphore(2)
    monkeypatch.setattr(slicer_mod, "_SUMMARIZE_SEM", s)
    return s


def test_get_client_lazy_and_reuse(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr("infra.model.large_model_client.LargeModelClient", lambda: fake)
    s = ContextSlicer()
    assert s._client is None
    assert s._get_client() is fake
    assert s._get_client() is fake  # 复用，不重建


def test_slice_explicit_max_chars(monkeypatch):
    s = ContextSlicer(window_size=2, chunk_chars=100)
    s._summarize_overflow = AsyncMock(return_value="")
    out = asyncio.run(s.slice(_msgs(10, "很长很长的消息"), max_chars=10))
    assert len(out) >= 2


def test_slice_partial_history_kept(monkeypatch):
    """部分历史在 max_chars 内被保留（kept.insert 分支）"""
    s = ContextSlicer(window_size=2, chunk_chars=100)
    s._summarize_overflow = AsyncMock(return_value="")
    _patch_max_chars(monkeypatch, 100)
    msgs = [{"role": "user", "content": "x" * 30} for _ in range(5)]
    out = asyncio.run(s.slice(msgs))
    assert len(out) == 4  # 1 fallback 摘要 + 3 保留（窗口 2 + 历史 1）


def test_slice_window_exceeds_max(monkeypatch):
    """窗口本身超上限 → 从窗口最旧开始总结"""
    s = ContextSlicer(window_size=3, chunk_chars=100)
    s._summarize_overflow = AsyncMock(return_value="总结")
    _patch_max_chars(monkeypatch, 10)
    msgs = [{"role": "user", "content": "x" * 6} for _ in range(4)]
    out = asyncio.run(s.slice(msgs))
    assert any("总结" in str(m.get("content", "")) for m in out)
    s._summarize_overflow.assert_awaited_once()


def test_slice_summary_fallback(monkeypatch):
    """总结失败 → 降级保留每条约首 30 字"""
    s = ContextSlicer(window_size=2, chunk_chars=100)
    s._summarize_overflow = AsyncMock(return_value="")
    _patch_max_chars(monkeypatch, 50)
    msgs = _msgs(8, "很长的消息内容很长的消息内容")
    out = asyncio.run(s.slice(msgs))
    assert any("[user]:" in str(m.get("content", "")) for m in out)


def test_slice_with_memory_context(monkeypatch):
    s = ContextSlicer(window_size=3)
    _patch_max_chars(monkeypatch, 1000)
    msgs = _msgs(3)
    out = asyncio.run(s.slice(msgs, memory_context="记忆内容"))
    assert out[0]["role"] == "system"
    assert "记忆内容" in out[0]["content"]
    assert len(out) == 4


def test_slice_blank_memory_context_skipped(monkeypatch):
    s = ContextSlicer(window_size=3)
    _patch_max_chars(monkeypatch, 1000)
    msgs = _msgs(3)
    out = asyncio.run(s.slice(msgs, memory_context="   "))
    assert not any(m.get("role") == "system" for m in out)


async def test_summarize_overflow_empty(sem):
    s = ContextSlicer()
    assert await s._summarize_overflow([]) == ""


async def test_summarize_overflow_single_chunk(sem):
    s = ContextSlicer(chunk_chars=1000)
    s._summarize_chunk = AsyncMock(return_value="摘要a")
    out = await s._summarize_overflow([{"role": "user", "content": "hi"}])
    assert out == "摘要a"
    s._summarize_chunk.assert_awaited_once()


async def test_summarize_overflow_multiple_chunks(sem):
    s = ContextSlicer(chunk_chars=100)
    s._summarize_chunk = AsyncMock(side_effect=["s1", "s2", "s3"])
    msgs = [{"role": "user", "content": "x" * 80} for _ in range(3)]
    out = await s._summarize_overflow(msgs)
    assert out == "s1；s2；s3"


async def test_summarize_overflow_partial_exceptions(sem):
    """部分块失败被过滤，成功块仍并合"""
    s = ContextSlicer(chunk_chars=100)
    s._summarize_chunk = AsyncMock(side_effect=[RuntimeError("llm"), "ok1", "ok2"])
    msgs = [{"role": "user", "content": "x" * 80} for _ in range(3)]
    out = await s._summarize_overflow(msgs)
    assert out == "ok1；ok2"


def test_split_text_into_chunks_sentence_boundary():
    s = ContextSlicer(chunk_chars=100)
    text = "a" * 90 + "\n" + "b" * 100 + "c" * 100
    chunks = s._split_text_into_chunks(text)
    assert chunks == ["a" * 90, "b" * 100, "c" * 100]


def test_split_text_into_chunks_skips_empty():
    s = ContextSlicer(chunk_chars=100)
    text = "a" * 100 + "\n\n\n"
    chunks = s._split_text_into_chunks(text)
    assert chunks == ["a" * 100]


async def test_summarize_chunk_blank(sem):
    s = ContextSlicer()
    assert await s._summarize_chunk("   ") == ""


async def test_summarize_chunk_success(sem, monkeypatch):
    s = ContextSlicer()
    client = MagicMock()
    client.generate = AsyncMock(return_value="摘要：核心结论")
    monkeypatch.setattr(s, "_get_client", lambda: client)
    out = await s._summarize_chunk("对话内容")
    assert out == "核心结论"
    args, kw = client.generate.await_args
    assert "对话内容" in args[0]
    assert kw["max_tokens"] == 120
    assert kw["temperature"] == 0.3


async def test_summarize_chunk_no_prefix(sem, monkeypatch):
    """摘要不以『摘要：』开头 → 原样返回"""
    s = ContextSlicer()
    client = MagicMock()
    client.generate = AsyncMock(return_value="  直接结论  ")
    monkeypatch.setattr(s, "_get_client", lambda: client)
    out = await s._summarize_chunk("对话")
    assert out == "直接结论"


async def test_summarize_chunk_failure(sem, monkeypatch):
    s = ContextSlicer()
    client = MagicMock()
    client.generate = AsyncMock(side_effect=RuntimeError("llm down"))
    monkeypatch.setattr(s, "_get_client", lambda: client)
    out = await s._summarize_chunk("多行\n对话")
    assert out == "多行 对话..."
