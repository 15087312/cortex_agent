"""screen_monitor_server.py（MCP 屏幕监控 server）单元测试

mock 截图/OCR 边界，绝不真实截图（PIL 内存假图 + 注入 fake OCR）：
- _init / _send / _handle_initialize / _handle_list_tools
- _capture_screen: 禁用 / daemon 成功 / daemon 失败回退本地 / 本地截图失败 / 本地异常 / 宽图缩放
- _detect_elements: 空图 / OCR 结果解析（tuple/list/score 类型/低置信度/None text/短 item）/
  按钮检测（反二值化前景）/ 超大图缩放 / OCR 异常吞掉
- 三个工具 handler 成功/截图失败/OCR 不可用/区域裁剪/OCR 异常
- _handle_call_tool: 未知工具 / 成功 / 执行异常
- main(): 空行跳过 / 无效 JSON / initialize / notifications/initialized
"""
import io
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

# 测试环境不加载真实 OCR 引擎（避免 onnxruntime 双 OpenMP 死锁，见 tests/conftest.py）。
# conftest 的 block_real_native_libs 已全局置 rapidocr_onnxruntime=None，
# 这里不再模块级置 None（避免 §31 类顺序污染），_init() 自然走 ImportError，_ocr 为 None。

import infra.mcp.servers.screen_monitor_server as sms  # noqa: E402


def _png_bytes(size=(16, 12), color=(200, 100, 50)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


def _img(w=640, h=480, fill=200):
    return np.full((h, w, 3), fill, dtype=np.uint8)


def _req(id_, method, **kw):
    return {"jsonrpc": "2.0", "id": id_, "method": method, **kw}


class TestModuleState:
    def test_cv2_available(self):
        assert sms._available is True
        assert sms._cv2 is not None
        assert sms._MAX_ANALYZE_WIDTH == 1280


class TestSend:
    def test_send_writes_json_line(self, capsys):
        sms._send({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
        msg = json.loads(capsys.readouterr().out)
        assert msg["result"]["ok"] is True


class TestBasicHandlers:
    def test_initialize(self, capsys):
        sms._handle_initialize(_req(7, "initialize"))
        msg = json.loads(capsys.readouterr().out)
        assert msg["id"] == 7
        assert msg["result"]["serverInfo"]["name"] == "screen_monitor"
        assert msg["result"]["protocolVersion"] == "2024-11-05"

    def test_list_tools(self, capsys):
        sms._handle_list_tools(_req(8, "tools/list"))
        msg = json.loads(capsys.readouterr().out)
        names = {t["name"] for t in msg["result"]["tools"]}
        assert names == {"analyze_ui_elements", "capture_and_analyze", "extract_text_from_screen"}


class TestCaptureScreen:
    def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.setattr("utils.screen_capture.SCREENSHOT_ENABLED", False)
        assert sms._capture_screen() is None

    def test_daemon_success(self, monkeypatch):
        monkeypatch.setattr("utils.screen_capture.SCREENSHOT_ENABLED", True)
        monkeypatch.setattr(
            "utils.screen_capture_daemon_client.get_frame_bytes",
            lambda max_width=1280: _png_bytes(),
        )
        img = sms._capture_screen()
        assert img is not None
        assert img.shape[1] == 16
        assert img.shape[0] == 12

    def test_daemon_none_falls_back_local(self, monkeypatch):
        png = _png_bytes(size=(20, 10))
        calls = {"run": 0}

        def fake_run(cmd, **kw):
            calls["run"] += 1
            with open(cmd[-1], "wb") as f:
                f.write(png)
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr("utils.screen_capture.SCREENSHOT_ENABLED", True)
        monkeypatch.setattr("utils.screen_capture_daemon_client.get_frame_bytes", lambda max_width=1280: None)
        monkeypatch.setattr("infra.mcp.servers.screen_monitor_server.subprocess.run", fake_run)
        img = sms._capture_screen()
        assert calls["run"] == 1
        assert img is not None
        assert img.shape[1] == 20

    def test_daemon_exception_falls_back_local(self, monkeypatch):
        png = _png_bytes(size=(20, 10))

        def fake_run(cmd, **kw):
            with open(cmd[-1], "wb") as f:
                f.write(png)
            return SimpleNamespace(returncode=0)

        def bad_frame(max_width=1280):
            raise OSError("daemon socket boom")

        monkeypatch.setattr("utils.screen_capture.SCREENSHOT_ENABLED", True)
        monkeypatch.setattr("utils.screen_capture_daemon_client.get_frame_bytes", bad_frame)
        monkeypatch.setattr("infra.mcp.servers.screen_monitor_server.subprocess.run", fake_run)
        img = sms._capture_screen()
        assert img is not None

    def test_local_wide_image_resized(self, monkeypatch):
        png = _png_bytes(size=(2000, 50))

        def fake_run(cmd, **kw):
            with open(cmd[-1], "wb") as f:
                f.write(png)
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr("utils.screen_capture.SCREENSHOT_ENABLED", True)
        monkeypatch.setattr("utils.screen_capture_daemon_client.get_frame_bytes", lambda max_width=1280: None)
        monkeypatch.setattr("infra.mcp.servers.screen_monitor_server.subprocess.run", fake_run)
        img = sms._capture_screen()
        assert img is not None
        assert img.shape[1] == sms._MAX_ANALYZE_WIDTH

    def test_local_capture_failure(self, monkeypatch):
        monkeypatch.setattr("utils.screen_capture.SCREENSHOT_ENABLED", True)
        monkeypatch.setattr("utils.screen_capture_daemon_client.get_frame_bytes", lambda max_width=1280: None)
        monkeypatch.setattr(
            "infra.mcp.servers.screen_monitor_server.subprocess.run",
            lambda cmd, **kw: SimpleNamespace(returncode=1),
        )
        assert sms._capture_screen() is None

    def test_local_invalid_png_returns_none(self, monkeypatch):
        def fake_run(cmd, **kw):
            with open(cmd[-1], "wb") as f:
                f.write(b"not-a-real-png")
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr("utils.screen_capture.SCREENSHOT_ENABLED", True)
        monkeypatch.setattr("utils.screen_capture_daemon_client.get_frame_bytes", lambda max_width=1280: None)
        monkeypatch.setattr("infra.mcp.servers.screen_monitor_server.subprocess.run", fake_run)
        assert sms._capture_screen() is None

    def test_local_exception_returns_none(self, monkeypatch):
        monkeypatch.setattr("utils.screen_capture.SCREENSHOT_ENABLED", True)
        monkeypatch.setattr("utils.screen_capture_daemon_client.get_frame_bytes", lambda max_width=1280: None)

        def boom(cmd, **kw):
            raise RuntimeError("screencapture timeout")

        monkeypatch.setattr("infra.mcp.servers.screen_monitor_server.subprocess.run", boom)
        assert sms._capture_screen() is None


class TestDetectElements:
    def test_blank_image_no_elements(self):
        assert sms._detect_elements(_img(), detect_buttons=True, extract_text=True) == []

    def test_ocr_tuple_results_parsed(self, monkeypatch):
        fake_ocr = lambda enhanced: (  # noqa: E731
            [
                [[[10, 20], [100, 20], [100, 40], [10, 40]], "Hello", 0.9],
                [[5, 5, 50, 20], "Mid", "0.7"],
                [[0, 0, 10, 10], "low", 0.1],
                [[0, 0, 10, 10], None, 0.6],
                [[0, 0, 10, 10], "bad-score", "not-a-number"],
                [[0, 0, 10, 10], 0.9],  # len(item) < 3 → 跳过
                ["oops", "bad-box", 0.9],  # box 非 list → 跳过
            ],
            None,
        )
        monkeypatch.setattr(sms, "_ocr", fake_ocr)
        elements = sms._detect_elements(_img(), detect_buttons=False, confidence=0.5)
        texts = [e for e in elements if e["type"] == "text"]
        labels = {e["label"] for e in texts}
        assert "Hello" in labels
        assert "Mid" in labels
        assert "low" not in labels
        assert "bad-score" not in labels
        assert "" in labels  # text None → ""
        hello = next(e for e in texts if e["label"] == "Hello")
        assert hello["bbox"] == [10, 20, 100, 40]
        assert hello["center_x"] == 55
        assert hello["confidence"] == 0.9

    def test_ocr_plain_list_result(self, monkeypatch):
        fake_ocr = lambda enhanced: [[[[0, 0], [10, 10], [10, 0], [0, 10]], "Plain", 0.8]]
        monkeypatch.setattr(sms, "_ocr", fake_ocr)
        elements = sms._detect_elements(_img(), detect_buttons=False, confidence=0.5)
        assert any(e["label"] == "Plain" for e in elements)

    def test_ocr_exception_swallowed(self, monkeypatch):
        def bad_ocr(enhanced):
            raise ValueError("ocr crash")

        monkeypatch.setattr(sms, "_ocr", bad_ocr)
        assert sms._detect_elements(_img(), detect_buttons=False) == []

    def test_ocr_result_falsy_skips_text(self, monkeypatch):
        monkeypatch.setattr(sms, "_ocr", lambda enhanced: None)
        assert sms._detect_elements(_img(), detect_buttons=False) == []

    def test_ocr_empty_items_skips_text(self, monkeypatch):
        monkeypatch.setattr(sms, "_ocr", lambda enhanced: (None,))
        assert sms._detect_elements(_img(), detect_buttons=False) == []

    def test_button_detection(self):
        img = np.full((200, 200, 3), 255, dtype=np.uint8)
        img[50:100, 60:160] = 100  # 暗色矩形 → 反二值化后为前景轮廓
        elements = sms._detect_elements(img, detect_buttons=True, extract_text=False)
        buttons = [e for e in elements if e["type"] == "button"]
        assert buttons
        b = buttons[0]
        assert b["bbox"][2] - b["bbox"][0] == 100
        assert b["bbox"][3] - b["bbox"][1] == 50
        assert b["confidence"] == 0.5

    def test_extreme_aspect_button_skipped(self):
        img = np.full((500, 500, 3), 255, dtype=np.uint8)
        img[100:116, 50:450] = 100  # 400x16 超扁矩形 → aspect 25 > 5 → 跳过
        elements = sms._detect_elements(img, detect_buttons=True, extract_text=False)
        assert not any(e["type"] == "button" for e in elements)

    def test_wide_image_resized_before_analysis(self, monkeypatch):
        monkeypatch.setattr(sms, "_ocr", None)
        img = _img(w=2000, h=100, fill=255)
        assert sms._detect_elements(img, detect_buttons=False) == []


class TestAnalyzeUiElements:
    def test_screenshot_failure(self, monkeypatch):
        monkeypatch.setattr(sms, "_capture_screen", lambda: None)
        assert sms._handle_analyze_ui_elements({})["content"][0]["text"] == "截图失败"

    def test_renders_elements(self, monkeypatch):
        monkeypatch.setattr(sms, "_capture_screen", lambda: _img())
        monkeypatch.setattr(sms, "_detect_elements", lambda *a, **k: [
            {"type": "text", "label": "OK", "bbox": [1, 2, 3, 4], "confidence": 0.9},
            {"type": "button", "label": "", "bbox": [0, 0, 50, 20], "confidence": 0.5},
        ])
        result = sms._handle_analyze_ui_elements(
            {"detect_buttons": True, "extract_text": True, "confidence_threshold": 0.5}
        )
        text = result["content"][0]["text"]
        assert "截图大小: 640x480" in text
        assert "检测到 2 个元素" in text
        assert "OK" in text


class TestCaptureAndAnalyze:
    def test_screenshot_failure(self, monkeypatch):
        monkeypatch.setattr(sms, "_capture_screen", lambda: None)
        assert sms._handle_capture_and_analyze({})["content"][0]["text"] == "截图失败"

    def test_summary(self, monkeypatch):
        monkeypatch.setattr(sms, "_capture_screen", lambda: _img())
        monkeypatch.setattr(sms, "_detect_elements", lambda *a, **k: [
            {"type": "text", "label": "Label1"},
            {"type": "text", "label": "Label2"},
            {"type": "button", "label": ""},
        ])
        result = sms._handle_capture_and_analyze({"analysis_prompt": "describe"})
        text = result["content"][0]["text"]
        assert "describe" in text
        assert "分辨率: 640x480" in text
        assert "文字区域: 2 处" in text
        assert "按钮/可交互区域: 1 处" in text
        assert "Label1" in text and "Label2" in text

    def test_summary_no_text_elements(self, monkeypatch):
        monkeypatch.setattr(sms, "_capture_screen", lambda: _img())
        monkeypatch.setattr(sms, "_detect_elements", lambda *a, **k: [])
        result = sms._handle_capture_and_analyze({})
        text = result["content"][0]["text"]
        assert "检测到的文字" not in text


class TestExtractText:
    def test_screenshot_failure(self, monkeypatch):
        monkeypatch.setattr(sms, "_capture_screen", lambda: None)
        assert sms._handle_extract_text({})["content"][0]["text"] == "截图失败"

    def test_ocr_unavailable(self, monkeypatch):
        monkeypatch.setattr(sms, "_capture_screen", lambda: _img())
        monkeypatch.setattr(sms, "_ocr", None)
        result = sms._handle_extract_text({})
        assert "OCR 引擎不可用" in result["content"][0]["text"]

    def test_region_crop_and_extract(self, monkeypatch):
        monkeypatch.setattr(sms, "_capture_screen", lambda: _img(w=100, h=100))
        fake_ocr = lambda enhanced: (  # noqa: E731
            [
                [[[0, 0], [20, 20], [20, 0], [0, 20]], "RegionText", 0.9],
                [[0, 0, 10, 10], None, 0.9],  # text None → 跳过
                [[0, 0, 10, 10]],  # len < 3 → 跳过
            ],
        )
        monkeypatch.setattr(sms, "_ocr", fake_ocr)
        result = sms._handle_extract_text(
            {"region": {"x": 10, "y": 10, "width": 20, "height": 20}}
        )
        assert "RegionText" in result["content"][0]["text"]
        assert "None" not in result["content"][0]["text"]

    def test_no_text_detected(self, monkeypatch):
        monkeypatch.setattr(sms, "_capture_screen", lambda: _img())
        monkeypatch.setattr(sms, "_ocr", lambda enhanced: None)
        assert sms._handle_extract_text({})["content"][0]["text"] == "未检测到文字"

    def test_empty_result_items(self, monkeypatch):
        monkeypatch.setattr(sms, "_capture_screen", lambda: _img())
        monkeypatch.setattr(sms, "_ocr", lambda enhanced: (None,))
        assert sms._handle_extract_text({})["content"][0]["text"] == "未检测到文字"

    def test_ocr_exception_reported(self, monkeypatch):
        monkeypatch.setattr(sms, "_capture_screen", lambda: _img())

        def bad_ocr(enhanced):
            raise ValueError("ocr crash")

        monkeypatch.setattr(sms, "_ocr", bad_ocr)
        result = sms._handle_extract_text({})
        assert "OCR 失败" in result["content"][0]["text"]


class TestCallTool:
    def test_unknown_tool(self, capsys):
        sms._handle_call_tool(_req(1, "tools/call", params={"name": "nope", "arguments": {}}))
        msg = json.loads(capsys.readouterr().out)
        assert msg["result"]["isError"] is True
        assert "未知工具" in msg["result"]["content"][0]["text"]

    def test_success(self, monkeypatch, capsys):
        monkeypatch.setitem(
            sms._TOOL_HANDLERS, "analyze_ui_elements",
            lambda params: {"content": [{"type": "text", "text": "ok"}]},
        )
        sms._handle_call_tool(
            _req(2, "tools/call", params={"name": "analyze_ui_elements", "arguments": {}})
        )
        msg = json.loads(capsys.readouterr().out)
        assert msg["id"] == 2
        assert msg["result"]["content"][0]["text"] == "ok"

    def test_handler_exception(self, monkeypatch, capsys):
        def boom(params):
            raise ValueError("handler boom")

        monkeypatch.setitem(sms._TOOL_HANDLERS, "analyze_ui_elements", boom)
        sms._handle_call_tool(
            _req(3, "tools/call", params={"name": "analyze_ui_elements", "arguments": {}})
        )
        msg = json.loads(capsys.readouterr().out)
        assert msg["result"]["isError"] is True
        assert "handler boom" in msg["result"]["content"][0]["text"]


class TestMain:
    def test_not_available(self, monkeypatch, capsys):
        monkeypatch.setattr(sms, "_available", False)
        sms.main()
        assert "opencv-python" in capsys.readouterr().err

    def test_skips_blank_and_invalid_json(self, monkeypatch, capsys):
        lines = [
            "\n",
            "not-json\n",
            json.dumps(_req(1, "initialize")),
            json.dumps(_req(2, "notifications/initialized")),
            json.dumps(_req(3, "tools/list")),
            json.dumps(_req(4, "tools/call", params={"name": "nope", "arguments": {}})),
            json.dumps(_req(5, "some/unknown-method")),
        ]
        monkeypatch.setattr(sms.sys, "stdin", iter(lines))
        sms.main()
        msgs = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
        assert len(msgs) == 3  # initialize + tools/list + tools/call；invalid/unknown 跳过、initialized 无响应
        assert msgs[0]["result"]["serverInfo"]["name"] == "screen_monitor"
        assert {t["name"] for t in msgs[1]["result"]["tools"]} & {"analyze_ui_elements"}
        assert msgs[2]["result"]["isError"] is True
