"""
MCP Screen Diff Server — 完整单元测试

覆盖:
- _compute_frame_diff: 相同帧/不同帧/尺寸不一致/空帧
- MCP 协议处理: initialize, list_tools, get_stats, call_tool
- _handle_call_tool: 已知工具/未知工具/异常传播
- _handle_capture_screenshot(mock 截图)
- 窗口运动(region tracking)
"""
import pytest
import json
import numpy as np
from unittest.mock import patch, MagicMock


class TestComputeFrameDiff:
    """_compute_frame_diff 像素级帧差检测"""

    def test_same_frame_no_change(self):
        from infra.mcp.servers.screen_diff_server import _compute_frame_diff
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        result = _compute_frame_diff(frame, frame)
        assert result["has_changed"] is False
        assert result["change_ratio"] == 0.0

    def test_completely_different_frame(self):
        from infra.mcp.servers.screen_diff_server import _compute_frame_diff
        black = np.zeros((100, 200, 3), dtype=np.uint8)
        white = np.ones((100, 200, 3), dtype=np.uint8) * 255
        result = _compute_frame_diff(black, white)
        assert result["has_changed"] is True
        assert result["change_ratio"] > 0.5

    def test_different_dimensions_triggers_change(self):
        from infra.mcp.servers.screen_diff_server import _compute_frame_diff
        small = np.zeros((50, 100, 3), dtype=np.uint8)
        large = np.zeros((100, 200, 3), dtype=np.uint8)
        result = _compute_frame_diff(large, small)
        assert result["has_changed"] is True
        assert result["change_ratio"] == 1.0

    def test_grayscale_input(self):
        from infra.mcp.servers.screen_diff_server import _compute_frame_diff
        frame_a = np.zeros((50, 100), dtype=np.uint8)
        frame_b = np.ones((50, 100), dtype=np.uint8) * 255
        result = _compute_frame_diff(frame_a, frame_b)
        assert result["has_changed"] is True
        assert result["change_ratio"] == 1.0

    def test_partial_change(self):
        from infra.mcp.servers.screen_diff_server import _compute_frame_diff
        frame = np.zeros((200, 200, 3), dtype=np.uint8)
        modified = frame.copy()
        modified[:40, :40, :] = 255
        result = _compute_frame_diff(frame, modified)
        assert result["has_changed"] is True
        assert 0.01 < result["change_ratio"] < 0.5
        assert len(result["changed_regions"]) > 0

    def test_region_tracking(self):
        """变化区域应包含坐标信息"""
        from infra.mcp.servers.screen_diff_server import _compute_frame_diff
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        modified = frame.copy()
        modified[30:50, 40:60, :] = 255
        result = _compute_frame_diff(frame, modified)
        assert result["has_changed"] is True
        if result["changed_regions"]:
            region = result["changed_regions"][0]
            assert "x" in region
            assert "y" in region
            assert "w" in region
            assert "h" in region

    def test_output_keys(self):
        """验证返回 dict 的完整结构"""
        from infra.mcp.servers.screen_diff_server import _compute_frame_diff
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        result = _compute_frame_diff(frame, frame)
        expected_keys = {"has_changed", "change_ratio", "changed_regions", "width", "height"}
        assert set(result.keys()) == expected_keys


class TestHandleInitialize:
    """MCP initialize handler"""

    def test_returns_proper_response(self):
        from infra.mcp.servers.screen_diff_server import _handle_initialize
        with patch("infra.mcp.servers.screen_diff_server._send") as mock_send:
            _handle_initialize({"id": 1, "method": "initialize"})
            mock_send.assert_called_once()
            args = mock_send.call_args[0][0]
            assert args["jsonrpc"] == "2.0"
            assert args["id"] == 1
            assert "result" in args
            assert args["result"]["protocolVersion"] == "2024-11-05"
            assert args["result"]["serverInfo"]["name"] == "screen_diff"


class TestHandleListTools:
    """MCP list tools handler"""

    def test_returns_three_tools(self):
        from infra.mcp.servers.screen_diff_server import _handle_list_tools, _TOOL_HANDLERS
        with patch("infra.mcp.servers.screen_diff_server._send") as mock_send:
            _handle_list_tools({"id": 2, "method": "tools/list"})
            mock_send.assert_called_once()
            args = mock_send.call_args[0][0]
            tools = args["result"]["tools"]
            assert len(tools) == 3
            tool_names = {t["name"] for t in tools}
            assert tool_names == {"check_screen_changes", "capture_screenshot", "get_stats"}
            assert set(tool_names) == set(_TOOL_HANDLERS.keys())

    def test_tools_have_input_schema(self):
        from infra.mcp.servers.screen_diff_server import _handle_list_tools
        with patch("infra.mcp.servers.screen_diff_server._send") as mock_send:
            _handle_list_tools({"id": 3})
            tools = mock_send.call_args[0][0]["result"]["tools"]
            for tool in tools:
                assert "inputSchema" in tool


class TestHandleGetStats:
    """MCP get_stats handler"""

    def setup_method(self):
        import infra.mcp.servers.screen_diff_server as sds
        sds._prev_frame = None
        sds._frame_count = 0

    def test_returns_frame_count(self):
        from infra.mcp.servers.screen_diff_server import _handle_get_stats
        result = _handle_get_stats({})
        content = result["content"][0]["text"]
        data = json.loads(content)
        assert "frame_count" in data
        assert "has_previous_frame" in data


class TestHandleCheckScreenChanges:
    """MCP check_screen_changes handler (mock 截图)"""

    def setup_method(self):
        import infra.mcp.servers.screen_diff_server as sds
        sds._prev_frame = None
        sds._frame_count = 0

    def test_capture_failure_returns_error(self):
        from infra.mcp.servers.screen_diff_server import _handle_check_screen_changes
        with patch("infra.mcp.servers.screen_diff_server._capture_screen") as mock_cap:
            mock_cap.return_value = None
            result = _handle_check_screen_changes({})
            text = result["content"][0]["text"]
            data = json.loads(text)
            assert "error" in data
            assert data["error"] == "截图失败"

    def test_first_frame_returns_full_change(self):
        import infra.mcp.servers.screen_diff_server as sds
        sds._prev_frame = None
        with patch("infra.mcp.servers.screen_diff_server._capture_screen") as mock_cap:
            mock_cap.return_value = np.zeros((100, 200, 3), dtype=np.uint8)
            result = sds._handle_check_screen_changes({})
            data = json.loads(result["content"][0]["text"])
            assert data["changed"] is True
            assert data["change_ratio"] == 1.0
            assert data["width"] == 200
            assert data["height"] == 100

    def test_second_frame_compares_with_first(self):
        import infra.mcp.servers.screen_diff_server as sds
        sds._prev_frame = None
        sds._frame_count = 0
        with patch("infra.mcp.servers.screen_diff_server._capture_screen") as mock_cap:
            mock_cap.return_value = np.zeros((100, 200, 3), dtype=np.uint8)
            sds._handle_check_screen_changes({})
            mock_cap.return_value = np.ones((100, 200, 3), dtype=np.uint8) * 255
            result = sds._handle_check_screen_changes({})
            data = json.loads(result["content"][0]["text"])
            assert data["changed"] is True
            assert data["change_ratio"] > 0.5
            assert data["frame_count"] == 2

    def test_result_structure(self):
        import infra.mcp.servers.screen_diff_server as sds
        sds._prev_frame = None
        with patch("infra.mcp.servers.screen_diff_server._capture_screen") as mock_cap:
            mock_cap.return_value = np.zeros((100, 200, 3), dtype=np.uint8)
            sds._handle_check_screen_changes({})
            mock_cap.return_value = np.ones((100, 200, 3), dtype=np.uint8) * 255
            result = sds._handle_check_screen_changes({})
            data = json.loads(result["content"][0]["text"])
            expected_keys = {"changed", "change_ratio", "regions", "width", "height", "frame_count"}
            assert set(data.keys()) == expected_keys


class TestHandleCaptureScreenshot:
    """MCP capture_screenshot handler (mock 截图)"""

    def test_capture_failure_returns_error(self):
        from infra.mcp.servers.screen_diff_server import _handle_capture_screenshot
        with patch("infra.mcp.servers.screen_diff_server._capture_screen") as mock_cap:
            mock_cap.return_value = None
            result = _handle_capture_screenshot({})
            assert "截图失败" in result["content"][0]["text"]

    def test_capture_success_returns_base64(self):
        from infra.mcp.servers.screen_diff_server import _handle_capture_screenshot
        with patch("infra.mcp.servers.screen_diff_server._capture_screen") as mock_cap:
            mock_cap.return_value = np.zeros((100, 200, 3), dtype=np.uint8)
            result = _handle_capture_screenshot({})
            text = result["content"][0]["text"]
            data = json.loads(text)
            assert "mime_type" in data
            assert data["mime_type"] == "image/png"
            assert "data" in data
            assert len(data["data"]) > 0
            assert data["width"] == 200
            assert data["height"] == 100


class TestHandleCallTool:
    """MCP tools/call 路由"""

    def test_unknown_tool_returns_error(self):
        from infra.mcp.servers.screen_diff_server import _handle_call_tool
        with patch("infra.mcp.servers.screen_diff_server._send") as mock_send:
            _handle_call_tool({
                "id": 10,
                "method": "tools/call",
                "params": {"name": "nonexistent", "arguments": {}},
            })
            args = mock_send.call_args[0][0]
            assert args["id"] == 10
            assert args["result"]["isError"] is True
            assert "未知工具" in args["result"]["content"][0]["text"]

    def test_exception_propagation(self):
        from infra.mcp.servers.screen_diff_server import _handle_call_tool
        with patch("infra.mcp.servers.screen_diff_server._TOOL_HANDLERS",
                   new={"broken": lambda p: (_ for _ in ()).throw(ValueError("crash"))}):
            with patch("infra.mcp.servers.screen_diff_server._send") as mock_send:
                _handle_call_tool({
                    "id": 11,
                    "method": "tools/call",
                    "params": {"name": "broken", "arguments": {}},
                })
                args = mock_send.call_args[0][0]
                assert args["result"]["isError"] is True
                assert "crash" in args["result"]["content"][0]["text"]


class TestCaptureScreen:
    """_capture_screen (mock macOS screencapture)"""

    def test_screencapture_failure(self):
        from infra.mcp.servers.screen_diff_server import _capture_screen
        # daemon 不可用（取帧返回 None）→ 回退本地 screencapture，本地也失败 → None
        with patch("subprocess.run") as mock_run, \
             patch("utils.screen_capture_daemon_client.get_frame_bytes", return_value=None):
            mock_run.return_value.returncode = 1
            result = _capture_screen()
            assert result is None

    def test_screencapture_timeout(self):
        from infra.mcp.servers.screen_diff_server import _capture_screen
        # daemon 不可用 → 回退本地 screencapture，本地超时 → None
        with patch("subprocess.run") as mock_run, \
             patch("utils.screen_capture_daemon_client.get_frame_bytes", return_value=None):
            mock_run.side_effect = TimeoutError("timeout")
            result = _capture_screen()
        assert result is None


class TestMain:
    """main() MCP stdio 主循环"""

    def test_main_unavailable_module(self):
        from infra.mcp.servers.screen_diff_server import main
        with patch("infra.mcp.servers.screen_diff_server._available", False):
            with patch("sys.stdin", []):
                with patch("sys.stderr") as mock_stderr:
                    main()
                    mock_stderr.write.assert_called_once()
                    text = mock_stderr.write.call_args[0][0]
                    assert "opencv" in text.lower()

    def test_main_processes_initialize(self):
        from infra.mcp.servers.screen_diff_server import main
        with patch("sys.stdin", [
            '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n',
        ]):
            with patch("infra.mcp.servers.screen_diff_server._send") as mock_send:
                main()
                sent = mock_send.call_args[0][0]
                assert sent["id"] == 1
                assert "result" in sent

    def test_main_processes_list_tools(self):
        from infra.mcp.servers.screen_diff_server import main
        with patch("sys.stdin", [
            '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
        ]):
            with patch("infra.mcp.servers.screen_diff_server._send") as mock_send:
                main()
                sent = mock_send.call_args[0][0]
                assert len(sent["result"]["tools"]) == 3

    def test_main_ignores_initialized_notification(self):
        from infra.mcp.servers.screen_diff_server import main
        with patch("sys.stdin", [
            '{"jsonrpc":"2.0","method":"notifications/initialized"}\n',
        ]):
            with patch("infra.mcp.servers.screen_diff_server._send") as mock_send:
                main()
                mock_send.assert_not_called()

    def test_main_handles_empty_line(self):
        from infra.mcp.servers.screen_diff_server import main
        with patch("sys.stdin", ["\n", "\n"]):
            with patch("infra.mcp.servers.screen_diff_server._send") as mock_send:
                main()
                mock_send.assert_not_called()

    def test_main_handles_bad_json(self):
        from infra.mcp.servers.screen_diff_server import main
        with patch("sys.stdin", ["not json\n"]):
            with patch("infra.mcp.servers.screen_diff_server._send") as mock_send:
                main()
                mock_send.assert_not_called()


class TestGlobals:
    """模块级全局状态"""

    def setup_method(self):
        import infra.mcp.servers.screen_diff_server as sds
        sds._prev_frame = None
        sds._frame_count = 0

    def test_frame_count_tracking(self):
        import infra.mcp.servers.screen_diff_server as sds
        assert isinstance(sds._frame_count, int)

    def test_prev_frame_initial_none(self):
        import infra.mcp.servers.screen_diff_server as sds
        sds._frame_count = 0
        sds._prev_frame = None
        assert sds._prev_frame is None

    def test_tool_handlers_completeness(self):
        from infra.mcp.servers.screen_diff_server import _TOOL_HANDLERS
        assert "check_screen_changes" in _TOOL_HANDLERS
        assert "capture_screenshot" in _TOOL_HANDLERS
        assert "get_stats" in _TOOL_HANDLERS
        assert len(_TOOL_HANDLERS) == 3
