"""语音轮次状态机 — 管理全双工语音的对话轮次

状态转换：
  IDLE → LISTENING（VAD 检测到语音）
  LISTENING → THINKING（静音结束，ASR 完成）
  THINKING → SPEAKING（PetEngine 回复，TTS 开始播放）
  SPEAKING → IDLE（TTS 播放完成）
  SPEAKING → LISTENING（用户打断，VAD 再次检测到语音）
"""
import enum
import threading
import time
from typing import Callable, Optional

from utils.logger import setup_logger

logger = setup_logger("pet_turn_manager")


class VoiceState(enum.Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


class TurnManager:
    """语音轮次管理器

    on_state_change: 状态变化回调（通知前端更新 UI）
    on_interrupt: 用户打断 TTS 播放时的回调
    """

    def __init__(
        self,
        silence_duration: float = 1.5,
        on_state_change: Optional[Callable[[VoiceState], None]] = None,
        on_interrupt: Optional[Callable[[], None]] = None,
    ):
        self._silence_duration = silence_duration
        self._on_state_change = on_state_change
        self._on_interrupt = on_interrupt

        self._state = VoiceState.IDLE
        self._lock = threading.Lock()
        self._speech_start_time = 0.0
        self._last_speech_time = 0.0

    @property
    def state(self) -> VoiceState:
        with self._lock:
            return self._state

    def _set_state(self, new_state: VoiceState):
        with self._lock:
            old = self._state
            if old == new_state:
                return
            self._state = new_state
        logger.debug(f"语音状态: {old.value} → {new_state.value}")
        if self._on_state_change:
            try:
                self._on_state_change(new_state)
            except Exception as e:
                logger.debug(f"状态回调失败: {e}")

    def on_speech_start(self):
        """VAD 检测到语音开始"""
        with self._lock:
            current = self._state
            self._speech_start_time = time.time()
            self._last_speech_time = time.time()

        if current == VoiceState.SPEAKING:
            if self._on_interrupt:
                self._on_interrupt()
            logger.info("用户打断 TTS")

        self._set_state(VoiceState.LISTENING)

    def on_speech_chunk(self):
        """持续收到语音 chunk（更新最后语音时间）"""
        with self._lock:
            self._last_speech_time = time.time()

    def check_silence(self) -> bool:
        """检查是否已静音超过 silence_duration

        返回 True 表示用户已说完，应触发 final ASR。
        """
        with self._lock:
            if self._state != VoiceState.LISTENING:
                return False
            elapsed = time.time() - self._last_speech_time
        return elapsed >= self._silence_duration

    def on_thinking_start(self):
        """开始调用 PetEngine"""
        self._set_state(VoiceState.THINKING)

    def on_thinking_end(self):
        """PetEngine 回复完成，开始 TTS"""
        self._set_state(VoiceState.SPEAKING)

    def on_speaking_end(self):
        """TTS 播放完成"""
        self._set_state(VoiceState.IDLE)

    def reset(self):
        """重置为 IDLE"""
        self._set_state(VoiceState.IDLE)
        with self._lock:
            self._speech_start_time = 0.0
            self._last_speech_time = 0.0
