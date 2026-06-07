"""语音检测器 — 麦克风监听 + Whisper STT

使用 speech_recognition 库监听麦克风，检测到语音时调用 Whisper 识别，
直接发布 SPEECH_DETECTED 事件到 Event Bus。

依赖:
- SpeechRecognition (pip install SpeechRecognition)
- pyaudio (pip install pyaudio)
- openai-whisper (pip install openai-whisper)
"""
import collections
import threading
import time
from typing import Any, Dict, List, Optional

import numpy as np

from modules.perception.detectors.base import PerceptionDetector
from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from utils.logger import setup_logger

logger = setup_logger("perception_voice_detector")


class VoiceDetector(PerceptionDetector):
    """语音检测器

    后台线程监听麦克风，检测到语音时:
    1. 录音直到静音
    2. 调用 Whisper STT 识别
    3. 直接发布 SPEECH_DETECTED 事件到 Event Bus

    detect() 返回缓存的事件（用于非图像检测器路径）。
    """

    def __init__(
        self,
        device_index: Optional[int] = None,
        model_size: str = "tiny",
        language: str = "zh",
        energy_threshold: int = 300,
        timeout: float = 10.0,
        event_bus=None,
    ):
        self._device_index = device_index
        self._model_size = model_size
        self._language = language
        self._energy_threshold = energy_threshold
        self._timeout = timeout
        self._event_bus = event_bus

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._recognizer = None
        self._microphone = None
        self._events: collections.deque = collections.deque(maxlen=100)
        self._events_lock = threading.Lock()

        self._available = self._check_availability()

    def _check_availability(self) -> bool:
        try:
            import speech_recognition  # noqa: F401
            import pyaudio  # noqa: F401
            return True
        except ImportError as e:
            logger.debug(f"语音依赖不可用: {e}")
            return False

    def is_available(self) -> bool:
        return self._available

    @property
    def detector_type(self) -> str:
        return "voice"

    def start(self) -> None:
        if not self._available or self._running:
            return

        try:
            import speech_recognition as sr

            self._recognizer = sr.Recognizer()
            self._recognizer.energy_threshold = self._energy_threshold
            self._recognizer.dynamic_energy_threshold = True
            self._microphone = sr.Microphone(device_index=self._device_index)

            with self._microphone as source:
                self._recognizer.adjust_for_ambient_noise(source, duration=1)
                logger.info(f"麦克风校准: threshold={self._recognizer.energy_threshold:.0f}")

            self._running = True
            self._thread = threading.Thread(
                target=self._listen_loop, daemon=True, name="voice-detector"
            )
            self._thread.start()
            logger.info(f"语音检测器启动: model={self._model_size} lang={self._language}")
        except Exception as e:
            logger.error(f"语音检测器启动失败: {e}")
            self._running = False

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        self._recognizer = None
        self._microphone = None
        logger.info("语音检测器已停止")

    def detect(
        self,
        roi_image: np.ndarray,
        roi_name: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[PerceptionEvent]:
        """返回缓存的事件（由后台线程产出）"""
        with self._events_lock:
            events = list(self._events)
            self._events.clear()
        return events

    def _listen_loop(self):
        import speech_recognition as sr

        while self._running:
            try:
                with self._microphone as source:
                    audio = self._recognizer.listen(
                        source, timeout=self._timeout, phrase_time_limit=15,
                    )
                if not self._running:
                    break

                text = self._recognize(audio)
                if text:
                    event = PerceptionEvent(
                        event_type=PerceptionEventType.SPEECH_DETECTED,
                        source="voice",
                        importance=0.8,
                        payload={"text": text, "language": self._language},
                    )
                    # 缓存事件
                    with self._events_lock:
                        self._events.append(event)
                    # 直接发布到 Event Bus
                    if self._event_bus:
                        self._event_bus.publish(event)
                    logger.info(f"语音识别: {text[:80]}")

            except sr.WaitTimeoutError:
                pass
            except OSError as e:
                logger.warning(f"麦克风错误: {e}")
                time.sleep(2)
            except Exception as e:
                logger.warning(f"语音监听异常: {e}")
                time.sleep(1)

    def _recognize(self, audio) -> Optional[str]:
        import speech_recognition as sr

        # 优先 Whisper（本地）
        try:
            text = self._recognizer.recognize_whisper(
                audio, model=self._model_size, language=self._language,
            )
            return text.strip() if text else None
        except sr.UnknownValueError:
            return None
        except Exception as e:
            logger.debug(f"Whisper 失败: {e}")

        # 不降级到 Google STT（避免发送音频到外部服务器）
        return None

    def reset(self) -> None:
        with self._events_lock:
            self._events.clear()
