"""vision_backend + screen_monitor_source 测试（此前 16%/17% 覆盖）"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from modules.perception.screen.vision_backend import VisionBackend
from modules.perception.difference.sources.screen_monitor_source import ScreenMonitorSource
from modules.perception.screen.context import UIElement


# ── vision_backend ──

def test_vision_is_available_true(monkeypatch):
    import sys
    v = VisionBackend()
    monkeypatch.setitem(sys.modules, "infra", MagicMock())
    assert isinstance(v.is_available(), bool)


def test_vision_parse_elements():
    v = VisionBackend()
    desc = '按钮："提交"\n输入框: 用户名\nbutton: 登录\n按钮："提交"'
    els = v._parse_elements_from_description(desc)
    types = [e.type for e in els]
    assert "button" in types
    assert "text_field" in types
    # 去重后按钮只有一个
    assert sum(1 for t in types if t == "button") == 2  # 提交 + 登录


def test_vision_parse_empty():
    v = VisionBackend()
    assert v._parse_elements_from_description("") == []


def test_vision_detect_no_screenshot(monkeypatch):
    import utils.screen_capture as sc_mod
    v = VisionBackend()
    monkeypatch.setattr(sc_mod, "capture_screen", lambda: None)
    result = asyncio.run(v.detect(app="Test"))
    assert result.backend == "vision"
    assert result.element_count == 0


def test_vision_detect_full(monkeypatch):
    import utils.screen_capture as sc_mod
    import base64
    v = VisionBackend()
    monkeypatch.setattr(sc_mod, "capture_screen", lambda: base64.b64encode(b"raw").decode())
    import infra.data_process.core.image_analyzer as ia_mod
    analyzer = MagicMock()
    analyzer.initialize = AsyncMock(return_value=None)
    async def fake_analyze(image_data, prompt=None):
        return {"description": '按钮："确定" 输入框: 搜索'}
    analyzer.analyze = fake_analyze
    monkeypatch.setattr(ia_mod, "ImageAnalyzer", lambda **kw: analyzer)
    result = asyncio.run(v.detect(app="Test"))
    assert result.element_count >= 1
    assert result.visual_description


# ── screen_monitor_source ──

def _source(monkeypatch):
    s = ScreenMonitorSource.__new__(ScreenMonitorSource)
    s._interval = 5.0
    s._running = False
    s._thread = None
    s._last_text_lines = []
    s._proc = None
    s._lock = __import__("threading").RLock()
    s._reader_running = False
    s._reader_thread = None
    s._resp_queue = __import__("queue").Queue()
    s._proc_restarts = 0
    s._consecutive_timeouts = 0
    return s


def test_find_server_script(tmp_path, monkeypatch):
    import os
    p = ScreenMonitorSource._find_server_script()
    assert p.endswith("screen_monitor_server.py")
    assert os.path.basename(os.path.dirname(p)) == "servers"


def test_start_stop(monkeypatch):
    s = _source(monkeypatch)
    s._ensure_process = MagicMock(return_value=True)
    s._run_loop = MagicMock()
    s._close_process = MagicMock()
    fake_thread = MagicMock()
    import modules.perception.difference.sources.screen_monitor_source as mod
    monkeypatch.setattr(mod.threading, "Thread", lambda **k: fake_thread)
    s.start()
    assert s._running is True
    s.start()  # 幂等
    fake_thread.start.assert_called_once()
    s.stop()
    assert s._running is False
    fake_thread.join.assert_called_once()


def test_ensure_process_script_missing(monkeypatch):
    s = _source(monkeypatch)
    s._server_script = "/nonexistent/server.py"
    import os
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    assert s._ensure_process() is False


def test_ensure_process_script_exists(monkeypatch):
    s = _source(monkeypatch)
    s._server_script = "/tmp/sms.py"
    import os
    import subprocess
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    proc = MagicMock()
    proc.poll.return_value = None
    proc.stdin = MagicMock()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    s._send_request = MagicMock()
    s._read_response = MagicMock(return_value={"result": {}})
    s._close_process = MagicMock()
    assert s._ensure_process() is True


def test_close_process_and_send(monkeypatch):
    s = _source(monkeypatch)
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.poll.return_value = 0
    s._proc = proc
    s._send_request({"jsonrpc": "2.0", "id": "1", "method": "initialize"})
    proc.stdin.write.assert_called_once()
    s._close_process()
    assert s._proc is None


def test_call_mcp_tool_text(monkeypatch):
    s = _source(monkeypatch)
    s._send_request = MagicMock()
    s._read_response = MagicMock(return_value={
        "result": {"content": [{"type": "text", "text": "hello"}]},
    })
    out = s._call_mcp_tool("get_screen_text")
    assert out == {"text": "hello"}


def test_call_mcp_tool_no_response(monkeypatch):
    s = _source(monkeypatch)
    s._send_request = MagicMock()
    s._read_response = MagicMock(return_value=None)
    assert s._call_mcp_tool("get_screen_text") is None


def test_read_response_invalid():
    s = _source(None)
    import queue
    s._resp_queue.put("not json")
    assert s._read_response(timeout=0.1) is None


def test_read_response_success_and_empty():
    """_read_response 真实实现：读队列成功 / 超时空"""
    s = _source(None)
    import queue
    s._resp_queue.put('{"jsonrpc":"2.0","id":"1","result":{}}')
    resp = s._read_response(timeout=0.5)
    assert resp == {"jsonrpc": "2.0", "id": "1", "result": {}}
    assert s._read_response(timeout=0.1) is None  # 队列空 → 超时 None
