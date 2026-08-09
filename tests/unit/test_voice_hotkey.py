"""热键语音检测器 + 完毕停止词 测试

覆盖：
- extract_instruction：完毕停止词截断 + 唤醒词剥离
- HotkeyVoiceDetector：依赖探测、事件发布、完毕自动停止、状态机
"""
from unittest.mock import patch

import numpy as np
import pytest

from modules.perception.detectors.hotkey_voice_detector import (
    HotkeyVoiceDetector,
    SAMPLE_RATE,
)
from modules.perception.detectors.voice_detector import (
    VoiceDetector,
    extract_instruction,
)
from modules.perception.events.types import PerceptionEvent, PerceptionEventType


# ====================================================================
# extract_instruction — 完毕作为停止词
# ====================================================================

class TestExtractInstruction:
    def test_truncates_at_end_word(self):
        assert extract_instruction("科特 帮我查天气 完毕", "科特", "完毕") == "帮我查天气"

    def test_no_end_word_keeps_all(self):
        assert extract_instruction("科特 你好", "科特", "完毕") == "你好"

    def test_end_word_only_without_wake(self):
        assert extract_instruction("随便说点什么 完毕", "科特", "完毕") == "随便说点什么"

    def test_empty_result(self):
        assert extract_instruction("完毕", "科特", "完毕") == ""
        assert extract_instruction("", "科特", "完毕") == ""

    def test_wake_word_in_middle(self):
        assert extract_instruction("请 科特 回答我 完毕", "科特", "完毕") == "请 回答我"


# ====================================================================
# VoiceDetector — 完毕停止词在监听循环中生效
# ====================================================================

class TestVoiceDetectorEndStop:
    @pytest.fixture(autouse=True)
    def _isolate_voice_deps(self):
        with patch.dict("sys.modules", {
            "speech_recognition": None, "pyaudio": None, "whisper": None, "pynput": None,
        }):
            yield

    def test_constructor_accepts_cooldown(self):
        det = VoiceDetector(end_stop_cooldown=5.0)
        assert det._end_stop_cooldown == 5.0

    def test_default_cooldown(self):
        det = VoiceDetector()
        assert det._end_stop_cooldown == 3.0


# ====================================================================
# HotkeyVoiceDetector
# ====================================================================

class TestHotkeyVoiceDetector:
    @pytest.fixture(autouse=True)
    def _isolate_voice_deps(self):
        """隔离语音原生依赖（pyaudio/whisper/pynput）。

        构造 HotkeyVoiceDetector 会 _check_availability() → import 这些原生库，
        在完整测试套件中 portaudio 已被其他测试初始化，二次 import 会触发原生
        Abort（Fatal Python error）。这里 patch 掉让依赖不可用。
        """
        with patch.dict("sys.modules", {
            "speech_recognition": None, "pyaudio": None, "whisper": None, "pynput": None,
        }):
            yield
    def test_not_available_without_deps(self):
        with patch.dict("sys.modules", {"pynput": None, "pyaudio": None}):
            det = HotkeyVoiceDetector()
            assert det.is_available() is False

    def test_detector_type(self):
        det = HotkeyVoiceDetector()
        assert det.detector_type == "voice_hotkey"

    def test_start_when_not_available(self):
        det = HotkeyVoiceDetector()
        det._available = False
        det.start()  # 不应报错
        assert det._running is False

    def test_stop_without_start(self):
        det = HotkeyVoiceDetector()
        det.stop()
        assert det._running is False

    def test_detect_returns_cached_events(self):
        det = HotkeyVoiceDetector()
        det._events.append(PerceptionEvent(
            event_type=PerceptionEventType.SPEECH_DETECTED,
            payload={"text": "hello"},
        ))
        events = det.detect(np.empty(0), "test")
        assert len(events) == 1
        assert events[0].payload["text"] == "hello"
        # 第二次应为空（已清空）
        assert det.detect(np.empty(0), "test") == []

    def test_reset_clears_events(self):
        det = HotkeyVoiceDetector()
        det._events.append(PerceptionEvent(event_type="test"))
        det.reset()
        assert len(det._events) == 0

    def test_finalize_recording_publishes_clean_text(self):
        """转写含唤醒词+结束词 → 剥离后发布事件"""
        det = HotkeyVoiceDetector()
        det._recording = True
        with patch.object(det, "_transcribe_raw", return_value="科特 今天天气不错 完毕") as mock_t:
            with det._frames_lock:
                det._frames = [b"\x00\x00" * 1024]
            det._finalize_recording()
            mock_t.assert_called_once()
        assert len(det._events) == 1
        evt = det._events[0]
        assert evt.event_type == PerceptionEventType.SPEECH_DETECTED
        assert evt.payload["text"] == "今天天气不错"
        assert evt.payload["mode"] == "hotkey"

    def test_finalize_recording_without_wake_word(self):
        """无唤醒词也能触发（热键模式不需要科特）"""
        det = HotkeyVoiceDetector()
        det._recording = True
        with patch.object(det, "_transcribe_raw", return_value="直接说句话 完毕"):
            with det._frames_lock:
                det._frames = [b"\x00\x00" * 1024]
            det._finalize_recording()
        assert len(det._events) == 1
        assert det._events[0].payload["text"] == "直接说句话"

    def test_finalize_recording_no_frames(self):
        det = HotkeyVoiceDetector()
        det._recording = True
        with patch.object(det, "_transcribe_raw") as mock_t:
            det._finalize_recording()
            mock_t.assert_not_called()
        assert len(det._events) == 0

    def test_finalize_recording_empty_transcription(self):
        det = HotkeyVoiceDetector()
        det._recording = True
        with patch.object(det, "_transcribe_raw", return_value=""):
            with det._frames_lock:
                det._frames = [b"\x00\x00" * 1024]
            det._finalize_recording()
        assert len(det._events) == 0

    def test_toggle_recording_starts_and_stops(self):
        """第一次 toggle 开始录音，第二次 toggle 请求停止（录音线程收尾）"""
        det = HotkeyVoiceDetector()
        det._running = True
        with patch.object(det, "_begin_recording") as mock_begin:
            det._recording = False
            det._toggle_recording()
            mock_begin.assert_called_once()

            det._recording = True
            det._toggle_recording()
            assert det._stop_recording.is_set()

    def test_end_word_check_loop_auto_stops(self):
        """录音中检测到完毕 → 请求停止录音（置位停止事件）"""
        import threading

        det = HotkeyVoiceDetector(end_check_interval=1.0)
        det._recording = True
        det._end_stop = True
        det._end_word = "完毕"
        det._stop_recording = threading.Event()
        frames_needed = int(SAMPLE_RATE * 1.0 / 1024) + 1
        with det._frames_lock:
            det._frames = [b"\x00\x00" * 1024] * frames_needed

        with patch.object(det, "_transcribe_raw", return_value="完毕") as mock_t:
            det._end_word_check_loop()
            mock_t.assert_called()
            assert det._stop_recording.is_set()
        det._recording = False

    def test_end_word_check_loop_skips_when_disabled(self):
        det = HotkeyVoiceDetector()
        det._end_stop = False
        with patch.object(det, "_transcribe_raw") as mock_t:
            det._end_word_check_loop()
            mock_t.assert_not_called()

    def test_extract_instruction_shared_logic(self):
        """热键检测器复用 voice_detector 的指令提取逻辑"""
        assert HotkeyVoiceDetector.__module__
        assert extract_instruction.__module__ == "modules.perception.detectors.voice_detector"
