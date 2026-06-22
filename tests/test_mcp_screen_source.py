"""
MCP 屏幕差异源 — 完整单元测试

覆盖:
- ScreenDiffSource 初始化和属性
- interval / change_threshold 属性 setter(含 clamp)
- get_stats() 初始状态
- 单例模式 get_screen_diff_source()
- _find_server_script() 路径解析
- start() / stop() / is_running 生命周期
- capture() / capture_screenshot() 公共 API(mock 子进程)
"""
import pytest
import threading
from unittest.mock import patch, MagicMock

from modules.perception.difference.sources.mcp_screen_source import (
    ScreenDiffSource, get_screen_diff_source, _DEFAULT_INTERVAL,
)


class TestInit:
    """ScreenDiffSource 构造函数"""

    def test_default_initialization(self):
        src = ScreenDiffSource()
        assert src.interval == _DEFAULT_INTERVAL
        assert src.change_threshold == 0.01
        assert src.is_running is False
        assert src._thread is None
        assert src._proc is None

    def test_custom_interval(self):
        src = ScreenDiffSource(interval=2.5)
        assert src.interval == 2.5

    def test_custom_server_script(self):
        src = ScreenDiffSource(server_script="/custom/path/server.py")
        assert src._server_script == "/custom/path/server.py"

    def test_find_server_script_defaults(self):
        src = ScreenDiffSource()
        assert "screen_diff_server.py" in src._server_script


class TestIntervalProperty:
    """interval 属性 getter/setter — 范围 [0.1, 30.0] 钳位"""

    def test_getter_reflects_init_value(self):
        src = ScreenDiffSource(interval=3.0)
        assert src.interval == 3.0

    @pytest.mark.parametrize("set_value,expected", [
        (5.0, 5.0),       # 正常值
        (0.01, 0.1),      # 低于下限 → 钳到 0.1
        (100.0, 30.0),    # 高于上限 → 钳到 30.0
        (0.1, 0.1),       # 下边界
        (30.0, 30.0),     # 上边界
    ])
    def test_setter_clamps(self, set_value, expected):
        src = ScreenDiffSource()
        src.interval = set_value
        assert src.interval == expected


class TestChangeThresholdProperty:
    """change_threshold 属性 getter/setter — 范围 [0.0, 1.0] 钳位"""

    def test_getter_returns_float(self):
        src = ScreenDiffSource()
        assert isinstance(src.change_threshold, float)

    @pytest.mark.parametrize("set_value,expected", [
        (0.05, 0.05),     # 正常值
        (-1.0, 0.0),      # 低于下限 → 钳到 0.0
        (2.0, 1.0),       # 高于上限 → 钳到 1.0
        (0.0, 0.0),       # 下边界
        (1.0, 1.0),       # 上边界
    ])
    def test_setter_clamps(self, set_value, expected):
        src = ScreenDiffSource()
        src.change_threshold = set_value
        assert src.change_threshold == expected


class TestGetStats:
    """get_stats() 状态统计"""

    def test_initial_state(self):
        src = ScreenDiffSource()
        stats = src.get_stats()
        assert stats["running"] is False
        assert stats["scan_count"] == 0
        assert stats["total_changes"] == 0
        assert stats["last_change_ratio"] == 0.0
        assert stats["consecutive_no_change"] == 0
        assert stats["proc_restarts"] == 0

    def test_after_start_stop(self):
        src = ScreenDiffSource()
        with patch.object(src, "_ensure_process", return_value=True):
            with patch.object(src, "_run_loop"):
                src.start()
                assert src.get_stats()["running"] is True
                src.stop()
                assert src.get_stats()["running"] is False

    def test_stats_keys_immutable(self):
        src = ScreenDiffSource()
        expected_keys = {"running", "scan_count", "total_changes",
                         "last_change_ratio", "consecutive_no_change", "proc_restarts"}
        assert set(src.get_stats().keys()) == expected_keys


class TestGetScreenDiffSource:
    """get_screen_diff_source() 全局单例"""

    def setup_method(self):
        import modules.perception.difference.sources.mcp_screen_source as ms
        ms._instance = None

    def test_singleton(self):
        s1 = get_screen_diff_source()
        s2 = get_screen_diff_source()
        assert s1 is s2
        assert isinstance(s1, ScreenDiffSource)


class TestFindServerScript:
    """_find_server_script() 路径解析"""

    def test_resolves_to_screen_diff_server(self):
        path = ScreenDiffSource._find_server_script()
        assert isinstance(path, str)
        assert path.endswith("screen_diff_server.py")
        assert "infra/mcp/servers/screen_diff_server.py" in path.replace("\\", "/")


class TestLifecycle:
    """start() / stop() / is_running"""

    def test_start_sets_running(self):
        src = ScreenDiffSource()
        with patch.object(src, "_ensure_process", return_value=True):
            with patch.object(src, "_run_loop"):
                src.start()
                assert src.is_running is True
                src.stop()
                assert src.is_running is False

    def test_start_creates_daemon_thread(self):
        src = ScreenDiffSource()
        with patch.object(src, "_ensure_process", return_value=True):
            with patch.object(src, "_run_loop"):
                src.start()
                assert src._thread is not None
                assert src._thread.daemon is True
                assert src._thread.name == "mcp-screen-diff"
                src.stop()

    def test_start_idempotent(self):
        src = ScreenDiffSource()
        with patch.object(src, "_ensure_process", return_value=True):
            with patch.object(src, "_run_loop"):
                src.start()
                src.start()
                assert src._thread is not None
                src.stop()

    def test_stop_when_not_running(self):
        src = ScreenDiffSource()
        src.stop()
        assert src.is_running is False

    def test_stop_closes_process(self):
        src = ScreenDiffSource()
        with patch.object(src, "_close_process") as mock_close:
            with patch.object(src, "_ensure_process", return_value=True):
                with patch.object(src, "_run_loop"):
                    src.start()
                    src.stop()
                    mock_close.assert_called_once()


class TestPublicAPI:
    """capture() / capture_screenshot() — mock MCP 子进程"""

    def test_capture_ensure_process_failure(self):
        src = ScreenDiffSource()
        with patch.object(src, "_ensure_process", return_value=False):
            result = src.capture()
            assert result is None

    def test_capture_calls_mcp_tool(self):
        src = ScreenDiffSource()
        expected = {"changed": True, "change_ratio": 0.05, "regions": []}
        with patch.object(src, "_ensure_process", return_value=True):
            with patch.object(src, "_call_mcp_tool", return_value=expected) as mock_mcp:
                result = src.capture()
                mock_mcp.assert_called_once_with("check_screen_changes")
                assert result == expected

    def test_capture_screenshot_ensure_process_failure(self):
        src = ScreenDiffSource()
        with patch.object(src, "_ensure_process", return_value=False):
            result = src.capture_screenshot()
            assert result is None

    def test_capture_screenshot_calls_mcp_tool(self):
        src = ScreenDiffSource()
        expected = {"mime_type": "image/png", "data": "base64...", "width": 1920, "height": 1080}
        with patch.object(src, "_ensure_process", return_value=True):
            with patch.object(src, "_call_mcp_tool", return_value=expected) as mock_mcp:
                result = src.capture_screenshot()
                mock_mcp.assert_called_once_with("capture_screenshot")
                assert result == expected


class TestEnsureProcess:
    """_ensure_process() 子进程生命周期"""

    def test_process_already_running(self):
        src = ScreenDiffSource()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        src._proc = mock_proc
        result = src._ensure_process()
        assert result is True
        assert src._proc is mock_proc

    def test_script_not_found(self):
        src = ScreenDiffSource(server_script="/nonexistent/path.py")
        result = src._ensure_process()
        assert result is False

    def test_process_initialize_failure(self):
        """子进程启动后 init 握手无响应 → _ensure_process 返回 False 并 terminate

        注意：源码已从 select.select 切换到 reader 线程模型（见
        mcp_screen_source.py:188 注释），不再 import select。
        """
        src = ScreenDiffSource(server_script="/nonexistent/path.py")
        with patch("os.path.exists", return_value=True):
            with patch("modules.perception.difference.sources.mcp_screen_source.subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_proc.poll.return_value = None
                mock_proc.stdout.readline.return_value = ""  # 无 init 响应
                mock_popen.return_value = mock_proc
                result = src._ensure_process()
                assert result is False
                mock_proc.terminate.assert_called_once()

    def test_process_start_exception(self):
        src = ScreenDiffSource(server_script="/nonexistent/path.py")
        with patch("os.path.exists", return_value=True):
            with patch("modules.perception.difference.sources.mcp_screen_source.subprocess.Popen") as mock_popen:
                mock_popen.side_effect = OSError("exec format error")
                result = src._ensure_process()
                assert result is False


class TestCloseProcess:
    """_close_process() 子进程关闭"""

    def test_terminates_running_process(self):
        src = ScreenDiffSource()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        src._proc = mock_proc
        src._close_process()
        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once_with(timeout=2)
        assert src._proc is None

    def test_kills_if_terminate_timeout(self):
        src = ScreenDiffSource()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.side_effect = TimeoutError("timeout")
        src._proc = mock_proc
        src._close_process()
        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()
        assert src._proc is None

    def test_noop_when_no_process(self):
        src = ScreenDiffSource()
        src._close_process()
        assert src._proc is None

    def test_sets_proc_to_none(self):
        src = ScreenDiffSource()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        src._proc = mock_proc
        src._close_process()
        assert src._proc is None


class TestMcpCommunication:
    """MCP stdin/stdout 通信"""

    def test_send_request_writes_to_stdin(self):
        src = ScreenDiffSource()
        mock_stdin = MagicMock()
        mock_proc = MagicMock()
        mock_proc.stdin = mock_stdin
        src._proc = mock_proc
        src._send_request({"jsonrpc": "2.0", "method": "ping"})
        mock_stdin.write.assert_called_once()
        mock_stdin.flush.assert_called_once()
        written = mock_stdin.write.call_args[0][0]
        assert "ping" in written

    def test_send_request_noop_no_process(self):
        src = ScreenDiffSource()
        src._send_request({"jsonrpc": "2.0", "method": "ping"})

    def test_read_response_noop_no_stdout(self):
        src = ScreenDiffSource()
        src._proc = None
        result = src._read_response(timeout=1.0)
        assert result is None


class TestCallMcpTool:
    """_call_mcp_tool() 内部工具调用"""

    def test_counter_increments(self):
        src = ScreenDiffSource()
        old_counter = ScreenDiffSource._req_counter
        with patch.object(src, "_send_request"):
            with patch.object(src, "_read_response", return_value={
                "result": {"content": [{"type": "text", "text": '{"ok": true}'}]}
            }):
                src._call_mcp_tool("test_tool")
                assert ScreenDiffSource._req_counter == old_counter + 1

    def test_parses_json_content(self):
        src = ScreenDiffSource()
        with patch.object(src, "_send_request"):
            with patch.object(src, "_read_response", return_value={
                "result": {"content": [{"type": "text", "text": '{"changed": true}'}]}
            }):
                result = src._call_mcp_tool("check_screen_changes")
                assert result == {"changed": True}

    def test_fallback_when_not_json(self):
        src = ScreenDiffSource()
        with patch.object(src, "_send_request"):
            with patch.object(src, "_read_response", return_value={
                "result": {"content": [{"type": "text", "text": "plain text"}]}
            }):
                result = src._call_mcp_tool("test")
                assert result == {"text": "plain text"}

    def test_returns_none_on_no_result(self):
        src = ScreenDiffSource()
        with patch.object(src, "_send_request"):
            with patch.object(src, "_read_response", return_value={"result": {"content": []}}):
                result = src._call_mcp_tool("test")
                assert result is None

    def test_returns_none_on_empty_response(self):
        src = ScreenDiffSource()
        with patch.object(src, "_send_request"):
            with patch.object(src, "_read_response", return_value=None):
                result = src._call_mcp_tool("test")
                assert result is None


class TestCheckOnce:
    """_check_once() 单次检测循环(mock MCP 通信 + detector)"""

    def test_no_change_does_not_ingest(self):
        src = ScreenDiffSource()
        with patch.object(src, "_ensure_process", return_value=True):
            with patch.object(src, "_call_mcp_tool", return_value={
                "changed": False, "change_ratio": 0.0, "regions": []
            }):
                with patch("modules.perception.difference.get_detector") as mock_get:
                    src._check_once()
                    mock_get.return_value.ingest.assert_not_called()
                    assert src._consecutive_no_change == 1
                    assert src._last_change_ratio == 0.0

    def test_change_ingests_to_detector(self):
        src = ScreenDiffSource()
        with patch.object(src, "_ensure_process", return_value=True):
            with patch.object(src, "_call_mcp_tool", return_value={
                "changed": True, "change_ratio": 0.05, "regions": [{"x": 10, "y": 20, "w": 100, "h": 50}],
                "width": 1920, "height": 1080,
            }):
                with patch("modules.perception.difference.get_detector") as mock_get:
                    mock_detector = MagicMock()
                    mock_get.return_value = mock_detector
                    src._check_once()
                    mock_detector.ingest.assert_called_once()
                    args = mock_detector.ingest.call_args[1]
                    assert args["target_type"] == "screen"
                    assert args["change_type"] == "changed"
                    assert src._total_changes == 1

    def test_ensure_process_failure_skips(self):
        src = ScreenDiffSource()
        with patch.object(src, "_ensure_process", return_value=False):
            src._check_once()
            assert src._scan_count == 0

    def test_mcp_tool_returns_none_skips(self):
        src = ScreenDiffSource()
        with patch.object(src, "_ensure_process", return_value=True):
            with patch.object(src, "_call_mcp_tool", return_value=None):
                src._check_once()
                assert src._scan_count == 0


class TestRunLoop:
    """_run_loop() 后台检测主循环"""

    def test_ensure_process_failure_early_return(self):
        src = ScreenDiffSource()
        with patch.object(src, "_ensure_process", return_value=False):
            src._run_loop()
            assert src.is_running is False

    def test_runs_check_once_then_exits_on_stop(self):
        src = ScreenDiffSource()
        src._running = True

        def fake_check():
            src._running = False

        with patch.object(src, "_ensure_process", return_value=True):
            with patch.object(src, "_check_once") as mock_check:
                mock_check.side_effect = fake_check
                src._run_loop()
                mock_check.assert_called_once()

    def test_exception_in_check_triggers_restart(self):
        src = ScreenDiffSource()
        src._running = True
        call_count = [0]

        def check_with_error():
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("mock crash")
            src._running = False

        with patch.object(src, "_ensure_process", return_value=True):
            with patch.object(src, "_check_once") as mock_check:
                mock_check.side_effect = check_with_error
                src._run_loop()
                assert call_count[0] >= 2


class TestThreadSafety:
    """线程安全相关"""

    def test_get_screen_diff_source_thread_safe(self):
        import modules.perception.difference.sources.mcp_screen_source as ms
        ms._instance = None
        results = []

        def get():
            results.append(get_screen_diff_source())

        threads = [threading.Thread(target=get) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for r in results:
            assert r is results[0]

    def test_lock_prevents_race(self):
        """验证 _lock 是可用的锁对象（Lock 或 RLock 都接受）"""
        src = ScreenDiffSource()
        # 鸭子类型校验：能进入 with 块、能 acquire/release 即可
        with src._lock:
            pass
        acquired = src._lock.acquire(blocking=False)
        assert acquired is True
        src._lock.release()
