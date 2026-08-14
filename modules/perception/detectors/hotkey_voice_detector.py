"""热键语音检测器 — 全局热键 Push-to-Talk 录音 + Whisper STT

按一下热键开始录音，再按一下停止录音并转写识别，发布 SPEECH_DETECTED 事件。
录音过程中可选的结束词（默认"完毕"）检测：检测到即自动停止录音（说完即止）。

依赖:
- pynput (pip install pynput) — 全局热键监听
- pyaudio (pip install pyaudio) — 麦克风采集
- openai-whisper (pip install openai-whisper) — 本地识别

配置（config.settings）:
- PERCEPTION_VOICE_HOTKEY: 全局热键（pynput 格式，如 "f8" / "<ctrl>+<space>"）
- PERCEPTION_VOICE_END_STOP: 结束词是否作为自动停止
- PERCEPTION_VOICE_MAX_DURATION: 单次录音最大时长（秒）
- PERCEPTION_VOICE_END_CHECK_INTERVAL: 录音中检测结束词的间隔（秒）

线程模型（避免死锁/重复发布）:
- 录音线程（_record_loop）是唯一执行 finalize 的线程：
  超时/异常/外部请求停止后，由录音线程自己收尾并转写发布
- 控制线程（热键回调、结束词检测）只负责置位 _stop_recording 事件，
  绝不 join 录音线程，也不直接处理音频
"""
import collections
import threading
import time
import weakref
from typing import Any, Dict, List, Optional

import numpy as np

from modules.perception.detectors.base import PerceptionDetector
from modules.perception.detectors.voice_detector import extract_instruction
from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from utils.logger import setup_logger

logger = setup_logger("perception_hotkey_voice")

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # paInt16


class HotkeyVoiceDetector(PerceptionDetector):
    """热键语音检测器

    全局热键切换录音：
    - 第一次按下 → 开始录音（pyaudio 采集到内存）
    - 再次按下 → 停止录音 → Whisper 转写 → 发布 SPEECH_DETECTED
    - 录音中检测到结束词（默认"完毕"）→ 自动停止录音

    活跃实例追踪（weakref）：测试/退出时统一 stop，避免后台线程遗留。
    """

    _all_instances = weakref.WeakSet()

    def __init__(
        self,
        hotkey: str = "f8",
        device_index: Optional[int] = None,
        model_size: str = "tiny",
        language: str = "zh",
        wake_word: str = "科特",
        end_word: str = "完毕",
        end_stop: bool = True,
        max_duration: float = 60.0,
        end_check_interval: float = 3.0,
        event_bus=None,
    ):
        self._hotkey = hotkey
        self._device_index = device_index
        self._model_size = model_size
        self._language = language
        self._wake_word = wake_word
        self._end_word = end_word
        self._end_stop = end_stop
        self._max_duration = max_duration
        self._end_check_interval = max(1.0, end_check_interval)
        self._event_bus = event_bus

        self._running = False
        self._listener = None
        self._recording = False
        self._stop_recording = threading.Event()
        self._frames: List[bytes] = []
        self._frames_lock = threading.Lock()
        self._record_thread: Optional[threading.Thread] = None
        HotkeyVoiceDetector._all_instances.add(self)
        self._finish_lock = threading.Lock()
        # 录音会话代号：快速重启时防止旧的结束词检测线程串台到新会话
        self._record_generation = 0
        self._events: collections.deque = collections.deque(maxlen=100)
        self._events_lock = threading.Lock()
        self._recognizer = None

        self._available = self._check_availability()

    def _check_availability(self) -> bool:
        try:
            import pynput  # noqa: F401
            import pyaudio  # noqa: F401
            import speech_recognition  # noqa: F401
            import whisper  # noqa: F401
            return True
        except ImportError as e:
            logger.debug(f"热键语音依赖不可用: {e}")
            return False

    def is_available(self) -> bool:
        return self._available

    @property
    def detector_type(self) -> str:
        return "voice_hotkey"

    # ── 生命周期 ────────────────────────────────────────────

    def start(self) -> None:
        if not self._available or self._running:
            return
        try:
            from pynput import keyboard

            def _on_hotkey() -> None:
                self._toggle_recording()

            self._listener = keyboard.GlobalHotKeys({self._hotkey: _on_hotkey})
            self._listener.start()
            self._running = True
            logger.info(
                f"热键语音检测器启动: hotkey={self._hotkey!r} "
                f"model={self._model_size} lang={self._language}"
            )
        except Exception as e:
            logger.error(f"热键语音检测器启动失败: {e}")
            self._running = False

    def stop(self) -> None:
        self._running = False
        # 先复位录音状态，避免收尾处理陈旧音频
        self._recording = False
        self._stop_recording.set()
        if self._record_thread and self._record_thread.is_alive():
            self._record_thread.join(timeout=5)
            self._record_thread = None
        self._end_check_thread = None
        HotkeyVoiceDetector._all_instances.discard(self)
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        logger.info("热键语音检测器已停止")

    # ── 热键切换录音 ────────────────────────────────────────

    def _toggle_recording(self) -> None:
        if not self._running:
            return
        if self._recording:
            # 控制线程只请求停止；录音线程自己收尾
            self._stop_recording.set()
            logger.info("⏹️ 已请求停止录音（录音线程收尾中）...")
        else:
            self._begin_recording()

    def _begin_recording(self) -> None:
        self._record_generation += 1
        self._frames = []
        self._stop_recording.clear()
        self._recording = True
        self._record_thread = threading.Thread(
            target=self._record_loop, daemon=True, name="hotkey-voice-record"
        )
        self._record_thread.start()
        logger.info("🎙️ 热键录音开始（再按热键或说完毕结束）")

        if self._end_stop and self._end_word:
            self._end_check_thread = threading.Thread(
                target=self._end_word_check_loop,
                daemon=True,
                name="hotkey-voice-endcheck",
            )
            self._end_check_thread.start()

    # ── 录音线程（唯一 finalize 线程）───────────────────────

    def _record_loop(self) -> None:
        import pyaudio

        pa = pyaudio.PyAudio()
        try:
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                input_device_index=self._device_index,
                frames_per_buffer=1024,
            )
            start = time.time()
            while not self._stop_recording.is_set():
                if time.time() - start > self._max_duration:
                    logger.info("热键录音达到最大时长，自动停止")
                    break
                try:
                    data = stream.read(1024, exception_on_overflow=False)
                except OSError as e:
                    logger.warning(f"麦克风读取错误: {e}")
                    break
                with self._frames_lock:
                    self._frames.append(data)
            stream.stop_stream()
            stream.close()
        except Exception as e:
            logger.error(f"热键录音异常: {e}")
        finally:
            pa.terminate()
            # 只有录音线程自己收尾：处理音频并发布事件（幂等，由锁保证）
            self._finalize_recording()

    def _finalize_recording(self) -> None:
        """收尾：复位状态并处理音频。仅在录音线程中调用。"""
        with self._finish_lock:
            if not self._recording:
                return
            self._record_thread = None
            self._recording = False
            self._process_frames()

    # ── 结束词自动停止 ──────────────────────────────────────

    def _end_word_check_loop(self) -> None:
        """录音期间周期性转写最近音频，检测到结束词即请求停止录音"""
        if not self._end_stop or not self._end_word:
            return

        # 绑定当前会话代号：会话重启后本线程立即退出，避免串台
        generation = self._record_generation
        frames_per_check = int(SAMPLE_RATE * self._end_check_interval / 1024)
        while self._recording and not self._stop_recording.is_set():
            time.sleep(self._end_check_interval)
            if generation != self._record_generation:
                return
            if not self._recording or self._stop_recording.is_set():
                return

            with self._frames_lock:
                if not self._frames:
                    continue
                recent = self._frames[-frames_per_check:]

            try:
                text = self._transcribe_raw(b"".join(recent))
            except Exception as e:
                logger.debug(f"结束词检测转写失败 (非致命): {e}")
                continue

            if text and self._end_word in text:
                logger.info(f"检测到结束词，自动停止录音: {text[:40]!r}")
                # 控制线程只请求停止；录音线程自己收尾
                self._stop_recording.set()
                return

    # ── 转写与事件发布 ──────────────────────────────────────

    def _transcribe_raw(self, audio_bytes: bytes) -> str:
        """转写原始 PCM 音频（云端 API 或本地 Whisper），返回文本（失败返回空串）"""
        from config.settings import settings
        if getattr(settings, "PERCEPTION_VOICE_BACKEND", "local") == "api":
            from infra.data_process.core.speech_recognizer import transcribe_with_api
            return transcribe_with_api(audio_bytes, self._language, SAMPLE_RATE)

        import speech_recognition as sr

        if self._recognizer is None:
            self._recognizer = sr.Recognizer()
        audio = sr.AudioData(audio_bytes, sample_rate=SAMPLE_RATE, sample_width=SAMPLE_WIDTH)
        try:
            text = self._recognizer.recognize_whisper(
                audio, model=self._model_size, language=self._language,
            )
            return text.strip() if text else ""
        except sr.UnknownValueError:
            return ""
        except Exception as e:
            logger.debug(f"Whisper 识别失败: {e}")
            return ""

    def _process_frames(self) -> None:
        with self._frames_lock:
            frames = list(self._frames)
        if not frames:
            logger.info("无录音数据")
            return

        audio_bytes = b"".join(frames)
        text = self._transcribe_raw(audio_bytes)
        if not text:
            logger.info("未识别到有效语音")
            return

        clean = extract_instruction(text, self._wake_word, self._end_word)
        if not clean:
            logger.info(f"无有效指令: raw={text[:50]!r}")
            return

        logger.info(f"热键语音识别: text={clean[:80]}")

        event = PerceptionEvent(
            event_type=PerceptionEventType.SPEECH_DETECTED,
            source="voice_hotkey",
            importance=0.8,
            payload={
                "text": clean,
                "raw": text,
                "language": self._language,
                "mode": "hotkey",
            },
        )
        with self._events_lock:
            self._events.append(event)
        if self._event_bus:
            self._event_bus.publish(event)

    # ── PerceptionDetector 接口 ─────────────────────────────

    def detect(
        self,
        roi_image: np.ndarray,
        roi_name: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[PerceptionEvent]:
        """返回缓存的事件（由录音线程产出）"""
        with self._events_lock:
            events = list(self._events)
            self._events.clear()
        return events

    def reset(self) -> None:
        with self._events_lock:
            self._events.clear()
