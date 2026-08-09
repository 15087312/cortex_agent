"""chat_light/context_slicer 测试（此前 18% 覆盖）：消息窗口切片"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from modules.thinking.chat_light.context_slicer import ContextSlicer


def _slicer(window=3):
    s = ContextSlicer(window_size=window, chunk_chars=100)
    return s


def _msgs(n, prefix="m"):
    return [{"role": "user", "content": f"{prefix}{i}"} for i in range(n)]


def test_split_text_into_chunks():
    s = _slicer()
    chunks = s._split_text_into_chunks("a" * 250)
    assert len(chunks) == 3  # 100 字符每块
    assert all(len(c) <= 100 for c in chunks)


def test_slice_small_all_kept():
    s = _slicer()
    msgs = _msgs(2)
    out = asyncio.run(s.slice(msgs))
    assert len(out) == 2


def test_slice_within_window_all_kept():
    s = _slicer()
    msgs = _msgs(3)
    out = asyncio.run(s.slice(msgs))
    assert len(out) == 3


def test_slice_overflow_summarizes(monkeypatch):
    s = _slicer()
    s._summarize_overflow = AsyncMock(return_value="【历史总结】...")
    monkeypatch.setattr("modules.thinking.chat_light.context_slicer.settings", type("S", (), {"CHAT_CONTEXT_MAX_CHARS": 50})())
    msgs = _msgs(10, prefix="很长的消息内容很长的消息内容")
    out = asyncio.run(s.slice(msgs))
    # 超出上限部分被总结为一条
    assert any("历史总结" in str(m.get("content", "")) for m in out)
