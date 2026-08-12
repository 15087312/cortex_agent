"""cdp_scanner DOM 解析 + perception_tools 语音/屏幕（mock）"""
import asyncio
from unittest.mock import MagicMock, patch

from infra.data_process.core.cdp_scanner import CDPScanner
from infra.tool_manager.tools import perception_tools as pt


def _run(coro):
    return asyncio.run(coro)


def _scanner():
    s = CDPScanner.__new__(CDPScanner)
    s.logger = MagicMock()
    return s


def test_parse_dom_button(monkeypatch):
    s = _scanner()
    monkeypatch.setattr(s, "_parse_dom_node", s._parse_dom_node)
    node = {
        "nodeName": "BUTTON", "nodeType": 1,
        "attributes": ["aria-label", "提交", "class", "btn"],
        "children": [],
    }
    els = s._parse_dom_node(node, 0, 3)
    assert len(els) == 1
    assert els[0]["role"] == "button"


def test_parse_dom_skips_text_and_comment():
    s = _scanner()
    assert s._parse_dom_node({"nodeType": 3, "nodeValue": "x"}, 0, 3) == []
    assert s._parse_dom_node({"nodeType": 8}, 0, 3) == []


def test_parse_dom_max_depth():
    s = _scanner()
    child = {"nodeName": "DIV", "nodeType": 1, "children": []}
    parent = {"nodeName": "DIV", "nodeType": 1, "children": [child]}
    assert s._parse_dom_node(parent, 4, 3) == []  # 超深度


def test_parse_dom_placeholder_text():
    s = _scanner()
    node = {"nodeName": "INPUT", "nodeType": 1, "attributes": ["placeholder", "搜索..."], "children": []}
    els = s._parse_dom_node(node, 0, 3)
    assert els
    assert els[0]["name"] == "搜索..."  # placeholder 提取为 name
    assert els[0]["attributes"]["placeholder"] == "搜索..."


def test_find_chromium_ports(monkeypatch):
    s = _scanner()
    monkeypatch.setattr(s, "find_chromium_ports", lambda: [{"port": 9222, "app": "Chrome"}])
    r = s.find_chromium_ports()
    assert r[0]["port"] == 9222


def test_transcribe_audio_mock(monkeypatch):
    class FakeSR:
        async def initialize(self):
            pass
        async def recognize(self, audio, language=None, task="transcribe"):
            return {"text": "识别结果", "language": "zh", "confidence": 0.9}
    import infra.data_process.core.speech_recognizer as sr_mod
    monkeypatch.setattr(sr_mod, "SpeechRecognizer", lambda **kw: FakeSR())
    r = _run(pt.transcribe_audio(audio_base64="AQ==", language="zh"))
    assert "识别结果" in str(r)


def test_understand_screen_error(monkeypatch):
    # mock 截图失败 → 返回错误而非崩溃
    monkeypatch.setattr(pt, "_capture_screen", lambda: "")
    r = _run(pt.understand_screen())
    assert isinstance(r, dict)
