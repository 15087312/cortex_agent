"""perception_tools 测试（此前 23% 覆盖）：transcribe_audio / understand_screen / detect_ui_elements

所有系统边界（截图、语音识别、视觉模型、无障碍检测）均 mock，
仅验证工具层纯逻辑。
"""
import base64
import subprocess
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import infra.data_process.core.image_analyzer as image_analyzer_mod
import infra.data_process.core.speech_recognizer as speech_recognizer_mod
import utils.screen_capture
from infra.tool_manager.service_registry import (
    get_capability,
    register_capability,
    unregister_capability,
)
from infra.tool_manager.tools import perception_tools


# ── transcribe_audio ─────────────────────────────────────────────────────────

class FakeRecognizer:
    def __init__(self, model_name="tiny", language="auto"):
        self.model_name = model_name
        self.language = language
        self.initialize = AsyncMock()
        self.recognize = AsyncMock(return_value={"text": "你好", "language": "zh", "duration": 1.2})
        self.recognize_file = AsyncMock(return_value={"text": "file ok", "language": "en", "duration": 2.0})


@pytest.fixture
def recognizer(monkeypatch):
    fake = FakeRecognizer()
    monkeypatch.setattr(
        speech_recognizer_mod,
        "SpeechRecognizer",
        lambda model_name="tiny", language="auto": fake,
    )
    return fake


async def test_transcribe_by_file(recognizer, tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(b"fake")
    r = await perception_tools.transcribe_audio(file_path=str(p))
    assert r["success"] is True
    assert r["text"] == "file ok"
    assert r["language"] == "en"
    assert r["duration"] == 2.0
    recognizer.recognize_file.assert_awaited_once_with(str(p), language=None)


async def test_transcribe_by_file_with_language(recognizer, tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(b"fake")
    await perception_tools.transcribe_audio(file_path=str(p), language="ja")
    recognizer.recognize_file.assert_awaited_once_with(str(p), language="ja")


async def test_transcribe_file_not_found(recognizer):
    r = await perception_tools.transcribe_audio(file_path="/no/such/file.wav")
    assert r == {"error": "文件不存在: /no/such/file.wav"}
    recognizer.recognize_file.assert_not_awaited()


async def test_transcribe_by_base64(recognizer):
    data = b"audio-bytes"
    r = await perception_tools.transcribe_audio(audio_base64=base64.b64encode(data).decode())
    assert r["success"] is True
    assert r["text"] == "你好"
    recognizer.recognize.assert_awaited_once_with(data, language=None)


async def test_transcribe_by_base64_with_language(recognizer):
    data = b"x"
    await perception_tools.transcribe_audio(audio_base64=base64.b64encode(data).decode(), language="ja")
    recognizer.recognize.assert_awaited_once_with(data, language="ja")


async def test_transcribe_no_input(recognizer):
    r = await perception_tools.transcribe_audio()
    assert r == {"error": "请提供 audio_base64 或 file_path"}
    recognizer.recognize.assert_not_awaited()


async def test_transcribe_result_defaults(recognizer):
    recognizer.recognize.return_value = {}
    r = await perception_tools.transcribe_audio(audio_base64="eA==")
    assert r == {"success": True, "text": "", "language": "", "duration": 0}


async def test_transcribe_exception(recognizer):
    recognizer.recognize.side_effect = RuntimeError("boom")
    r = await perception_tools.transcribe_audio(audio_base64="eA==")
    assert "语音识别失败" in r["error"]


# ── understand_screen ────────────────────────────────────────────────────────

async def test_understand_screen_capture_failed(monkeypatch):
    monkeypatch.setattr(perception_tools, "_capture_screen", lambda: "")
    r = await perception_tools.understand_screen()
    assert r == {"error": "截图失败：无可用的屏幕捕获方式"}


async def test_understand_screen_vision_error(monkeypatch):
    monkeypatch.setattr(perception_tools, "_capture_screen", lambda: "b64")
    monkeypatch.setattr(perception_tools, "_get_active_window", lambda: "PyCharm")

    async def vision(*args):
        return {"error": "模型不可用"}

    monkeypatch.setattr(perception_tools, "_vision_understand", vision)
    r = await perception_tools.understand_screen("看错误信息")
    assert r == {"success": False, "error": "模型不可用", "window": "PyCharm"}


async def test_understand_screen_success(monkeypatch):
    monkeypatch.setattr(perception_tools, "_capture_screen", lambda: "b64")
    monkeypatch.setattr(perception_tools, "_get_active_window", lambda: "Safari")

    async def vision(*args):
        return {"understanding": "显示登录界面", "method": "mlx_vlm"}

    monkeypatch.setattr(perception_tools, "_vision_understand", vision)
    r = await perception_tools.understand_screen()
    assert r["success"] is True
    assert r["window"] == "Safari"
    assert r["understanding"] == "显示登录界面"
    assert r["method"] == "mlx_vlm"


async def test_understand_screen_exception(monkeypatch):
    monkeypatch.setattr(perception_tools, "_capture_screen", lambda: "b64")

    def boom():
        raise RuntimeError("window error")

    monkeypatch.setattr(perception_tools, "_get_active_window", boom)
    r = await perception_tools.understand_screen()
    assert "屏幕理解失败" in r["error"]


# ── _capture_screen ──────────────────────────────────────────────────────────

def test_capture_screen_ok(monkeypatch):
    monkeypatch.setattr(utils.screen_capture, "capture_screen", lambda: "b64data")
    assert perception_tools._capture_screen() == "b64data"


def test_capture_screen_empty(monkeypatch):
    monkeypatch.setattr(utils.screen_capture, "capture_screen", lambda: None)
    assert perception_tools._capture_screen() == ""


# ── _get_active_window（平台分支用 monkeypatch sys.platform 模拟）────────────

def _run_result(returncode=0, stdout=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout)


def test_active_window_darwin_success(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _run_result(0, "Chrome\n"))
    assert perception_tools._get_active_window() == "Chrome"


def test_active_window_darwin_fail_rc(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _run_result(1, ""))
    assert perception_tools._get_active_window() == "未知"


def test_active_window_darwin_exception(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")

    def run(*a, **k):
        raise OSError("no osascript")

    monkeypatch.setattr(subprocess, "run", run)
    assert perception_tools._get_active_window() == "未知"


def _fake_ctypes_module(title="PyCharm", length=7):
    mod = types.ModuleType("ctypes")

    class _Buf:
        def __init__(self, size):
            self.size = size
            self.value = ""

    mod.create_unicode_buffer = lambda size: _Buf(size)

    class _User32:
        def GetForegroundWindow(self):
            return 0x1

        def GetWindowTextLengthW(self, hwnd):
            return length

        def GetWindowTextW(self, hwnd, buf, n):
            buf.value = title
            return n

    mod.windll = types.SimpleNamespace(user32=_User32())
    return mod


def test_active_window_win32_success(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "ctypes", _fake_ctypes_module(title="Notepad"))
    assert perception_tools._get_active_window() == "Notepad"


def test_active_window_win32_empty_title(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "ctypes", _fake_ctypes_module(length=0))
    assert perception_tools._get_active_window() == "未知"


def test_active_window_win32_exception(monkeypatch):
    mod = types.ModuleType("ctypes")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "ctypes", mod)
    assert perception_tools._get_active_window() == "未知"


def test_active_window_linux_success(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _run_result(0, "Terminal\n"))
    assert perception_tools._get_active_window() == "Terminal"


def test_active_window_linux_fail(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _run_result(1, ""))
    assert perception_tools._get_active_window() == "未知"


def test_active_window_linux_exception(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    def run(*a, **k):
        raise FileNotFoundError("xdotool missing")

    monkeypatch.setattr(subprocess, "run", run)
    assert perception_tools._get_active_window() == "未知"


def test_active_window_unknown_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "plan9")
    assert perception_tools._get_active_window() == "未知"


# ── _vision_understand ───────────────────────────────────────────────────────

def _patch_analyzer(monkeypatch, result=None, exc=None):
    analyzer = MagicMock()
    analyzer.model_type = "mlx_vlm"
    if result is not None:
        analyzer.analyze = AsyncMock(return_value=result)

    async def get_analyzer():
        if exc is not None:
            raise exc
        return analyzer

    monkeypatch.setattr(image_analyzer_mod, "get_default_analyzer", get_analyzer)
    return analyzer


async def test_vision_understand_success(monkeypatch):
    _patch_analyzer(monkeypatch, result={"description": "桌面图标"})
    r = await perception_tools._vision_understand("aGVsbG8=", "PyCharm", "看图标")
    assert r == {"understanding": "桌面图标", "method": "mlx_vlm"}


async def test_vision_understand_error_result(monkeypatch):
    _patch_analyzer(monkeypatch, result={"error": "后端不可用"})
    r = await perception_tools._vision_understand("aGVsbG8=", "", "")
    assert r == {"error": "后端不可用"}


async def test_vision_understand_empty_content(monkeypatch):
    _patch_analyzer(monkeypatch, result={})
    r = await perception_tools._vision_understand("aGVsbG8=", "", "")
    assert r == {"error": "视觉理解返回空内容"}


async def test_vision_understand_exception(monkeypatch):
    _patch_analyzer(monkeypatch, exc=RuntimeError("model load fail"))
    r = await perception_tools._vision_understand("aGVsbG8=", "", "")
    assert r == {"error": "视觉理解失败: model load fail"}


# ── detect_ui_elements ───────────────────────────────────────────────────────

def _elem(eid, etype, label, cx=1, cy=2):
    return SimpleNamespace(
        element_id=eid, type=etype, label=label,
        bbox=[0, 0, 10, 10], center_x=cx, center_y=cy, actions=["click"],
    )


def _make_ctx(elements, visual_description="", backend="touchpoint"):
    return SimpleNamespace(
        app_name="PyCharm", depth=3, role_summary={"button": 1},
        elapsed_ms=12.6, backend=backend,
        visual_description=visual_description, elements=elements,
    )


@pytest.fixture
def fake_detector():
    prev = get_capability("detector_router")
    router = MagicMock()
    register_capability("detector_router", lambda: router)
    yield router
    if prev is None:
        unregister_capability("detector_router")
    else:
        register_capability("detector_router", prev)


def test_detect_ui_factory_missing():
    prev = get_capability("detector_router")
    unregister_capability("detector_router")
    try:
        r = perception_tools.detect_ui_elements()
    finally:
        if prev is not None:
            register_capability("detector_router", prev)
    assert r == {"success": False, "error": "感知服务未注册", "elements": []}


def test_detect_ui_success_all(fake_detector):
    ctx = _make_ctx(
        [_elem("b1", "button", "确定"), _elem("t1", "text", "标题")],
        visual_description="x" * 10,
    )
    fake_detector.detect.return_value = ctx
    r = perception_tools.detect_ui_elements(depth=2, app="Safari")
    fake_detector.detect.assert_called_once_with(app="Safari", depth=2)
    assert r["success"] is True
    assert r["count"] == 2
    assert r["backend"] == "touchpoint"
    assert r["elapsed_ms"] == 13
    assert r["app"] == "PyCharm"
    assert r["depth"] == 3
    assert r["role_summary"] == {"button": 1}
    assert r["hint"]
    assert r["elements"][0]["element_id"] == "b1"
    assert r["elements"][0]["actions"] == ["click"]


def test_detect_ui_role_filter_case_insensitive(fake_detector):
    ctx = _make_ctx([_elem("b1", "button", "确定"), _elem("t1", "text", "标题")])
    fake_detector.detect.return_value = ctx
    r = perception_tools.detect_ui_elements(role_filter="BUTTON")
    assert r["count"] == 1
    assert r["elements"][0]["element_id"] == "b1"


def test_detect_ui_role_filter_no_match(fake_detector):
    ctx = _make_ctx([_elem("b1", "button", "确定")])
    fake_detector.detect.return_value = ctx
    r = perception_tools.detect_ui_elements(role_filter="text_field")
    assert r["count"] == 0
    assert r["elements"] == []


def test_detect_ui_visual_description_truncated(fake_detector):
    ctx = _make_ctx([], visual_description="v" * 500)
    fake_detector.detect.return_value = ctx
    r = perception_tools.detect_ui_elements()
    assert r["visual_description"] == "v" * 300


def test_detect_ui_visual_description_empty(fake_detector):
    ctx = _make_ctx([])
    fake_detector.detect.return_value = ctx
    r = perception_tools.detect_ui_elements()
    assert r["visual_description"] == ""


def test_detect_ui_exception(fake_detector):
    fake_detector.detect.side_effect = RuntimeError("touchpoint down")
    r = perception_tools.detect_ui_elements()
    assert r["success"] is False
    assert "touchpoint down" in r["error"]
