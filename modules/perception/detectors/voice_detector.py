"""语音检测器 — 麦克风监听 + Whisper STT

使用 speech_recognition 库监听麦克风，检测到语音时调用 Whisper 识别，
直接发布 SPEECH_DETECTED 事件到 Event Bus。

依赖:
- SpeechRecognition (pip install SpeechRecognition)
- pyaudio (pip install pyaudio)
- openai-whisper (pip install openai-whisper)
"""
import collections
import re
import threading
import time
import weakref
from typing import Any, Dict, List, Optional

import numpy as np

from modules.perception.detectors.base import PerceptionDetector
from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from utils.logger import setup_logger

logger = setup_logger("perception_voice_detector")


def extract_instruction(
    text: str,
    wake_word: str = "",
    end_word: str = "",
) -> str:
    """从识别文本中提取有效指令

    - 结束词（如"完毕"）作为停止词：只保留结束词之前的内容
    - 剥离唤醒词（如"科特"）
    """
    clean = text
    if end_word and end_word in clean:
        clean = clean.split(end_word, 1)[0]
    if wake_word:
        clean = clean.replace(wake_word, "")
    # 压缩多余空白（剥离唤醒词可能留下双空格）
    return re.sub(r"\s+", " ", clean).strip()


class VoiceDetector(PerceptionDetector):
    """语音检测器

    后台线程监听麦克风，检测到语音时:
    1. 录音直到静音
    2. 调用 Whisper STT 识别
    3. 直接发布 SPEECH_DETECTED 事件到 Event Bus

    detect() 返回缓存的事件（用于非图像检测器路径）。

    活跃实例追踪（weakref）：测试/退出时统一 stop，避免后台线程遗留。
    """

    _all_instances = weakref.WeakSet()

    def __init__(
        self,
        device_index: Optional[int] = None,
        model_size: str = "tiny",
        language: str = "zh",
        energy_threshold: int = 300,
        timeout: float = 10.0,
        event_bus=None,
        wake_word: str = "科特",
        end_word: str = "完毕",
        end_stop_cooldown: float = 3.0,
    ):
        self._device_index = device_index
        self._model_size = model_size
        self._language = language
        self._energy_threshold = energy_threshold
        self._timeout = timeout
        self._event_bus = event_bus
        self._wake_word = wake_word
        self._end_word = end_word
        self._end_stop_cooldown = end_stop_cooldown

        self._running = False
        self._thread: Optional[threading.Thread] = None
        VoiceDetector._all_instances.add(self)
        self._recognizer = None
        self._microphone = None
        self._events: collections.deque = collections.deque(maxlen=100)
        self._events_lock = threading.Lock()

        self._available = self._check_availability()

    def _check_availability(self) -> bool:
        try:
            import speech_recognition  # noqa: F401
            import pyaudio  # noqa: F401
            import whisper  # noqa: F401
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
            # dynamic_energy_threshold 使引擎自适应环境噪音变化
            self._recognizer.dynamic_energy_threshold = True
            self._microphone = sr.Microphone(device_index=self._device_index)

            with self._microphone as source:
                # 1 秒环境噪声校准
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
        VoiceDetector._all_instances.discard(self)
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
        """后台语音监听循环

        使用 speech_recognition 的 listen() 方法检测语音活动（VAD），
        每次说话结束后用 Whisper 转写，仅当文本包含唤醒词时才触发事件。

        唤醒词机制：
        - 每次转写后检查是否包含唤醒词（默认"科特"）
        - 包含唤醒词 → 剥离唤醒词和结束词（默认"完毕"），发布事件
        - 不含唤醒词 → 静默丢弃，不触发任何响应
        - 能量阈值仅用于判断是否有人在说话（VAD），不是触发条件
        """
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
                if not text:
                    continue

                # ── 唤醒词过滤：仅当文本包含唤醒词时才触发 ──
                if self._wake_word not in text:
                    continue

                # 完毕作为停止词：截断到结束词之前 + 剥离唤醒词
                end_detected = bool(self._end_word and self._end_word in text)
                clean = extract_instruction(text, self._wake_word, self._end_word)

                if not clean:
                    continue

                logger.info(
                    f"语音唤醒: wake={self._wake_word!r} text={clean[:80]}"
                )

                event = PerceptionEvent(
                    event_type=PerceptionEventType.SPEECH_DETECTED,
                    source="voice",
                    importance=0.8,
                    payload={
                        "text": clean,
                        "raw": text,
                        "language": self._language,
                    },
                )
                with self._events_lock:
                    self._events.append(event)
                if self._event_bus:
                    self._event_bus.publish(event)

                # 完毕作为停止词：说完毕后进入冷却，防止环境音立刻再次触发
                if end_detected and self._end_stop_cooldown > 0:
                    logger.info(
                        f"检测到结束词，冷却 {self._end_stop_cooldown}s"
                    )
                    time.sleep(self._end_stop_cooldown)

            except sr.WaitTimeoutError:
                pass
            except OSError as e:
                logger.warning(f"麦克风错误: {e}")
                time.sleep(2)
            except Exception as e:
                logger.warning(f"语音监听异常: {e}")
                time.sleep(1)

    def _recognize(self, audio) -> Optional[str]:
        """语音识别：云端 API（配置后）或本地 Whisper"""
        from config.settings import settings
        if getattr(settings, "PERCEPTION_VOICE_BACKEND", "local") == "api":
            from infra.data_process.core.speech_recognizer import transcribe_with_api
            try:
                text = transcribe_with_api(
                    audio.get_raw_data(), self._language, getattr(audio, "sample_rate", 16000)
                )
                return text or None
            except Exception as e:
                logger.debug(f"云端 STT 失败: {e}")
                return None

        import speech_recognition as sr

        try:
            text = self._recognizer.recognize_whisper(
                audio, model=self._model_size, language=self._language,
            )
            return text.strip() if text else None
        except sr.UnknownValueError:
            return None
        except Exception as e:
            logger.debug(f"Whisper 失败: {e}")

        # 不降级到 Google STT（避免将用户音频发送到外部服务器）
        return None

    def reset(self) -> None:
        """清空事件缓存"""
        with self._events_lock:
            self._events.clear()
