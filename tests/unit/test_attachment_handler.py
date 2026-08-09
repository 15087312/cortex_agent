"""附件解析测试（图片走视觉、文本读内容、坏数据降级）"""
import asyncio
import base64
import io

import pytest

from modules.thinking.attachment_handler import parse_attachments


def _run(coro):
    return asyncio.run(coro)


def test_text_attachment_content():
    b64 = base64.b64encode(b"hello world").decode()
    r = _run(parse_attachments([{"type": "text/plain", "name": "a.txt", "data": f"data:text/plain;base64,{b64}"}]))
    assert "hello world" in r
    assert "a.txt" in r


def test_empty_attachments():
    assert _run(parse_attachments([])) == ""
    assert _run(parse_attachments(None)) == ""


def test_bad_image_no_crash():
    r = _run(parse_attachments([{"type": "image/png", "data": "not-base64"}]))
    assert "图片" in r  # 降级标注，不崩溃


def test_image_uses_vision(monkeypatch):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (5, 5), (0, 0, 0)).save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    import infra.data_process.core.image_analyzer as ia

    class FakeAnalyzer:
        async def analyze(self, image_data, prompt=""):
            return {"description": "一张测试图片"}

    monkeypatch.setattr(ia, "ImageAnalyzer", lambda *a, **k: FakeAnalyzer())
    r = _run(parse_attachments([{"type": "image/png", "name": "t.png", "data": f"data:image/png;base64,{b64}"}]))
    assert "一张测试图片" in r
    assert "t.png" in r
