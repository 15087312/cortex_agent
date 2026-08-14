"""voice_detector + hotkey_voice_detector 分支覆盖扩展

补足: _check_availability(可用), start 成功/失败, stop join,
      _listen_loop 各分支(唤醒词/空文本/超时/OSError/异常/冷却),
      _recognize 云API/本地Whisper 各分支,
      hotkey _record_loop/_end_word_check_loop/_transcribe_raw/_process_frames。
全部 mock 硬件/系统边界，不触碰真实麦克风。
"""
import sys
import threading
import types
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from modules.perception.detectors.hotkey_voice_detector import (
    SAMPLE_RATE,
    HotkeyVoiceDetector,
)
from modules.perception.detectors.voice_detector import (
    VoiceDetector,
    extract_instruction,
)
from modules.perception.events.types import PerceptionEvent, PerceptionEventType

_HOTKEY_DEPS = ("pynput", "pyaudio", "speech_recognition", "whisper")


class _FakeWaitTimeoutError(Exception):
    pass


class _FakeUnknownValueError(Exception):
    pass


def _fake_sr_module():
    mod = types.ModuleType("speech_recognition")
    mod.WaitTimeoutError = _FakeWaitTimeoutError
    mod.UnknownValueError = _FakeUnknownValueError
    mod.Recognizer = MagicMock
    mod.Microphone = MagicMock
    mod.AudioData = MagicMock
    return mod


def _missing_deps():
    return {name: None for name in _HOTKEY_DEPS}


# ====================================================================
# extract_instruction 分支
# ====================================================================

class TestExtractInstructionExt:
    def test_end_word_empty_keeps_all(self):
        assert extract_instruction("科特 你好", "科特", "") == "你好"

    def test_wake_word_empty_keeps_all(self):
        assert extract_instruction("你好 完毕", "", "完毕") == "你好"

    def test_whitespace_compressed(self):
        assert extract_instruction("科特   你好", "科特", "") == "你好"


# ====================================================================
# VoiceDetector — 可用性 / 生命周期 / 后台监听循环 / 转写
# ====================================================================

class TestVoiceDetectorExt:
    @pytest.fixture(autouse=True)
    def _isolate_voice_deps(self):
        with patch.dict("sys.modules", _missing_deps()):
            yield

    def test_check_availability_true(self):
        mods = {name: types.ModuleType(name) for name in ("speech_recognition", "pyaudio", "whisper")}
        with patch.dict("sys.modules", mods):
            det = VoiceDetector()
            assert det.is_available() is True

    def test_start_success(self):
        fake_sr = _fake_sr_module()
        rec = MagicMock()
        mic = MagicMock()
        fake_sr.Recognizer = lambda: rec
        fake_sr.Microphone = lambda device_index=None: mic
        det = VoiceDetector()
        det._available = True
        with patch.dict("sys.modules", {"speech_recognition": fake_sr}), \
             patch("modules.perception.detectors.voice_detector.threading.Thread") as mt:
            det.start()
        assert det._running is True
        assert rec.energy_threshold == 300
        assert rec.dynamic_energy_threshold is True
        rec.adjust_for_ambient_noise.assert_called_once()
        mt.return_value.start.assert_called_once()

    def test_start_failure_sets_running_false(self):
        fake_sr = _fake_sr_module()
        def bad_microphone(**kw):
            raise OSError("no mic")
        fake_sr.Microphone = bad_microphone
        det = VoiceDetector()
        det._available = True
        with patch.dict("sys.modules", {"speech_recognition": fake_sr}):
            det.start()
        assert det._running is False

    def test_start_when_already_running(self):
        det = VoiceDetector()
        det._available = True
        det._running = True
        with patch("modules.perception.detectors.voice_detector.threading.Thread") as mt:
            det.start()
        mt.assert_not_called()

    def test_stop_joins_thread(self):
        det = VoiceDetector()
        t = MagicMock()
        det._thread = t
        det.stop()
        t.join.assert_called_once_with(timeout=5)
        assert det._recognizer is None
        assert det._microphone is None

    # ── _listen_loop 分支 ──

    def _make_listener(self, recognize=None, listen_returns="audio", stop_after=None):
        det = VoiceDetector()
        det._running = True
        det._recognizer = MagicMock()
        det._microphone = MagicMock()
        calls = {"n": 0}

        def fake_listen(*a, **k):
            calls["n"] += 1
            if stop_after is not None and calls["n"] >= stop_after:
                det._running = False
            if isinstance(listen_returns, Exception):
                raise listen_returns
            return listen_returns

        det._recognizer.listen.side_effect = fake_listen
        if recognize is not None:
            det._recognize = MagicMock(return_value=recognize)
        return det, calls

    def test_listen_loop_publishes_with_cooldown(self):
        det, calls = self._make_listener(recognize="科特 帮我查天气 完毕", stop_after=2)
        bus = MagicMock()
        det._event_bus = bus
        with patch.dict("sys.modules", {"speech_recognition": _fake_sr_module()}), \
             patch("modules.perception.detectors.voice_detector.time.sleep") as mock_sleep:
            det._listen_loop()
        assert calls["n"] == 2
        assert len(det._events) == 1
        assert det._events[0].payload["text"] == "帮我查天气"
        bus.publish.assert_called_once()
        mock_sleep.assert_called_once_with(3.0)

    def test_listen_loop_no_end_word_no_cooldown(self):
        det, _ = self._make_listener(recognize="科特 你好", stop_after=2)
        with patch.dict("sys.modules", {"speech_recognition": _fake_sr_module()}), \
             patch("modules.perception.detectors.voice_detector.time.sleep") as mock_sleep:
            det._listen_loop()
        assert len(det._events) == 1
        assert det._events[0].payload["text"] == "你好"
        mock_sleep.assert_not_called()

    def test_listen_loop_end_word_zero_cooldown(self):
        det, _ = self._make_listener(recognize="科特 帮我查天气 完毕", stop_after=2)
        det._end_stop_cooldown = 0.0
        with patch.dict("sys.modules", {"speech_recognition": _fake_sr_module()}), \
             patch("modules.perception.detectors.voice_detector.time.sleep") as mock_sleep:
            det._listen_loop()
        assert len(det._events) == 1
        mock_sleep.assert_not_called()

    def test_listen_loop_skips_invalid_texts(self):
        texts = iter(["", "普通的话", "科特", "科特 完毕"])
        det, calls = self._make_listener(stop_after=5)
        det._recognize = MagicMock(side_effect=lambda a: next(texts))
        with patch.dict("sys.modules", {"speech_recognition": _fake_sr_module()}), \
             patch("modules.perception.detectors.voice_detector.time.sleep"):
            det._listen_loop()
        assert calls["n"] == 5
        assert len(det._events) == 0

    def test_listen_loop_wait_timeout(self):
        det, calls = self._make_listener(listen_returns=_FakeWaitTimeoutError(), stop_after=2)
        with patch.dict("sys.modules", {"speech_recognition": _fake_sr_module()}), \
             patch("modules.perception.detectors.voice_detector.time.sleep"):
            det._listen_loop()
        assert calls["n"] == 2

    def test_listen_loop_oserror(self):
        det, calls = self._make_listener(listen_returns=OSError("mic"), stop_after=1)
        with patch.dict("sys.modules", {"speech_recognition": _fake_sr_module()}), \
             patch("modules.perception.detectors.voice_detector.time.sleep") as mock_sleep:
            det._listen_loop()
        mock_sleep.assert_called_once_with(2)

    def test_listen_loop_generic_exception(self):
        det, calls = self._make_listener(listen_returns=RuntimeError("boom"), stop_after=1)
        with patch.dict("sys.modules", {"speech_recognition": _fake_sr_module()}), \
             patch("modules.perception.detectors.voice_detector.time.sleep") as mock_sleep:
            det._listen_loop()
        mock_sleep.assert_called_once_with(1)

    def test_listen_loop_break_when_stopped_after_listen(self):
        det, calls = self._make_listener(recognize="科特 你好", stop_after=1)
        det._running = True
        with patch.dict("sys.modules", {"speech_recognition": _fake_sr_module()}), \
             patch("modules.perception.detectors.voice_detector.time.sleep"):
            det._listen_loop()
        assert calls["n"] == 1

    # ── _recognize 分支 ──

    def test_recognize_api_success(self, monkeypatch):
        from config.settings import settings
        monkeypatch.setattr(settings, "PERCEPTION_VOICE_BACKEND", "api")
        det = VoiceDetector()
        audio = MagicMock()
        audio.get_raw_data.return_value = b"raw"
        audio.sample_rate = 8000
        with patch("infra.data_process.core.speech_recognizer.transcribe_with_api",
                   return_value="你好") as m:
            assert det._recognize(audio) == "你好"
            m.assert_called_once_with(b"raw", "zh", 8000)

    def test_recognize_api_failure(self, monkeypatch):
        from config.settings import settings
        monkeypatch.setattr(settings, "PERCEPTION_VOICE_BACKEND", "api")
        det = VoiceDetector()
        audio = MagicMock()
        with patch("infra.data_process.core.speech_recognizer.transcribe_with_api",
                   side_effect=RuntimeError("api down")):
            assert det._recognize(audio) is None

    def test_recognize_local_success(self, monkeypatch):
        from config.settings import settings
        monkeypatch.setattr(settings, "PERCEPTION_VOICE_BACKEND", "local")
        det = VoiceDetector()
        rec = MagicMock()
        rec.recognize_whisper.return_value = "  科特 你好  "
        det._recognizer = rec
        audio = MagicMock()
        with patch.dict("sys.modules", {"speech_recognition": _fake_sr_module()}):
            assert det._recognize(audio) == "科特 你好"

    def test_recognize_local_unknown_value(self, monkeypatch):
        from config.settings import settings
        monkeypatch.setattr(settings, "PERCEPTION_VOICE_BACKEND", "local")
        det = VoiceDetector()
        rec = MagicMock()
        rec.recognize_whisper.side_effect = _FakeUnknownValueError()
        det._recognizer = rec
        audio = MagicMock()
        with patch.dict("sys.modules", {"speech_recognition": _fake_sr_module()}):
            assert det._recognize(audio) is None

    def test_recognize_local_generic_failure(self, monkeypatch):
        from config.settings import settings
        monkeypatch.setattr(settings, "PERCEPTION_VOICE_BACKEND", "local")
        det = VoiceDetector()
        rec = MagicMock()
        rec.recognize_whisper.side_effect = RuntimeError("whisper boom")
        det._recognizer = rec
        audio = MagicMock()
        with patch.dict("sys.modules", {"speech_recognition": _fake_sr_module()}):
            assert det._recognize(audio) is None

    def test_detect_and_reset(self):
        det = VoiceDetector()
        det._events.append(PerceptionEvent(event_type=PerceptionEventType.SPEECH_DETECTED))
        events = det.detect(np.empty(0), "x")
        assert len(events) == 1
        assert det.detect(np.empty(0), "x") == []
        det.reset()
        assert len(det._events) == 0


# ====================================================================
# HotkeyVoiceDetector — 可用性 / 生命周期 / 录音 / 结束词 / 转写
# ====================================================================

class TestHotkeyVoiceDetectorExt:
    @pytest.fixture(autouse=True)
    def _isolate_voice_deps(self):
        with patch.dict("sys.modules", _missing_deps()):
            yield

    def test_check_availability_true(self):
        mods = {name: types.ModuleType(name) for name in _HOTKEY_DEPS}
        with patch.dict("sys.modules", mods):
            det = HotkeyVoiceDetector()
            assert det.is_available() is True

    def test_detector_type(self):
        det = HotkeyVoiceDetector()
        assert det.detector_type == "voice_hotkey"

    def test_init_clamps_end_check_interval(self):
        det = HotkeyVoiceDetector(end_check_interval=0.2)
        assert det._end_check_interval == 1.0

    def test_start_success(self):
        det = HotkeyVoiceDetector()
        det._available = True
        listener = MagicMock()
        captured = {}
        fake_keyboard = MagicMock()

        def _global_hotkeys(cfg):
            captured["cfg"] = cfg
            return listener

        fake_keyboard.GlobalHotKeys = _global_hotkeys
        fake_pynput = types.ModuleType("pynput")
        fake_pynput.keyboard = fake_keyboard
        with patch.dict("sys.modules", {"pynput": fake_pynput}):
            det.start()
        assert det._running is True
        assert det._listener is listener
        listener.start.assert_called_once()
        # 触发注册的热键回调 → _toggle_recording
        callback = captured["cfg"]["f8"]
        with patch.object(det, "_toggle_recording") as m:
            callback()
        m.assert_called_once()

    def test_start_failure(self):
        det = HotkeyVoiceDetector()
        det._available = True
        fake_keyboard = MagicMock()
        def bad_global_hotkeys(cfg):
            raise RuntimeError("no global hotkeys")
        fake_keyboard.GlobalHotKeys = bad_global_hotkeys
        fake_pynput = types.ModuleType("pynput")
        fake_pynput.keyboard = fake_keyboard
        with patch.dict("sys.modules", {"pynput": fake_pynput}):
            det.start()
        assert det._running is False

    def test_start_when_running(self):
        det = HotkeyVoiceDetector()
        det._available = True
        det._running = True
        with patch.object(det, "_listener", None):
            det.start()
        assert det._running is True

    def test_stop_joins_record_thread(self):
        det = HotkeyVoiceDetector()
        t = MagicMock()
        t.is_alive.return_value = True
        det._record_thread = t
        listener = MagicMock()
        det._listener = listener
        det.stop()
        assert det._recording is False
        assert det._stop_recording.is_set()
        t.join.assert_called_once_with(timeout=5)
        listener.stop.assert_called_once()
        assert det._listener is None

    def test_stop_listener_stop_raises(self):
        det = HotkeyVoiceDetector()
        det._listener = MagicMock()
        det._listener.stop.side_effect = RuntimeError("stop fail")
        det.stop()
        assert det._listener is None

    def test_stop_no_listener(self):
        det = HotkeyVoiceDetector()
        det.stop()
        assert det._listener is None

    def test_toggle_not_running(self):
        det = HotkeyVoiceDetector()
        det._running = False
        with patch.object(det, "_begin_recording") as m:
            det._toggle_recording()
        m.assert_not_called()

    def test_toggle_recording_requests_stop(self):
        det = HotkeyVoiceDetector()
        det._running = True
        det._recording = True
        det._toggle_recording()
        assert det._stop_recording.is_set()

    def test_toggle_starts_recording(self):
        det = HotkeyVoiceDetector()
        det._running = True
        det._recording = False
        with patch.object(det, "_begin_recording") as m:
            det._toggle_recording()
        m.assert_called_once()

    def test_begin_recording_both_threads(self):
        det = HotkeyVoiceDetector(end_stop=True, end_word="完毕")
        with patch("modules.perception.detectors.hotkey_voice_detector.threading.Thread") as mt:
            det._begin_recording()
        assert det._recording is True
        assert det._record_generation == 1
        assert mt.call_count == 2
        mt.return_value.start.assert_called()

    def test_begin_recording_no_end_check(self):
        det = HotkeyVoiceDetector(end_stop=False)
        with patch("modules.perception.detectors.hotkey_voice_detector.threading.Thread") as mt:
            det._begin_recording()
        assert mt.call_count == 1

    # ── _record_loop 分支 ──

    def _fake_pyaudio(self, stream, open_side_effect=None):
        pa = MagicMock()
        pa.paInt16 = 8
        pa.open.return_value = stream
        if open_side_effect is not None:
            pa.open.side_effect = open_side_effect
        fake_pa = types.ModuleType("pyaudio")
        fake_pa.paInt16 = 8
        fake_pa.PyAudio = lambda: pa
        return fake_pa, pa

    def test_record_loop_stops_on_event(self):
        det = HotkeyVoiceDetector()
        counter = {"n": 0}
        stream = MagicMock()

        def _read(*a, **k):
            counter["n"] += 1
            if counter["n"] >= 2:
                det._stop_recording.set()
            return b"\x00" * 1024

        stream.read.side_effect = _read
        fake_pa, pa = self._fake_pyaudio(stream)
        with patch.dict("sys.modules", {"pyaudio": fake_pa}), \
             patch.object(det, "_finalize_recording") as mf:
            det._record_loop()
        assert counter["n"] == 2
        stream.stop_stream.assert_called_once()
        stream.close.assert_called_once()
        pa.terminate.assert_called_once()
        mf.assert_called_once()

    def test_record_loop_read_oserror(self):
        det = HotkeyVoiceDetector()
        stream = MagicMock()
        stream.read.side_effect = OSError("overflow")
        fake_pa, pa = self._fake_pyaudio(stream)
        with patch.dict("sys.modules", {"pyaudio": fake_pa}), \
             patch.object(det, "_finalize_recording") as mf:
            det._record_loop()
        mf.assert_called_once()

    def test_record_loop_max_duration(self):
        det = HotkeyVoiceDetector(max_duration=-1.0)
        stream = MagicMock()
        fake_pa, pa = self._fake_pyaudio(stream)
        with patch.dict("sys.modules", {"pyaudio": fake_pa}), \
             patch.object(det, "_finalize_recording") as mf:
            det._record_loop()
        stream.read.assert_not_called()
        mf.assert_called_once()

    def test_record_loop_open_failure(self):
        det = HotkeyVoiceDetector()
        fake_pa, pa = self._fake_pyaudio(MagicMock(), open_side_effect=OSError("no device"))
        with patch.dict("sys.modules", {"pyaudio": fake_pa}), \
             patch.object(det, "_finalize_recording") as mf:
            det._record_loop()
        pa.terminate.assert_called_once()
        mf.assert_called_once()

    def test_finalize_skips_when_not_recording(self):
        det = HotkeyVoiceDetector()
        det._recording = False
        with patch.object(det, "_process_frames") as m:
            det._finalize_recording()
        m.assert_not_called()

    def test_finalize_processes_frames(self):
        det = HotkeyVoiceDetector()
        det._recording = True
        with patch.object(det, "_process_frames") as m:
            det._finalize_recording()
        m.assert_called_once()
        assert det._recording is False
        assert det._record_thread is None

    # ── _end_word_check_loop 分支 ──

    def test_end_check_disabled(self):
        det = HotkeyVoiceDetector(end_stop=False)
        with patch.object(det, "_transcribe_raw") as m:
            det._end_word_check_loop()
        m.assert_not_called()

    def test_end_check_not_recording_loop_not_entered(self):
        det = HotkeyVoiceDetector(end_stop=True, end_word="完毕")
        det._recording = False
        with patch.object(det, "_transcribe_raw") as m:
            det._end_word_check_loop()
        m.assert_not_called()

    def test_end_check_generation_mismatch(self):
        det = HotkeyVoiceDetector(end_check_interval=1.0)
        det._recording = True
        det._end_stop = True
        det._end_word = "完毕"

        def _sleep(secs):
            det._record_generation += 1  # 模拟会话重启

        with patch.object(det, "_transcribe_raw") as m, \
             patch("modules.perception.detectors.hotkey_voice_detector.time.sleep", _sleep):
            det._end_word_check_loop()
        m.assert_not_called()

    def test_end_check_recording_off_during_sleep(self):
        det = HotkeyVoiceDetector(end_check_interval=1.0)
        det._recording = True
        det._end_stop = True
        det._end_word = "完毕"
        det._stop_recording = threading.Event()

        def _sleep(secs):
            det._recording = False

        with patch.object(det, "_transcribe_raw") as m, \
             patch("modules.perception.detectors.hotkey_voice_detector.time.sleep", _sleep):
            det._end_word_check_loop()
        m.assert_not_called()

    def test_end_check_stop_set_during_sleep(self):
        det = HotkeyVoiceDetector(end_check_interval=1.0)
        det._recording = True
        det._end_stop = True
        det._end_word = "完毕"
        det._stop_recording = threading.Event()

        def _sleep(secs):
            det._stop_recording.set()

        with patch.object(det, "_transcribe_raw") as m, \
             patch("modules.perception.detectors.hotkey_voice_detector.time.sleep", _sleep):
            det._end_word_check_loop()
        m.assert_not_called()

    def test_end_check_no_frames_then_no_end_word(self):
        det = HotkeyVoiceDetector(end_check_interval=1.0)
        det._recording = True
        det._end_stop = True
        det._end_word = "完毕"
        det._stop_recording = threading.Event()
        det._frames = []
        sleep_calls = {"n": 0}

        def _sleep(secs):
            sleep_calls["n"] += 1
            if sleep_calls["n"] >= 2:
                with det._frames_lock:
                    det._frames = [b"\x00\x00" * 1024]

        def _transcribe(data):
            det._recording = False  # 无结束词 → 下一轮退出
            return "没有完毕词"

        with patch.object(det, "_transcribe_raw", _transcribe), \
             patch("modules.perception.detectors.hotkey_voice_detector.time.sleep", _sleep):
            det._end_word_check_loop()
        assert sleep_calls["n"] == 2

    def test_end_check_transcribe_error_then_end_word(self):
        det = HotkeyVoiceDetector(end_check_interval=1.0)
        det._recording = True
        det._end_stop = True
        det._end_word = "完毕"
        det._stop_recording = threading.Event()
        calls = {"n": 0}
        frames_needed = int(SAMPLE_RATE * 1.0 / 1024) + 1
        with det._frames_lock:
            det._frames = [b"\x00\x00" * 1024] * frames_needed

        def _transcribe(data):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("stt boom")
            det._recording = False
            return "完毕"

        with patch.object(det, "_transcribe_raw", _transcribe), \
             patch("modules.perception.detectors.hotkey_voice_detector.time.sleep"):
            det._end_word_check_loop()
        assert det._stop_recording.is_set()
        assert calls["n"] == 2

    def test_end_check_short_circuit_falsy_text(self):
        det = HotkeyVoiceDetector(end_check_interval=1.0)
        det._recording = True
        det._end_stop = True
        det._end_word = "完毕"
        det._stop_recording = threading.Event()
        frames_needed = int(SAMPLE_RATE * 1.0 / 1024) + 1
        with det._frames_lock:
            det._frames = [b"\x00\x00" * 1024] * frames_needed
        calls = {"n": 0}

        def _transcribe(data):
            calls["n"] += 1
            det._recording = False
            return ""  # 空文本 → text 短路，走 252->234

        with patch.object(det, "_transcribe_raw", _transcribe), \
             patch("modules.perception.detectors.hotkey_voice_detector.time.sleep"):
            det._end_word_check_loop()
        assert calls["n"] == 1

    # ── _transcribe_raw 分支 ──

    def test_transcribe_raw_api(self, monkeypatch):
        from config.settings import settings
        monkeypatch.setattr(settings, "PERCEPTION_VOICE_BACKEND", "api")
        det = HotkeyVoiceDetector()
        with patch("infra.data_process.core.speech_recognizer.transcribe_with_api",
                   return_value="hi") as m:
            assert det._transcribe_raw(b"\x00\x00") == "hi"
            m.assert_called_once_with(b"\x00\x00", "zh", SAMPLE_RATE)

    def test_transcribe_raw_local_success(self, monkeypatch):
        from config.settings import settings
        monkeypatch.setattr(settings, "PERCEPTION_VOICE_BACKEND", "local")
        det = HotkeyVoiceDetector()
        fake_sr = _fake_sr_module()
        rec = MagicMock()
        rec.recognize_whisper.return_value = "  科特 你好  "
        fake_sr.Recognizer = lambda: rec
        with patch.dict("sys.modules", {"speech_recognition": fake_sr}):
            assert det._transcribe_raw(b"\x00\x00" * 1024) == "科特 你好"
        assert det._recognizer is rec

    def test_transcribe_raw_local_unknown(self, monkeypatch):
        from config.settings import settings
        monkeypatch.setattr(settings, "PERCEPTION_VOICE_BACKEND", "local")
        det = HotkeyVoiceDetector()
        fake_sr = _fake_sr_module()
        rec = MagicMock()
        rec.recognize_whisper.side_effect = _FakeUnknownValueError()
        fake_sr.Recognizer = lambda: rec
        with patch.dict("sys.modules", {"speech_recognition": fake_sr}):
            assert det._transcribe_raw(b"x") == ""

    def test_transcribe_raw_local_reuses_recognizer(self, monkeypatch):
        from config.settings import settings
        monkeypatch.setattr(settings, "PERCEPTION_VOICE_BACKEND", "local")
        det = HotkeyVoiceDetector()
        fake_sr = _fake_sr_module()
        rec = MagicMock()
        rec.recognize_whisper.return_value = "你好"
        fake_sr.Recognizer = lambda: rec
        det._recognizer = rec  # 已有 recognizer → 复用分支
        with patch.dict("sys.modules", {"speech_recognition": fake_sr}):
            assert det._transcribe_raw(b"x") == "你好"

    def test_transcribe_raw_local_exception(self, monkeypatch):
        from config.settings import settings
        monkeypatch.setattr(settings, "PERCEPTION_VOICE_BACKEND", "local")
        det = HotkeyVoiceDetector()
        fake_sr = _fake_sr_module()
        rec = MagicMock()
        rec.recognize_whisper.side_effect = RuntimeError("whisper boom")
        fake_sr.Recognizer = lambda: rec
        with patch.dict("sys.modules", {"speech_recognition": fake_sr}):
            assert det._transcribe_raw(b"x") == ""

    # ── _process_frames 分支 ──

    def test_process_frames_no_data(self):
        det = HotkeyVoiceDetector()
        with det._frames_lock:
            det._frames = []
        with patch.object(det, "_transcribe_raw") as m:
            det._process_frames()
        m.assert_not_called()

    def test_process_frames_empty_text(self):
        det = HotkeyVoiceDetector()
        with det._frames_lock:
            det._frames = [b"\x00\x00" * 1024]
        with patch.object(det, "_transcribe_raw", return_value=""):
            det._process_frames()
        assert len(det._events) == 0

    def test_process_frames_clean_empty(self):
        det = HotkeyVoiceDetector()
        with det._frames_lock:
            det._frames = [b"\x00\x00" * 1024]
        with patch.object(det, "_transcribe_raw", return_value="科特 完毕"):
            det._process_frames()
        assert len(det._events) == 0

    def test_process_frames_publish(self):
        det = HotkeyVoiceDetector()
        bus = MagicMock()
        det._event_bus = bus
        with det._frames_lock:
            det._frames = [b"\x00\x00" * 1024]
        with patch.object(det, "_transcribe_raw", return_value="科特 帮我写代码 完毕"):
            det._process_frames()
        assert len(det._events) == 1
        evt = det._events[0]
        assert evt.event_type == PerceptionEventType.SPEECH_DETECTED
        assert evt.payload["text"] == "帮我写代码"
        assert evt.payload["mode"] == "hotkey"
        bus.publish.assert_called_once_with(evt)

    def test_process_frames_publish_no_bus(self):
        det = HotkeyVoiceDetector()
        det._event_bus = None
        with det._frames_lock:
            det._frames = [b"\x00\x00" * 1024]
        with patch.object(det, "_transcribe_raw", return_value="科特 你好"):
            det._process_frames()
        assert len(det._events) == 1

    def test_detect_and_reset(self):
        det = HotkeyVoiceDetector()
        det._events.append(PerceptionEvent(event_type=PerceptionEventType.SPEECH_DETECTED))
        events = det.detect(np.empty(0), "x")
        assert len(events) == 1
        assert det.detect(np.empty(0), "x") == []
        det.reset()
        assert len(det._events) == 0
