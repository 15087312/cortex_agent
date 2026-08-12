"""附件解析测试（图片走视觉、文本读内容、坏数据降级 + 直连多模态提取）"""
import asyncio
import base64
import io

import pytest

from modules.thinking.attachment_handler import (
    parse_attachments, extract_images, summarize_attachments,
)


def _run(coro):
    return asyncio.run(coro)


def _png_b64():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (5, 5), (0, 0, 0)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_text_attachment_content():
    b64 = base64.b64encode(b"hello world").decode()
    r = _run(parse_attachments([{"type": "text/plain", "name": "a.txt", "data": f"data:text/plain;base64,{b64}"}]))
    assert "hello world" in r
    assert "a.txt" in r


def test_empty_attachments():
    assert _run(parse_attachments([])) == ""
    assert _run(parse_attachments(None)) == ""
    assert extract_images([]) == []
    assert extract_images(None) == []
    assert summarize_attachments([]) == ""


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


# ── 直连多模态模式：extract_images / summarize_attachments ──

def test_extract_images_returns_dataurls():
    png = f"data:image/png;base64,{_png_b64()}"
    atts = [
        {"type": "image/png", "name": "a.png", "data": png},
        {"type": "text/plain", "name": "b.txt", "data": "data:text/plain;base64,aGVsbG8="},
        {"type": "image/jpeg", "name": "c.jpg", "data": "data:image/jpeg;base64,QUJD"},
    ]
    imgs = extract_images(atts)
    assert len(imgs) == 2
    assert imgs[0] == png
    assert imgs[1] == "data:image/jpeg;base64,QUJD"


def test_extract_images_skips_non_dict_and_empty():
    assert extract_images([None, "x", {"type": "image/png", "data": ""}, {"type": "application/zip", "data": "z"}]) == []


def test_summarize_attachments_image_marker_only(monkeypatch):
    """直连模式：图片只标注文件名，不调用视觉模型"""
    import infra.data_process.core.image_analyzer as ia
    calls = []

    class FakeAnalyzer:
        async def analyze(self, image_data, prompt=""):
            calls.append(1)
            return {"description": "不应被调用"}

    monkeypatch.setattr(ia, "ImageAnalyzer", lambda *a, **k: FakeAnalyzer())
    png = f"data:image/png;base64,{_png_b64()}"
    r = summarize_attachments([{"type": "image/png", "name": "t.png", "data": png}])
    assert "t.png" in r
    assert "图片内容" not in r
    assert calls == []  # 没有调用视觉模型


def test_summarize_attachments_text_content():
    b64 = base64.b64encode(b"file body").decode()
    r = summarize_attachments([{"type": "text/plain", "name": "a.txt", "data": f"data:text/plain;base64,{b64}"}])
    assert "file body" in r
