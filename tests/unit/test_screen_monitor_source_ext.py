"""screen_monitor_source 补测：子进程管理 / MCP 调用 / 解析 / 事件发布 / 后台循环"""
import json
import queue
import threading
from unittest.mock import MagicMock, patch

import modules.perception.difference.sources.screen_monitor_source as sms_mod
from modules.perception.difference.sources.screen_monitor_source import (
    ScreenMonitorSource, get_screen_monitor_source,
)


def _source():
    """__new__ 绕过 __init__，手动注册弱引用表（conftest 统一 stop）"""
    s = ScreenMonitorSource.__new__(ScreenMonitorSource)
    ScreenMonitorSource._all_instances.add(s)
    s._interval = 5.0
    s._running = False
    s._thread = None
    s._last_text_lines = []
    s._proc = None
    s._lock = threading.RLock()
    s._reader_running = False
    s._reader_thread = None
    s._resp_queue = queue.Queue()
    s._proc_restarts = 0
    s._consecutive_timeouts = 0
    return s


def _proc():
    proc = MagicMock()
    proc.poll.return_value = None
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stdout.readline.return_value = ""
    return proc


# ── 初始化 ─────────────────────────────────────────────────────────────

def test_init_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(ScreenMonitorSource, "_find_server_script", staticmethod(lambda: "/srv.py"))
    s = ScreenMonitorSource()
    assert s._interval == 3.0
    assert s._server_script == "/srv.py"
    assert s._running is False
    assert s._proc is None
    assert s._consecutive_timeouts == 0
    s.stop()


def test_init_custom(monkeypatch):
    monkeypatch.setattr(ScreenMonitorSource, "_find_server_script", staticmethod(lambda: "/srv.py"))
    s = ScreenMonitorSource(server_script="/custom.py", interval=7.0)
    assert s._server_script == "/custom.py"
    assert s._interval == 7.0
    s.stop()


def test_find_server_script():
    p = ScreenMonitorSource._find_server_script()
    assert p.endswith("screen_monitor_server.py")
    assert "/infra/mcp/servers/" in p


# ── 子进程管理 ─────────────────────────────────────────────────────────

def test_ensure_process_already_alive():
    s = _source()
    proc = _proc()
    proc.poll.return_value = None
    s._proc = proc
    assert s._ensure_process() is True
    assert s._proc is proc


def test_ensure_process_script_missing(monkeypatch):
    s = _source()
    s._server_script = "/nonexistent.py"
    monkeypatch.setattr("os.path.exists", lambda p: False)
    assert s._ensure_process() is False


def test_ensure_process_init_failure(monkeypatch):
    s = _source()
    s._server_script = "/tmp/x.py"
    monkeypatch.setattr("os.path.exists", lambda p: True)
    proc = _proc()
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: proc)
    s._send_request = MagicMock()
    s._read_response = MagicMock(return_value={"jsonrpc": "2.0", "error": {"code": -1}})
    assert s._ensure_process() is False
    s.stop()


def test_ensure_process_exception(monkeypatch):
    s = _source()
    s._server_script = "/tmp/x.py"
    monkeypatch.setattr("os.path.exists", lambda p: True)

    def boom(*a, **k):
        raise RuntimeError("spawn failed")

    monkeypatch.setattr("subprocess.Popen", boom)
    assert s._ensure_process() is False


def test_close_process_terminate_kill(monkeypatch):
    s = _source()
    proc = _proc()
    proc.terminate.side_effect = RuntimeError("term fail")
    s._proc = proc
    s._resp_queue.put("stale")
    s._close_process()
    assert s._proc is None
    proc.kill.assert_called_once()


def test_close_process_terminate_and_kill_fail(monkeypatch):
    s = _source()
    proc = _proc()
    proc.terminate.side_effect = RuntimeError("term fail")
    proc.kill.side_effect = RuntimeError("kill fail")
    s._proc = proc
    s._close_process()  # 双失败不抛异常
    assert s._proc is None


def test_close_process_no_proc():
    s = _source()
    s._close_process()  # 不抛异常


def test_send_request_no_proc():
    s = _source()
    s._send_request({"jsonrpc": "2.0"})  # proc None → 静默


def test_send_request_stdin_none():
    s = _source()
    proc = MagicMock()
    proc.stdin = None
    s._proc = proc
    s._send_request({"jsonrpc": "2.0"})  # stdin None → 静默


def test_send_request_writes():
    s = _source()
    proc = _proc()
    s._proc = proc
    s._send_request({"jsonrpc": "2.0", "id": "1"})
    proc.stdin.write.assert_called_once()
    proc.stdin.flush.assert_called_once()


# ── MCP 调用 ───────────────────────────────────────────────────────────

def test_call_mcp_tool_ui_elements(monkeypatch):
    s = _source()
    s._send_request = MagicMock()
    text = '[按钮] "确定" 位置=(10,20)-(30,50) 置信度=0.92'
    s._read_response = MagicMock(return_value={
        "result": {"content": [{"type": "text", "text": text}]},
    })
    out = s._call_mcp_tool("analyze_ui_elements")
    assert out["elements"][0]["type"] == "按钮"
    assert out["elements"][0]["text"] == "确定"
    assert out["elements"][0]["confidence"] == 0.92


def test_call_mcp_tool_slow(monkeypatch):
    s = _source()
    s._send_request = MagicMock()
    s._read_response = MagicMock(return_value={
        "result": {"content": [{"type": "text", "text": "hello"}]},
    })
    times = iter([0.0, 20.0])
    monkeypatch.setattr(sms_mod.time, "time", lambda: next(times))
    out = s._call_mcp_tool("get_screen_text")
    assert out == {"text": "hello"}


def test_call_mcp_tool_timeout_single():
    s = _source()
    s._send_request = MagicMock()
    s._read_response = MagicMock(return_value=None)
    s._close_process = MagicMock()
    assert s._call_mcp_tool("x") is None
    assert s._consecutive_timeouts == 1
    s._close_process.assert_not_called()


def test_call_mcp_tool_timeout_double():
    s = _source()
    s._send_request = MagicMock()
    s._read_response = MagicMock(return_value=None)
    s._close_process = MagicMock()
    s._consecutive_timeouts = 1
    assert s._call_mcp_tool("x") is None
    assert s._consecutive_timeouts == 2
    s._close_process.assert_called_once()


def test_read_response_json_decode_error():
    s = _source()
    s._resp_queue.put("{bad json")
    assert s._read_response(timeout=0.1) is None


# ── 后台循环 ───────────────────────────────────────────────────────────

def _run_one_loop(s, monkeypatch):
    """跑 _run_loop 一圈后通过 sleep 置 _running=False 终止"""
    s._running = True

    def sleep_once(secs):
        s._running = False
    monkeypatch.setattr(sms_mod.time, "sleep", sleep_once)
    s._run_loop()


def test_run_loop_publishes_elements(monkeypatch):
    s = _source()
    s._ensure_process = MagicMock(return_value=True)
    s._call_mcp_tool = MagicMock(return_value={"elements": [{"confidence": 0.9}]})
    published = []
    monkeypatch.setattr(s, "_publish_to_event_bus",
                        lambda els: published.append(els))
    _run_one_loop(s, monkeypatch)
    assert published == [[{"confidence": 0.9}]]


def test_run_loop_result_empty_resets_timeout(monkeypatch):
    s = _source()
    s._ensure_process = MagicMock(return_value=True)
    s._call_mcp_tool = MagicMock(return_value={"elements": []})
    s._publish_to_event_bus = MagicMock()
    s._consecutive_timeouts = 1
    _run_one_loop(s, monkeypatch)
    assert s._consecutive_timeouts == 0
    s._publish_to_event_bus.assert_not_called()


def test_run_loop_result_none_keeps_timeout(monkeypatch):
    s = _source()
    s._ensure_process = MagicMock(return_value=True)
    s._call_mcp_tool = MagicMock(return_value=None)
    s._publish_to_event_bus = MagicMock()
    s._consecutive_timeouts = 1
    _run_one_loop(s, monkeypatch)
    assert s._consecutive_timeouts == 1  # 不重置


def test_run_loop_process_fail(monkeypatch):
    s = _source()
    s._ensure_process = MagicMock(return_value=False)
    _run_one_loop(s, monkeypatch)  # sleep + continue


def test_run_loop_exception(monkeypatch):
    s = _source()
    s._ensure_process = MagicMock(side_effect=RuntimeError("boom"))
    _run_one_loop(s, monkeypatch)  # 异常被捕获


# ── UI 元素解析 ────────────────────────────────────────────────────────

def test_parse_ui_elements():
    raw = """[按钮] "提交" 位置=(10,20)-(30,50) 置信度=0.92
[输入框] "搜索" 位置=(1,2)-(11,22) 置信度=0.8
忽略行"""
    out = ScreenMonitorSource._parse_ui_elements(raw)
    assert len(out["elements"]) == 2
    e0 = out["elements"][0]
    assert (e0["x"], e0["y"]) == (10, 20)
    assert (e0["w"], e0["h"]) == (20, 30)
    assert out["elements"][1]["text"] == "搜索"


def test_parse_ui_elements_empty():
    assert ScreenMonitorSource._parse_ui_elements("没有元素") == {"elements": []}


def test_filter_and_diff():
    s = _source()
    elements = [
        {"type": "b", "text": "低置信", "confidence": 0.5},
        {"type": "b", "text": "旧元素", "confidence": 0.95},
        {"type": "b", "text": "新元素", "confidence": 0.8},
    ]
    s._last_text_lines = ["旧元素"]
    new_elems, changed_elems = s._filter_and_diff(elements)
    assert [e["text"] for e in new_elems] == ["新元素"]
    assert len(changed_elems) == 2  # 高置信度元素都在当前
    assert len(s._last_text_lines) == 2


def test_filter_and_diff_nonlist_last():
    s = _source()
    s._last_text_lines = None  # 兼容非 list
    elements = [{"type": "b", "text": "aa", "confidence": 0.9}]
    new_elems, _ = s._filter_and_diff(elements)
    assert len(new_elems) == 1


# ── 公共方法 ───────────────────────────────────────────────────────────

def test_analyze_ui_elements_process_fail():
    s = _source()
    s._ensure_process = MagicMock(return_value=False)
    assert s.analyze_ui_elements() == {"elements": []}


def test_analyze_ui_elements_ok(monkeypatch):
    s = _source()
    s._ensure_process = MagicMock(return_value=True)
    s._call_mcp_tool = MagicMock(return_value={"elements": [{"t": 1}]})
    assert s.analyze_ui_elements(0.5) == {"elements": [{"t": 1}]}
    s._call_mcp_tool.assert_called_once_with("analyze_ui_elements", {"confidence_threshold": 0.5})


def test_call_capture_and_analyze_fail():
    s = _source()
    s._ensure_process = MagicMock(return_value=False)
    assert s._call_capture_and_analyze() == ""


def test_call_capture_and_analyze_ok():
    s = _source()
    s._ensure_process = MagicMock(return_value=True)
    s._call_mcp_tool = MagicMock(return_value={"text": "分析结果"})
    assert s._call_capture_and_analyze() == "分析结果"


# ── 事件发布 ───────────────────────────────────────────────────────────

def test_publish_empty_elements():
    s = _source()
    s._publish_to_event_bus([])  # 直接返回


def test_publish_no_new_elements():
    s = _source()
    s._last_text_lines = ["已有"]
    events = []
    fake_bus = MagicMock()
    fake_bus.publish = lambda e: events.append(e)
    with patch("modules.perception.events.bus.get_event_bus", return_value=fake_bus):
        s._publish_to_event_bus([{"type": "b", "text": "已有", "confidence": 0.9}])
    assert events == []


def test_publish_both_events(monkeypatch):
    s = _source()
    s._last_text_lines = []
    events = []
    fake_bus = MagicMock()
    fake_bus.publish = lambda e: events.append(e)
    monkeypatch.setattr("modules.perception.events.bus.get_event_bus", lambda: fake_bus)
    elements = [
        {"type": "b", "text": "新增1", "confidence": 0.95},
        {"type": "b", "text": "新增2", "confidence": 0.8},
    ]
    s._publish_to_event_bus(elements)
    assert len(events) == 2
    ocr_ev, ui_ev = events
    assert ocr_ev.event_type == "screen.ocr"
    assert ocr_ev.payload["changed_count"] == 2
    assert ui_ev.payload["element_count"] == 2
    assert ui_ev.payload["high_conf_count"] == 2


def test_publish_bus_error(monkeypatch):
    s = _source()
    s._last_text_lines = []
    monkeypatch.setattr("modules.perception.events.bus.get_event_bus",
                        MagicMock(side_effect=RuntimeError("bus down")))
    s._publish_to_event_bus([{"type": "b", "text": "xx", "confidence": 0.9}])  # 不抛异常


# ── 文本行解析 ─────────────────────────────────────────────────────────

def test_parse_text_lines():
    text = """分析结果
检测到的文字:
"第一行"
第二行
- 跳过
屏幕 xxx
按钮 "确定" 区域: (1,2)
尾部"""
    out = ScreenMonitorSource._parse_text_lines(text)
    assert "第一行" in out
    assert "第二行" in out
    assert "- 跳过" not in out
    assert "屏幕 xxx" not in out


def test_parse_text_lines_empty():
    assert ScreenMonitorSource._parse_text_lines("") == []


# ── 单例 ──────────────────────────────────────────────────────────────

def test_get_screen_monitor_source():
    s = get_screen_monitor_source()
    assert isinstance(s, ScreenMonitorSource)
    s.stop()
