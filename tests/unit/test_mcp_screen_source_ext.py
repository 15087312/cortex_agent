"""mcp_screen_source 补测 — 覆盖 detect / 相对路径 / 初始化握手 / reader 线程 / 事件限频

用 __new__ 手动注册弱引用表（conftest 统一 stop），不 mock _close_process 保留真实清理。
"""
import queue
import threading
import time
from unittest.mock import MagicMock, patch

import modules.perception.difference.sources.mcp_screen_source as mss
from modules.perception.difference.sources.mcp_screen_source import (
    ScreenDiffSource, get_screen_diff_source,
)


def _src(monkeypatch=None):
    s = ScreenDiffSource.__new__(ScreenDiffSource)
    ScreenDiffSource._all_instances.add(s)
    s._interval = 1.0
    s._change_threshold = 0.01
    s._running = False
    s._thread = None
    s._proc = None
    s._lock = threading.RLock()
    s._event_bus = None
    s._resp_queue = queue.Queue()
    s._reader_thread = None
    s._reader_running = False
    s._last_change_ratio = 0.0
    s._consecutive_no_change = 0
    s._total_changes = 0
    s._scan_count = 0
    s._last_activity_time = 0.0
    s._proc_restarts = 0
    s._last_event_publish_time = 0.0
    s._consecutive_timeouts = 0
    s._server_script = "/tmp/screen_diff_server.py"
    s._enabled = True
    return s


def _proc():
    p = MagicMock()
    p.poll.return_value = None
    p.stdin = MagicMock()
    p.stdout = MagicMock()
    p.stdout.readline.return_value = ""
    return p


# ── 基础属性 ─────────────────────────────────────────────────────────────

def test_source_type_and_detect():
    s = _src()
    assert s.source_type == "screen"
    assert s.detect() == []
    s.stop()


def test_enabled_property():
    s = _src()
    s._enabled = True
    assert s.enabled is True
    s.enabled = False
    assert s.enabled is False
    s.stop()


def test_find_server_script_fallback(monkeypatch):
    monkeypatch.setattr("os.path.exists", lambda p: False)
    assert ScreenDiffSource._find_server_script() == "infra/mcp/servers/screen_diff_server.py"


def test_ensure_process_relative_path(monkeypatch):
    s = _src()
    s._server_script = "infra/mcp/servers/screen_diff_server.py"
    monkeypatch.setattr("os.path.isabs", lambda p: False)
    monkeypatch.setattr("os.path.exists", lambda p: True)
    proc = _proc()
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: proc)
    s._send_request = MagicMock()
    s._read_response = MagicMock(return_value={"jsonrpc": "2.0", "result": {}})
    assert s._ensure_process() is True
    assert s._proc_restarts == 1
    s._close_process()


def test_ensure_process_init_success(monkeypatch):
    s = _src()
    monkeypatch.setattr("os.path.isabs", lambda p: True)
    monkeypatch.setattr("os.path.exists", lambda p: True)
    proc = _proc()
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: proc)
    s._send_request = MagicMock()
    s._read_response = MagicMock(return_value={"jsonrpc": "2.0", "result": {}})
    assert s._ensure_process() is True
    assert s._proc_restarts == 1
    # initialized 通知已发送
    assert any("notifications/initialized" in str(c) for c in s._send_request.call_args_list)
    s._close_process()


def test_ensure_process_init_response_error(monkeypatch):
    s = _src()
    monkeypatch.setattr("os.path.isabs", lambda p: True)
    monkeypatch.setattr("os.path.exists", lambda p: True)
    proc = _proc()
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: proc)
    s._send_request = MagicMock()
    s._read_response = MagicMock(return_value={"jsonrpc": "2.0", "error": {"code": -1}})
    assert s._ensure_process() is False
    assert s._proc is None
    s.stop()


# ── _close_process 边界 ──────────────────────────────────────────────────

def test_close_process_drains_queue():
    s = _src()
    s._resp_queue.put('{"a":1}')
    s._resp_queue.put('{"b":2}')
    s._close_process()
    assert s._resp_queue.empty()


def test_close_process_joins_reader_thread():
    s = _src()
    proc = _proc()
    s._proc = proc
    reader = threading.Thread(target=lambda: None)
    reader.start()
    s._reader_thread = reader
    s._reader_running = True
    s._close_process()
    assert s._reader_thread is None
    assert s._reader_running is False


def test_close_process_kill_fallback(monkeypatch):
    s = _src()
    proc = _proc()
    proc.terminate.side_effect = RuntimeError("term fail")
    proc.kill.side_effect = RuntimeError("kill fail")
    s._proc = proc
    s._close_process()
    assert s._proc is None


# ── reader 线程 / _read_response ─────────────────────────────────────────

def test_read_stdout_loop_proc_none():
    s = _src()
    s._reader_running = True
    s._proc = None
    s._read_stdout_loop()  # break 分支
    assert s._reader_running is True


def test_read_stdout_loop_stdout_none():
    s = _src()
    s._reader_running = True
    proc = _proc()
    proc.stdout = None
    s._proc = proc
    s._read_stdout_loop()  # else → break
    assert s._reader_running is True


def test_read_stdout_loop_exception():
    s = _src()
    s._reader_running = True
    proc = _proc()
    proc.stdout.readline.side_effect = RuntimeError("io error")
    s._proc = proc
    s._read_stdout_loop()  # except → break
    assert s._reader_running is True


def test_read_stdout_loop_reads_line():
    s = _src()
    s._reader_running = True
    proc = _proc()
    lines = iter(['{"ok": true}\n', ""])
    proc.stdout.readline.side_effect = lambda: next(lines)
    s._proc = proc
    s._read_stdout_loop()
    assert not s._resp_queue.empty()
    assert s._resp_queue.get() == '{"ok": true}'


def test_read_response_json_decode_error():
    s = _src()
    s._resp_queue.put("{bad json")
    assert s._read_response(timeout=0.1) is None


def test_read_response_other_exception(monkeypatch):
    s = _src()
    monkeypatch.setattr(mss.json, "loads", lambda line: (_ for _ in ()).throw(ValueError("bad")))
    s._resp_queue.put("{}")
    assert s._read_response(timeout=0.1) is None


# ── _call_mcp_tool 慢响应 / 连续超时 ─────────────────────────────────────

def test_call_mcp_tool_slow_log(monkeypatch):
    s = _src()
    s._send_request = MagicMock()
    s._read_response = MagicMock(return_value={"result": {"content": [{"type": "text", "text": '{"x": 1}'}]}})
    times = iter([0.0, 10.0])
    monkeypatch.setattr(mss.time, "time", lambda: next(times))
    out = s._call_mcp_tool("slow_tool")
    assert out == {"x": 1}


def test_call_mcp_tool_timeout_restart():
    s = _src()
    s._send_request = MagicMock()
    s._read_response = MagicMock(return_value=None)
    s._consecutive_timeouts = 1
    s._close_process = MagicMock()
    assert s._call_mcp_tool("x") is None
    assert s._consecutive_timeouts == 2
    s._close_process.assert_called_once()


def test_call_mcp_tool_non_text_content():
    s = _src()
    s._send_request = MagicMock()
    s._read_response = MagicMock(return_value={"result": {"content": [{"type": "image"}]}})
    assert s._call_mcp_tool("x") is None


# ── _run_loop 边界 ───────────────────────────────────────────────────────

def test_run_loop_ensure_process_failure_and_restart(monkeypatch):
    """初始 ensure 成功，check_once 抛异常 → 重试 ensure 失败 → sleep(interval*3)"""
    s = _src()
    s._running = True
    ensure_calls = {"n": 0}

    def fake_ensure():
        ensure_calls["n"] += 1
        return ensure_calls["n"] == 1  # 首次成功，随后失败

    def check_once():
        raise RuntimeError("mock crash")

    s._ensure_process = MagicMock(side_effect=fake_ensure)
    s._check_once = MagicMock(side_effect=check_once)
    sleeps = []
    monkeypatch.setattr(mss.time, "sleep",
                        lambda secs: sleeps.append(secs) or (setattr(s, "_running", False) if secs >= s._interval * 3 else None))
    s._run_loop()
    assert ensure_calls["n"] >= 2
    assert s._interval * 3 in sleeps


def test_run_loop_check_exception_then_sleep3(monkeypatch):
    """check_once 抛异常 → 重试 ensure 成功 → 继续循环直至 stop"""
    s = _src()
    s._running = True
    calls = {"check": 0}

    def fake_ensure():
        return True

    def check_once():
        calls["check"] += 1
        if calls["check"] == 1:
            raise RuntimeError("boom")
        s._running = False

    s._ensure_process = MagicMock(side_effect=fake_ensure)
    s._check_once = MagicMock(side_effect=check_once)
    monkeypatch.setattr(mss.time, "sleep", lambda *a, **k: None)
    s._run_loop()
    assert calls["check"] >= 2


# ── _check_once 更多分支 ─────────────────────────────────────────────────

def test_check_once_not_enabled():
    s = _src()
    s._enabled = False
    s._check_once()  # enabled=False → return
    assert s._scan_count == 0


def test_check_once_change_high_threshold(monkeypatch):
    s = _src()
    s._ensure_process = MagicMock(return_value=True)
    s._call_mcp_tool = MagicMock(return_value={
        "changed": True, "change_ratio": 0.6, "regions": [{"x": 1}],
        "width": 100, "height": 100,
    })
    s._publish_screen_diff_event = MagicMock()
    with patch("modules.perception.difference.get_detector"):
        s._check_once()
    assert s._total_changes == 1
    assert s._consecutive_no_change == 0


def test_check_once_ingest_exception(monkeypatch):
    s = _src()
    s._ensure_process = MagicMock(return_value=True)
    s._call_mcp_tool = MagicMock(return_value={"changed": True, "change_ratio": 0.05, "regions": []})
    s._publish_screen_diff_event = MagicMock()
    with patch("modules.perception.difference.get_detector",
               side_effect=RuntimeError("detector down")):
        s._check_once()  # 注入失败非致命
    assert s._total_changes == 1


# ── _publish_screen_diff_event 限频 / 异常 ───────────────────────────────

def test_publish_event_rate_limited(monkeypatch):
    s = _src()
    s._last_event_publish_time = time.time() + 100  # 未来时间 → 限频
    s._publish_screen_diff_event(0.05, [], {"width": 1, "height": 1})
    assert s._event_bus is None  # 未触发


def test_publish_event_ok(monkeypatch):
    s = _src()
    s._last_event_publish_time = 0.0
    published = []
    fake_bus = MagicMock()
    fake_bus.publish = lambda e: published.append(e)
    monkeypatch.setattr(mss, "get_event_bus", lambda: fake_bus)
    s._publish_screen_diff_event(0.05, [{"x": 1}], {"width": 10, "height": 20})
    assert len(published) == 1
    assert published[0].event_type == "screen.diff"


def test_publish_event_bus_exception(monkeypatch):
    s = _src()
    s._last_event_publish_time = 0.0
    monkeypatch.setattr(mss, "get_event_bus",
                        lambda: (_ for _ in ()).throw(RuntimeError("bus down")))
    s._publish_screen_diff_event(0.05, [], {"width": 1, "height": 1})  # 不抛异常


# ── 单例注册 ─────────────────────────────────────────────────────────────

def test_get_screen_diff_source_registers(monkeypatch):
    import modules.perception.difference.sources.mcp_screen_source as ms
    ms._instance = None
    fake_detector = MagicMock()
    fake_detector.registry = MagicMock()
    monkeypatch.setattr("modules.perception.difference.detector.get_detector", lambda: fake_detector)
    inst = get_screen_diff_source()
    assert inst is not None
    fake_detector.registry.register.assert_called_once_with(inst)
    ms._instance = None


def test_get_screen_diff_source_register_error(monkeypatch):
    import modules.perception.difference.sources.mcp_screen_source as ms
    ms._instance = None
    monkeypatch.setattr("modules.perception.difference.detector.get_detector",
                        lambda: (_ for _ in ()).throw(RuntimeError("no detector")))
    inst = get_screen_diff_source()
    assert inst is not None  # 注册失败不致命
    ms._instance = None


def test_get_screen_diff_source_inner_lock_race(monkeypatch):
    """外层检查 None、进入锁后已被占位 → 内层 if False 分支（479->483）"""
    import modules.perception.difference.sources.mcp_screen_source as ms
    ms._instance = None
    placeholder = object()
    with ms._instance_lock:
        ms._instance = placeholder
        inst = get_screen_diff_source()
    assert inst is placeholder
    ms._instance = None


# ── 队列竞态 / 空串响应 / 预置 event_bus ─────────────────────────────────

def test_close_process_queue_empty_race(monkeypatch):
    """get_nowait 抛 Empty → break（260-261）"""
    s = _src()
    s._resp_queue.put("x")
    monkeypatch.setattr(s._resp_queue, "get_nowait",
                        lambda: (_ for _ in ()).throw(queue.Empty))
    s._close_process()  # 不抛异常


def test_read_response_empty_string():
    s = _src()
    s._resp_queue.put("")
    assert s._read_response(timeout=0.1) is None


def test_run_loop_not_running(monkeypatch):
    """_running=False 且 ensure 成功 → while 不进入直接返回"""
    s = _src()
    s._running = False
    s._ensure_process = MagicMock(return_value=True)
    s._run_loop()
    s._check_once = MagicMock()
    s._run_loop()


def test_publish_event_uses_cached_bus(monkeypatch):
    """event_bus 已缓存 → 不重新获取（443->445 False 分支）"""
    s = _src()
    s._last_event_publish_time = 0.0
    fake_bus = MagicMock()
    s._event_bus = fake_bus
    monkeypatch.setattr(mss, "get_event_bus",
                        lambda: (_ for _ in ()).throw(RuntimeError("不应调用")))
    s._publish_screen_diff_event(0.05, [], {"width": 1, "height": 1})
    fake_bus.publish.assert_called_once()


def test_call_mcp_tool_response_no_result_key():
    """resp 有值但无 result 键 → return None（301->310）"""
    s = _src()
    s._send_request = MagicMock()
    s._read_response = MagicMock(return_value={"jsonrpc": "2.0", "id": "1", "error": {"code": -32601}})
    assert s._call_mcp_tool("x") is None


def test_read_stdout_loop_not_running():
    """_reader_running=False → while 不进直接返回（333->exit）"""
    s = _src()
    s._reader_running = False
    s._read_stdout_loop()  # 不抛异常


def test_get_screen_diff_source_inner_lock_race_via_lock(monkeypatch):
    """自定义锁：进入 __enter__ 后置位 _instance → 内层 if False（473->483）"""
    import modules.perception.difference.sources.mcp_screen_source as ms
    ms._instance = None
    placeholder = object()

    class SneakyLock:
        def __enter__(self):
            ms._instance = placeholder
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ms, "_instance_lock", SneakyLock())
    inst = get_screen_diff_source()
    assert inst is placeholder
    ms._instance = None


def test_get_screen_diff_source_logger_no_debug(monkeypatch):
    """logger 无 debug 属性 → 跳过注册日志（479->483）"""
    import modules.perception.difference.sources.mcp_screen_source as ms
    ms._instance = None

    class _L:
        def info(self, *a, **k):
            pass

        def warning(self, *a, **k):
            pass

        def error(self, *a, **k):
            pass

    fake_detector = MagicMock()
    fake_detector.registry = MagicMock()
    monkeypatch.setattr(ms, "logger", _L())
    monkeypatch.setattr("modules.perception.difference.detector.get_detector", lambda: fake_detector)
    inst = get_screen_diff_source()
    assert inst is not None
    fake_detector.registry.register.assert_called_once()
    ms._instance = None
