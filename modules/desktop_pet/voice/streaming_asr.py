"""流式 ASR — 累积音频分段转写

前端 WebSocket 持续发送 PCM chunk，本模块：
1. 累积语音 chunk
2. 每 SEGMENT_INTERVAL 做一次 partial 转写（实时反馈）
3. 静音结束后做 final 转写（送入 PetEngine）

复用现有 Whisper（local）或 API 后端（PERCEPTION_VOICE_BACKEND）。
"""
import io
import threading
import time
import wave
from typing import Callable, Optional

from utils.logger import setup_logger

logger = setup_logger("pet_streaming_asr")


class StreamingASR:
    """流式语音转写

    on_partial: 中间转写结果（前端实时显示）
    on_final: 最终转写结果（用户说完一句）
    """

    SEGMENT_INTERVAL = 3.0  # 每 3 秒做一次 partial 转写

    def __init__(
        self,
        sample_rate: int = 16000,
        language: str = "zh",
        on_partial: Optional[Callable[[str], None]] = None,
        on_final: Optional[Callable[[str], None]] = None,
    ):
        self._sample_rate = sample_rate
        self._language = language
        self._on_partial = on_partial
        self._on_final = on_final

        self._buffer: list[bytes] = []
        self._lock = threading.Lock()
        self._last_partial_time = 0.0
        self._running = False

    def start(self):
        self._running = True
        with self._lock:
            self._buffer.clear()
            self._last_partial_time = time.time()

    def stop(self):
        self._running = False

    def reset(self):
        with self._lock:
            self._buffer.clear()
            self._last_partial_time = time.time()

    def feed(self, audio_chunk: bytes):
        """接收一段 PCM 音频（16kHz 16bit mono）"""
        if not self._running:
            return
        with self._lock:
            self._buffer.append(audio_chunk)
            elapsed = time.time() - self._last_partial_time

        if elapsed >= self.SEGMENT_INTERVAL:
            self._do_partial()

    def finalize(self) -> Optional[str]:
        """用户停止说话，做最终转写"""
        text = self._transcribe_chunk(self._get_buffered())
        if text:
            if self._on_final:
                self._on_final(text)
            return text
        return None

    def _do_partial(self):
        with self._lock:
            self._last_partial_time = time.time()
            audio_data = self._get_buffered_locked()
        text = self._transcribe_chunk(audio_data)
        if text and self._on_partial:
            self._on_partial(text)

    def _get_buffered(self) -> bytes:
        with self._lock:
            return self._get_buffered_locked()

    def _get_buffered_locked(self) -> bytes:
        return b"".join(self._buffer)

    def _transcribe_chunk(self, audio_data: bytes) -> Optional[str]:
        if not audio_data:
            return None
        try:
            wav_buf = self._pcm_to_wav(audio_data)
            return self._transcribe_wav(wav_buf)
        except Exception as e:
            logger.debug(f"ASR 转写失败: {e}")
            return None

    def _transcribe_wav(self, wav_data: bytes) -> Optional[str]:
        from config.settings import settings
        backend = getattr(settings, "PERCEPTION_VOICE_BACKEND", "local")

        if backend == "api":
            return self._transcribe_api(wav_data)
        return self._transcribe_local(wav_data)

    def _transcribe_local(self, wav_data: bytes) -> Optional[str]:
        import whisper
        from config.settings import settings
        model_size = getattr(settings, "PERCEPTION_VOICE_MODEL", "tiny")
        model = whisper.load_model(model_size)
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_data)
            tmp_path = f.name
        try:
            result = model.transcribe(tmp_path, language=self._language)
            return result.get("text", "").strip() or None
        finally:
            os.unlink(tmp_path)

    def _transcribe_api(self, wav_data: bytes) -> Optional[str]:
        from infra.data_process.core.speech_recognizer import transcribe_with_api
        return transcribe_with_api(wav_data, self._language, self._sample_rate)

    @staticmethod
    def _pcm_to_wav(pcm_data: bytes, sample_rate: int = 16000) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_data)
        return buf.getvalue()
