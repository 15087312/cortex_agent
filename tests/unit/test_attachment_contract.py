"""WS input.attachments 契约测试 — 前端载荷形状必须与后端 parse_attachments 期望一致。

历史事故：前端曾发送裸 base64 字符串数组（[{data:"..."}] 而非 [{type,name,data}]），
parse_attachments 对非 dict 元素静默 continue 导致图片被丢弃。此测试固定契约：
网关层 validate_attachments 拒绝非法形状，parse_attachments 只收到合法字典。
"""
import asyncio
import base64
import io

import pytest

from modules.thinking.attachment_handler import (
    ChatAttachment,
    parse_attachments,
    validate_attachments,
)


def _run(coro):
    return asyncio.run(coro)


def _png_b64() -> str:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (255, 0, 0)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ── ChatAttachment schema ─────────────────────────────────────────────

def test_chat_attachment_model():
    a = ChatAttachment(type="image/png", name="t.png", data="data:image/png;base64,AAA=")
    assert a.type == "image/png"
    assert a.name == "t.png"
    assert a.data == "data:image/png;base64,AAA="


def test_chat_attachment_defaults():
    a = ChatAttachment()
    assert a.type == ""
    assert a.name == ""
    assert a.data == ""


# ── validate_attachments：合法形状 ─────────────────────────────────────

def test_validate_ok_empty_and_none():
    assert validate_attachments(None) == ""
    assert validate_attachments([]) == ""


def test_validate_ok_valid_dict_list():
    atts = [{"type": "image/png", "name": "t.png", "data": _png_b64()},
            {"type": "text/plain", "name": "a.txt", "data": "data:text/plain;base64,aGk="}]
    assert validate_attachments(atts) == ""


# ── validate_attachments：非法形状（历史 bug 回归）──────────────────────

def test_validate_rejects_bare_string_list():
    """历史 bug：前端曾发 [base64str, ...] —— 必须被拒绝而非静默跳过"""
    err = validate_attachments(["data:image/png;base64,AAA="])
    assert "attachments[0]" in err


def test_validate_rejects_non_list():
    assert "数组" in validate_attachments({"data": "x"})


def test_validate_rejects_missing_data():
    err = validate_attachments([{"type": "image/png"}])
    assert "data" in err


def test_validate_rejects_empty_data():
    err = validate_attachments([{"type": "image/png", "data": ""}])
    assert "data" in err


def test_validate_rejects_non_string_data():
    err = validate_attachments([{"type": "image/png", "data": 123}])
    assert "data" in err


def test_validate_rejects_mixed_bad_element():
    """一个坏元素必须整体拒绝（不允许部分成功导致内容缺漏）"""
    err = validate_attachments([{"type": "image/png", "data": _png_b64()}, "junk"])
    assert "attachments[1]" in err


# ── 契约闭环：校验通过的数据 parse_attachments 必然能解析 ───────────────

def test_validated_input_parses_ok(monkeypatch):
    import infra.data_process.core.image_analyzer as ia

    class FakeAnalyzer:
        async def analyze(self, image_data, prompt=""):
            return {"description": "一张测试图片"}

    monkeypatch.setattr(ia, "ImageAnalyzer", lambda *a, **k: FakeAnalyzer())

    atts = [{"type": "image/png", "name": "t.png", "data": f"data:image/png;base64,{_png_b64()}"}]
    assert validate_attachments(atts) == ""
    r = _run(parse_attachments(atts))
    assert "一张测试图片" in r
    assert "t.png" in r
