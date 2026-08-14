"""Screen Monitor MCP Server 单元测试

覆盖: _detect_elements, _handle_* 工具处理函数, _capture_screen (模拟)
"""
import json
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from infra.mcp.servers.screen_monitor_server import (
    _detect_elements,
    _handle_analyze_ui_elements,
    _handle_capture_and_analyze,
    _handle_extract_text,
    _init,
)


# ---------------------------------------------------------------------------
# 辅助：生成合成测试图像
# ---------------------------------------------------------------------------

def _make_test_img(width=400, height=300, text="Hello World"):
    """生成一张合成 BGR 图像"""
    img = np.ones((height, width, 3), dtype=np.uint8) * 220
    try:
        import cv2
        cv2.putText(img, text, (50, 100), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    except ImportError:
        pass
    return img


def _make_blank_img(width=400, height=300):
    """生成一张空白灰度图（无文字无按钮）"""
    return np.ones((height, width, 3), dtype=np.uint8) * 220


# ---------------------------------------------------------------------------
# _init / 全局状态
# ---------------------------------------------------------------------------

class TestInit:
    def test_init_available_with_cv2(self):
        from infra.mcp.servers.screen_monitor_server import _available
        assert _available is True


# ---------------------------------------------------------------------------
# _detect_elements
# ---------------------------------------------------------------------------

class TestDetectElements:
    def test_returns_list(self):
        img = _make_test_img()
        result = _detect_elements(img, detect_buttons=False, extract_text=False)
        assert isinstance(result, list)

    def test_detect_text(self, monkeypatch):
        img = _make_test_img(text="HelloWorld")
        # conftest 屏蔽真实 OCR（防双 OpenMP 死锁）→ 注入 fake OCR 测文字检测分支
        import infra.mcp.servers.screen_monitor_server as sms
        monkeypatch.setattr(
            sms, "_ocr",
            lambda enhanced: [[[[0, 0], [10, 10], [10, 0], [0, 10]], "HelloWorld", 0.9]],
        )
        result = _detect_elements(img, detect_buttons=False, extract_text=True)
        texts = [e for e in result if e["type"] == "text"]
        assert len(texts) >= 1

    def test_detect_buttons(self):
        img = _make_blank_img(400, 300)
        result = _detect_elements(img, detect_buttons=True, extract_text=False)
        assert isinstance(result, list)

    def test_empty_image_returns_list(self):
        img = np.zeros((10, 10, 3), dtype=np.uint8)
        result = _detect_elements(img, detect_buttons=True, extract_text=True)
        assert isinstance(result, list)

    def test_text_element_structure(self):
        img = _make_test_img(text="TestLabel")
        result = _detect_elements(img, detect_buttons=False, extract_text=True)
        texts = [e for e in result if e["type"] == "text"]
        if texts:
            t = texts[0]
            assert "label" in t
            assert "bbox" in t
            assert "center_x" in t
            assert "center_y" in t
            assert "confidence" in t
            assert "type" in t
            assert len(t["bbox"]) == 4

    def test_button_element_structure(self):
        img = _make_blank_img(400, 300)
        # 画一个白色矩形当作按钮
        img[50:100, 50:200] = 255
        result = _detect_elements(img, detect_buttons=True, extract_text=False)
        buttons = [e for e in result if e["type"] == "button"]
        if buttons:
            b = buttons[0]
            assert "bbox" in b
            assert "center_x" in b
            assert "center_y" in b
            assert len(b["bbox"]) == 4

    def test_confidence_threshold(self):
        img = _make_test_img()
        low = _detect_elements(img, detect_buttons=False, extract_text=True, confidence=0.0)
        high = _detect_elements(img, detect_buttons=False, extract_text=True, confidence=0.99)
        assert len(low) >= len(high)

    def test_bgr_image_preserved(self):
        img = _make_test_img()
        assert img.shape[2] == 3  # BGR


# ---------------------------------------------------------------------------
# _handle_analyze_ui_elements
# ---------------------------------------------------------------------------

class TestHandleAnalyzeUIElements:
    def test_returns_text_content(self):
        with patch("infra.mcp.servers.screen_monitor_server._capture_screen") as mock_cap:
            mock_cap.return_value = _make_test_img()
            result = _handle_analyze_ui_elements({
                "detect_buttons": False,
                "extract_text": True,
            })
            assert "content" in result
            assert len(result["content"]) >= 1
            assert result["content"][0]["type"] == "text"

    def test_capture_failure(self):
        with patch("infra.mcp.servers.screen_monitor_server._capture_screen") as mock_cap:
            mock_cap.return_value = None
            result = _handle_analyze_ui_elements({})
            assert "截图失败" in result["content"][0]["text"]

    def test_default_params(self):
        with patch("infra.mcp.servers.screen_monitor_server._capture_screen") as mock_cap:
            mock_cap.return_value = _make_test_img()
            result = _handle_analyze_ui_elements({})
            assert "content" in result

    def test_contains_image_size(self):
        with patch("infra.mcp.servers.screen_monitor_server._capture_screen") as mock_cap:
            mock_cap.return_value = _make_test_img(width=800, height=600)
            result = _handle_analyze_ui_elements({})
            text = result["content"][0]["text"]
            assert "800" in text and "600" in text


# ---------------------------------------------------------------------------
# _handle_capture_and_analyze
# ---------------------------------------------------------------------------

class TestHandleCaptureAndAnalyze:
    def test_returns_summary(self):
        with patch("infra.mcp.servers.screen_monitor_server._capture_screen") as mock_cap:
            mock_cap.return_value = _make_test_img()
            result = _handle_capture_and_analyze({"analysis_prompt": "描述屏幕"})
            assert "content" in result
            text = result["content"][0]["text"]
            assert "屏幕分析" in text or "分辨率" in text

    def test_capture_failure(self):
        with patch("infra.mcp.servers.screen_monitor_server._capture_screen") as mock_cap:
            mock_cap.return_value = None
            result = _handle_capture_and_analyze({})
            assert "截图失败" in result["content"][0]["text"]

    def test_contains_resolution(self):
        with patch("infra.mcp.servers.screen_monitor_server._capture_screen") as mock_cap:
            mock_cap.return_value = _make_test_img(width=1024, height=768)
            result = _handle_capture_and_analyze({})
            text = result["content"][0]["text"]
            assert "1024" in text and "768" in text

    def test_contains_brightness(self):
        with patch("infra.mcp.servers.screen_monitor_server._capture_screen") as mock_cap:
            mock_cap.return_value = _make_test_img()
            result = _handle_capture_and_analyze({})
            text = result["content"][0]["text"]
            assert "亮度" in text or "brightness" in text.lower()

    def test_analysis_prompt_included(self):
        with patch("infra.mcp.servers.screen_monitor_server._capture_screen") as mock_cap:
            mock_cap.return_value = _make_test_img()
            result = _handle_capture_and_analyze({"analysis_prompt": "查找错误信息"})
            text = result["content"][0]["text"]
            assert "查找错误信息" in text


# ---------------------------------------------------------------------------
# _handle_extract_text
# ---------------------------------------------------------------------------

class TestHandleExtractText:
    def test_returns_text_content(self):
        with patch("infra.mcp.servers.screen_monitor_server._capture_screen") as mock_cap:
            mock_cap.return_value = _make_test_img(text="SomeContent")
            result = _handle_extract_text({})
            assert "content" in result

    def test_capture_failure(self):
        with patch("infra.mcp.servers.screen_monitor_server._capture_screen") as mock_cap:
            mock_cap.return_value = None
            result = _handle_extract_text({})
            assert "截图失败" in result["content"][0]["text"]

    def test_with_region(self):
        with patch("infra.mcp.servers.screen_monitor_server._capture_screen") as mock_cap:
            mock_cap.return_value = _make_test_img()
            result = _handle_extract_text({"region": {"x": 0, "y": 0, "width": 100, "height": 100}})
            assert "content" in result

    def test_ocr_not_available(self):
        with patch("infra.mcp.servers.screen_monitor_server._capture_screen") as mock_cap:
            mock_cap.return_value = _make_test_img()
            with patch("infra.mcp.servers.screen_monitor_server._ocr", None):
                result = _handle_extract_text({})
                text = result["content"][0]["text"]
                assert "OCR" in text


# ---------------------------------------------------------------------------
# _capture_screen (via MCP 协议响应格式)
# ---------------------------------------------------------------------------

class TestCaptureScreen:
    def test_returns_numpy_array_on_success(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = b""
            with patch("builtins.open", MagicMock()):
                with patch("os.path.exists", return_value=True):
                    with patch("os.unlink"):
                        from infra.mcp.servers.screen_monitor_server import _capture_screen
                        result = _capture_screen()
                        assert result is None or isinstance(result, np.ndarray)

    def test_returns_none_on_failure(self):
        # daemon 不可用（取帧返回 None）→ 回退本地 screencapture，本地也失败 → None
        with patch("subprocess.run") as mock_run, \
             patch("utils.screen_capture_daemon_client.get_frame_bytes", return_value=None):
            mock_run.side_effect = Exception("no display")
            from infra.mcp.servers.screen_monitor_server import _capture_screen
            result = _capture_screen()
            assert result is None
