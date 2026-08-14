"""screen_diff_server.py（MCP 屏幕帧差 server）单元测试

mock 截图边界，绝不真实截图（PIL 内存假图），真实 cv2/numpy 计算帧差：
- _send / _handle_initialize / _handle_list_tools
- _capture_screen: 禁用 / daemon 成功(cv2 + PIL) / daemon 失败回退本地 / 本地失败 / 本地异常 / 宽图缩放
- _compute_frame_diff: 形状不同 / cv2 3D 变化与无变化 / cv2 2D / numpy 降级 3D 与 2D
- check_screen_changes: 截图失败 / 首帧 / 第二帧帧差
- capture_screenshot: 截图失败 / cv2 编码 / PIL 编码
- get_stats / _handle_call_tool / main
"""
import base64
import io
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

# 测试环境不加载真实 OCR/onnx 引擎（见 tests/conftest.py），这里仅保证模块自身 import 干净。
sys.modules["rapidocr_onnxruntime"] = None

import infra.mcp.servers.screen_diff_server as dss  # noqa: E402


def _png_bytes(size=(16, 12), color=(200, 100, 50)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


def _req(id_, method, **kw):
    return {"jsonrpc": "2.0", "id": id_, "method": method, **kw}


@pytest.fixture(autouse=True)
def _reset_frame_buffer():
    prev, count = dss._prev_frame, dss._frame_count
    dss._prev_frame = None
    dss._frame_count = 0
    yield
    dss._prev_frame = prev
    dss._frame_count = count


class TestModuleState:
    def test_cv2_available(self):
        assert dss._available is True
        assert dss._cv2 is not None
        assert dss._MAX_ANALYZE_WIDTH == 1280


class TestSend:
    def test_send_writes_json_line(self, capsys):
        dss._send({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
        msg = json.loads(capsys.readouterr().out)
        assert msg["result"]["ok"] is True


class TestBasicHandlers:
    def test_initialize(self, capsys):
        dss._handle_initialize(_req(7, "initialize"))
        msg = json.loads(capsys.readouterr().out)
        assert msg["id"] == 7
        assert msg["result"]["serverInfo"]["name"] == "screen_diff"

    def test_list_tools(self, capsys):
        dss._handle_list_tools(_req(8, "tools/list"))
        msg = json.loads(capsys.readouterr().out)
        names = {t["name"] for t in msg["result"]["tools"]}
        assert names == {"check_screen_changes", "capture_screenshot", "get_stats"}


class TestCaptureScreen:
    def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.setattr("utils.screen_capture.SCREENSHOT_ENABLED", False)
        assert dss._capture_screen() is None

    def test_daemon_success_cv2(self, monkeypatch):
        monkeypatch.setattr("utils.screen_capture.SCREENSHOT_ENABLED", True)
        monkeypatch.setattr(
            "utils.screen_capture_daemon_client.get_frame_bytes",
            lambda max_width=1280: _png_bytes(),
        )
        img = dss._capture_screen()
        assert img is not None
        assert img.shape[1] == 16 and img.shape[0] == 12

    def test_daemon_success_pil(self, monkeypatch):
        monkeypatch.setattr(dss, "_cv2", None)
        monkeypatch.setattr("utils.screen_capture.SCREENSHOT_ENABLED", True)
        monkeypatch.setattr(
            "utils.screen_capture_daemon_client.get_frame_bytes",
            lambda max_width=1280: _png_bytes(),
        )
        img = dss._capture_screen()
        assert img is not None
        assert img.shape[1] == 16 and img.shape[0] == 12

    def test_daemon_none_falls_back_local(self, monkeypatch):
        calls = {"run": 0}

        def fake_run(cmd, **kw):
            calls["run"] += 1
            with open(cmd[-1], "wb") as f:
                f.write(_png_bytes(size=(20, 10)))
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr("utils.screen_capture.SCREENSHOT_ENABLED", True)
        monkeypatch.setattr("utils.screen_capture_daemon_client.get_frame_bytes", lambda max_width=1280: None)
        monkeypatch.setattr("infra.mcp.servers.screen_diff_server.subprocess.run", fake_run)
        img = dss._capture_screen()
        assert calls["run"] == 1
        assert img is not None
        assert img.shape[1] == 20

    def test_daemon_exception_falls_back_local(self, monkeypatch):
        def bad_frame(max_width=1280):
            raise OSError("daemon socket boom")

        def fake_run(cmd, **kw):
            with open(cmd[-1], "wb") as f:
                f.write(_png_bytes(size=(20, 10)))
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr("utils.screen_capture.SCREENSHOT_ENABLED", True)
        monkeypatch.setattr("utils.screen_capture_daemon_client.get_frame_bytes", bad_frame)
        monkeypatch.setattr("infra.mcp.servers.screen_diff_server.subprocess.run", fake_run)
        img = dss._capture_screen()
        assert img is not None

    def test_local_pil_fallback(self, monkeypatch):
        monkeypatch.setattr(dss, "_cv2", None)

        def fake_run(cmd, **kw):
            with open(cmd[-1], "wb") as f:
                f.write(_png_bytes(size=(20, 10)))
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr("utils.screen_capture.SCREENSHOT_ENABLED", True)
        monkeypatch.setattr("utils.screen_capture_daemon_client.get_frame_bytes", lambda max_width=1280: None)
        monkeypatch.setattr("infra.mcp.servers.screen_diff_server.subprocess.run", fake_run)
        img = dss._capture_screen()
        assert img is not None
        assert img.shape[1] == 20

    def test_local_wide_image_resized(self, monkeypatch):
        def fake_run(cmd, **kw):
            with open(cmd[-1], "wb") as f:
                f.write(_png_bytes(size=(2000, 50)))
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr("utils.screen_capture.SCREENSHOT_ENABLED", True)
        monkeypatch.setattr("utils.screen_capture_daemon_client.get_frame_bytes", lambda max_width=1280: None)
        monkeypatch.setattr("infra.mcp.servers.screen_diff_server.subprocess.run", fake_run)
        img = dss._capture_screen()
        assert img is not None
        assert img.shape[1] == dss._MAX_ANALYZE_WIDTH

    def test_local_capture_failure(self, monkeypatch):
        monkeypatch.setattr("utils.screen_capture.SCREENSHOT_ENABLED", True)
        monkeypatch.setattr("utils.screen_capture_daemon_client.get_frame_bytes", lambda max_width=1280: None)
        monkeypatch.setattr(
            "infra.mcp.servers.screen_diff_server.subprocess.run",
            lambda cmd, **kw: SimpleNamespace(returncode=1),
        )
        assert dss._capture_screen() is None

    def test_local_exception_returns_none(self, monkeypatch):
        monkeypatch.setattr("utils.screen_capture.SCREENSHOT_ENABLED", True)
        monkeypatch.setattr("utils.screen_capture_daemon_client.get_frame_bytes", lambda max_width=1280: None)

        def boom(cmd, **kw):
            raise RuntimeError("screencapture timeout")

        monkeypatch.setattr("infra.mcp.servers.screen_diff_server.subprocess.run", boom)
        assert dss._capture_screen() is None


class TestComputeFrameDiff:
    def test_shape_mismatch(self):
        cur = np.zeros((10, 10, 3), dtype=np.uint8)
        prev = np.zeros((20, 20, 3), dtype=np.uint8)
        res = dss._compute_frame_diff(cur, prev)
        assert res["has_changed"] is True
        assert res["change_ratio"] == 1.0
        assert res["changed_regions"] == []
        assert res["width"] == 10 and res["height"] == 10

    def test_cv2_3d_change_detected(self):
        cur = np.full((80, 80, 3), 120, dtype=np.uint8)
        prev = cur.copy()
        prev[30:50, 30:50] = 10
        res = dss._compute_frame_diff(cur, prev)
        assert res["has_changed"] is True
        assert res["width"] == 80 and res["height"] == 80
        assert res["changed_regions"]
        assert res["change_ratio"] >= 0.01

    def test_cv2_3d_identical_no_change(self):
        cur = np.full((80, 80, 3), 120, dtype=np.uint8)
        prev = cur.copy()
        res = dss._compute_frame_diff(cur, prev)
        assert res["has_changed"] is False
        assert res["changed_regions"] == []

    def test_cv2_2d_identical(self):
        cur = np.full((40, 40), 50, dtype=np.uint8)
        prev = cur.copy()
        res = dss._compute_frame_diff(cur, prev)
        assert res["has_changed"] is False

    def test_numpy_fallback_3d_change(self, monkeypatch):
        monkeypatch.setattr(dss, "_cv2", None)
        cur = np.full((50, 50, 3), 100, dtype=np.uint8)
        prev = np.full((50, 50, 3), 100, dtype=np.uint8)
        prev[10:20, 10:20] = 0
        res = dss._compute_frame_diff(cur, prev)
        assert bool(res["has_changed"]) is True
        assert res["change_ratio"] >= 0.01
        assert res["changed_regions"]

    def test_numpy_fallback_2d_no_change(self, monkeypatch):
        monkeypatch.setattr(dss, "_cv2", None)
        cur = np.full((40, 40), 50, dtype=np.uint8)
        prev = cur.copy()
        res = dss._compute_frame_diff(cur, prev)
        assert bool(res["has_changed"]) is False
        assert res["changed_regions"] == []


class TestCheckScreenChanges:
    def test_screenshot_failure(self, monkeypatch):
        monkeypatch.setattr(dss, "_capture_screen", lambda: None)
        r = dss._handle_check_screen_changes({})
        assert json.loads(r["content"][0]["text"])["error"] == "截图失败"

    def test_first_frame_returns_full_change(self, monkeypatch):
        monkeypatch.setattr(dss, "_capture_screen", lambda: np.zeros((10, 10, 3), dtype=np.uint8))
        r = dss._handle_check_screen_changes({})
        data = json.loads(r["content"][0]["text"])
        assert data["changed"] is True
        assert data["change_ratio"] == 1.0
        assert data["frame_count"] == 1
        assert dss._prev_frame is not None

    def test_second_frame_diff(self, monkeypatch):
        dss._frame_count = 1
        dss._prev_frame = np.zeros((50, 50, 3), dtype=np.uint8)

        def fake_capture():
            cur = np.zeros((50, 50, 3), dtype=np.uint8)
            cur[20:30, 20:30] = 255
            return cur

        monkeypatch.setattr(dss, "_capture_screen", fake_capture)
        r = dss._handle_check_screen_changes({})
        data = json.loads(r["content"][0]["text"])
        assert data["changed"] is True
        assert data["frame_count"] == 2
        assert data["width"] == 50 and data["height"] == 50


class TestCaptureScreenshot:
    def test_screenshot_failure(self, monkeypatch):
        monkeypatch.setattr(dss, "_capture_screen", lambda: None)
        assert dss._handle_capture_screenshot({})["content"][0]["text"] == "截图失败"

    def test_cv2_encode(self, monkeypatch):
        monkeypatch.setattr(dss, "_capture_screen", lambda: np.zeros((8, 8, 3), dtype=np.uint8))
        r = dss._handle_capture_screenshot({})
        data = json.loads(r["content"][0]["text"])
        assert data["mime_type"] == "image/png"
        assert base64.b64decode(data["data"]).startswith(b"\x89PNG")
        assert data["width"] == 8 and data["height"] == 8

    def test_pil_encode(self, monkeypatch):
        monkeypatch.setattr(dss, "_cv2", None)
        monkeypatch.setattr(dss, "_capture_screen", lambda: np.zeros((8, 8, 3), dtype=np.uint8))
        r = dss._handle_capture_screenshot({})
        data = json.loads(r["content"][0]["text"])
        assert data["mime_type"] == "image/png"
        assert base64.b64decode(data["data"]).startswith(b"\x89PNG")
        assert data["width"] == 8


class TestGetStats:
    def test_with_frame_state(self):
        dss._frame_count = 5
        dss._prev_frame = np.zeros((4, 4, 3), dtype=np.uint8)
        data = json.loads(dss._handle_get_stats({})["content"][0]["text"])
        assert data["frame_count"] == 5
        assert data["has_previous_frame"] is True

    def test_initial_state(self):
        data = json.loads(dss._handle_get_stats({})["content"][0]["text"])
        assert data["frame_count"] == 0
        assert data["has_previous_frame"] is False


class TestCallTool:
    def test_unknown_tool(self, capsys):
        dss._handle_call_tool(_req(1, "tools/call", params={"name": "nope", "arguments": {}}))
        msg = json.loads(capsys.readouterr().out)
        assert msg["result"]["isError"] is True
        assert "未知工具" in msg["result"]["content"][0]["text"]

    def test_success(self, monkeypatch, capsys):
        monkeypatch.setitem(
            dss._TOOL_HANDLERS, "get_stats",
            lambda params: {"content": [{"type": "text", "text": "ok"}]},
        )
        dss._handle_call_tool(_req(2, "tools/call", params={"name": "get_stats", "arguments": {}}))
        msg = json.loads(capsys.readouterr().out)
        assert msg["id"] == 2
        assert msg["result"]["content"][0]["text"] == "ok"

    def test_handler_exception(self, monkeypatch, capsys):
        def boom(params):
            raise ValueError("handler boom")

        monkeypatch.setitem(dss._TOOL_HANDLERS, "get_stats", boom)
        dss._handle_call_tool(_req(3, "tools/call", params={"name": "get_stats", "arguments": {}}))
        msg = json.loads(capsys.readouterr().out)
        assert msg["result"]["isError"] is True
        assert "handler boom" in msg["result"]["content"][0]["text"]


class TestMain:
    def test_not_available(self, monkeypatch, capsys):
        monkeypatch.setattr(dss, "_available", False)
        dss.main()
        assert "opencv-python" in capsys.readouterr().err

    def test_skips_blank_and_invalid_json(self, monkeypatch, capsys):
        times = [0.0, 0.0, 0.0, 0.0, 300.0, 300.0]

        def fake_time():
            return times.pop(0)

        monkeypatch.setattr(dss.time, "time", fake_time)
        lines = [
            "\n",
            "not-json\n",
            json.dumps(_req(1, "initialize")),
            json.dumps(_req(2, "notifications/initialized")),
            json.dumps(_req(3, "tools/list")),
            json.dumps(_req(4, "tools/call", params={"name": "nope", "arguments": {}})),
            json.dumps(_req(5, "some/unknown-method")),
        ]
        monkeypatch.setattr(dss.sys, "stdin", iter(lines))
        dss.main()
        msgs = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
        assert len(msgs) == 3
        assert msgs[0]["result"]["serverInfo"]["name"] == "screen_diff"
        assert {t["name"] for t in msgs[1]["result"]["tools"]} & {"get_stats"}
        assert msgs[2]["result"]["isError"] is True
