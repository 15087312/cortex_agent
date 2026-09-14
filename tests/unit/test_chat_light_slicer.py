"""chat_light/context_slicer 测试（此前 18% 覆盖）：消息窗口切片"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from modules.thinking.chat_light.context_slicer import ContextSlicer


def _slicer(window=3):
    s = ContextSlicer(window_size=window, chunk_chars=100)
    return s


def _msgs(n, prefix="m"):
    return [{"role": "user", "content": f"{prefix}{i}"} for i in range(n)]


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

