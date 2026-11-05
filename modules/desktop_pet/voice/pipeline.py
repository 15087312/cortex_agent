"""全双工语音管线 — VAD → ASR → PetEngine → TTS

前端通过 WebSocket 发送 PCM 音频，本管线：
1. Silero VAD 检测语音开始/结束
2. StreamingASR 累积转写（partial + final）
3. TurnManager 管理轮次状态
4. 发布 SPEECH_DETECTED 事件（与现有 PetEngine 完全兼容）
5. TTS 合成后通知前端播放

向后兼容：PERCEPTION_VOICE_REALTIME=false 时不创建此管线，
使用现有 VoiceDetector（半双工）。
"""
import asyncio
import threading
import time
from typing import Any, Callable, Optional

from modules.desktop_pet.voice.vad import SileroVAD
from modules.desktop_pet.voice.streaming_asr import StreamingASR
from modules.desktop_pet.voice.turn_manager import TurnManager, VoiceState
from utils.logger import setup_logger

logger = setup_logger("pet_voice_pipeline")


class RealtimeVoicePipeline:
    """实时语音管线

    接收前端 WebSocket 发来的 PCM chunk，经过 VAD → ASR → PetEngine → TTS。
    内部发布 SPEECH_DETECTED 事件，PetEngine 无需改动。
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        language: str = "zh",
        vad_threshold: float = 0.5,
        silence_duration: float = 1.5,
    ):
        self._sample_rate = sample_rate
        self._language = language

        self._turn_manager = TurnManager(
            silence_duration=silence_duration,
            on_state_change=self._on_state_change,
            on_interrupt=self._on_interrupt,
        )
        self._vad = SileroVAD(threshold=vad_threshold, sample_rate=sample_rate)
        self._asr = StreamingASR(
            sample_rate=sample_rate,
            language=language,
            on_partial=self._on_partial,
            on_final=self._on_final,
        )

        self._running = False
        self._speech_active = False
        self._ws_send: Optional[Callable[..., Any]] = None
        self._event_bus: Optional[Any] = None
        self._lock = threading.Lock()

    def start(self, ws_send: Optional[Callable[..., Any]] = None):
        """启动管线

        Args:
            ws_send: WebSocket 发送函数（向前端推送状态/转写/TTS URL）
        """
        self._running = True
        self._ws_send = ws_send
        self._asr.start()

        try:
            from modules.perception.events.bus import get_event_bus
            self._event_bus = get_event_bus()
        except Exception as _swallowed_err:
            logger.debug(f"[start] ignored Exception: {_swallowed_err}")

        logger.info(
            f"实时语音管线启动: sr={self._sample_rate} "
            f"vad_threshold={self._vad.threshold} "
            f"silence={self._turn_manager._silence_duration}s"
        )

    def stop(self):
        self._running = False
        self._asr.stop()
        self._turn_manager.reset()
        logger.info("实时语音管线已停止")

    def feed_audio(self, pcm_chunk: bytes):
        """接收前端发来的 PCM 音频（16kHz 16bit mono）

        由 WebSocket handler 调用（可能在非主线程）。
        """
        if not self._running:
            return

        is_speech = self._vad.is_speech(pcm_chunk)

        if is_speech:
            if not self._speech_active:
                self._speech_active = True
                self._turn_manager.on_speech_start()
                self._asr.reset()
            self._turn_manager.on_speech_chunk()
            self._asr.feed(pcm_chunk)
        else:
            if self._speech_active:
                if self._turn_manager.check_silence():
                    self._speech_active = False
                    self._asr.finalize()

    def _on_state_change(self, state: VoiceState):
        """轮次状态变化 → 通知前端"""
        if self._ws_send:
            try:
                self._ws_send({"state": state.value})
            except Exception as e:
                logger.debug(f"WS 状态推送失败: {e}")

    def _on_interrupt(self):
        """用户打断 TTS → 通知前端停止播放"""
        if self._ws_send:
            try:
                self._ws_send({"action": "interrupt_tts"})
            except Exception as e:
                logger.debug(f"WS 打断推送失败: {e}")

    def _on_partial(self, text: str):
        """ASR 中间转写 → 通知前端实时显示"""
        if self._ws_send:
            try:
                self._ws_send({"partial_text": text})
            except Exception as e:
                logger.debug(f"WS partial 推送失败: {e}")

    def _on_final(self, text: str):
        """ASR 最终转写 → 发布 SPEECH_DETECTED 事件（与 VoiceDetector 兼容）"""
        logger.info(f"实时语音转写: {text[:80]}")
        if self._ws_send:
            try:
                self._ws_send({"final_text": text})
            except Exception as _swallowed_err:
                logger.debug(f"[_on_final] ignored Exception: {_swallowed_err}")

        if self._event_bus:
            try:
                from modules.perception.events.types import PerceptionEvent, PerceptionEventType
                event = PerceptionEvent(
                    event_type=PerceptionEventType.SPEECH_DETECTED,
                    source="realtime_voice",
                    importance=0.8,
                    payload={
                        "text": text,
                        "language": self._language,
                    },
                )
                self._event_bus.publish(event)
            except Exception as e:
                logger.debug(f"事件发布失败: {e}")

        self._turn_manager.on_thinking_start()

    def notify_tts_ready(self, audio_url: str):
        """TTS 合成完成 → 通知前端播放"""
        if self._ws_send:
            try:
                self._ws_send({"tts_url": audio_url})
            except Exception as e:
                logger.debug(f"WS TTS 推送失败: {e}")

    def notify_tts_finished(self):
        """TTS 播放完成 → 回到 IDLE"""
        self._turn_manager.on_speaking_end()

    @property
    def state(self) -> VoiceState:
        return self._turn_manager.state
