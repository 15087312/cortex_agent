"""桌宠语音识别 — 本地 Whisper / API 后端，模型实例缓存复用

原 StreamingASR 每次转写都 `whisper.load_model(...)`（每次几百 MB 重新加载），
实时监听下等于不可用。这里统一做：
- 进程内模型缓存（按 size 缓存，重复转写只加载一次）
- 模型可用性降级：配置的 size 未下载时，退回已缓存的最大可用模型，
  不让一次联网下载卡住实时监听
- API 后端（PERCEPTION_VOICE_BACKEND=api）直接走现有 transcribe_with_api
"""
import io
import os
import wave
from typing import Optional

from utils.logger import setup_logger

logger = setup_logger("pet_voice_asr")

_MODEL_CACHE = {}
# 降级顺序：配置的模型不可用时，依次尝试这些已缓存的小模型
_FALLBACK_SIZES = ("base", "tiny")


def _whisper_cache_dir() -> str:
    return os.path.expanduser("~/.cache/whisper")


def _cached_model_sizes() -> list:
    root = _whisper_cache_dir()
    try:
        return [
            os.path.basename(p)[:-3]
            for p in os.listdir(root)
            if p.endswith(".pt")
        ]
    except OSError:
        return []


def resolve_whisper_size(preferred: Optional[str] = None) -> Optional[str]:
    """选一个本地已缓存的模型：优先配置值，其次 base/tiny，最后任意一个已缓存模型。

    返回 None 表示本地一个模型都没有（调用方决定是否联网下载/报错）。
    """
    preferred = (preferred or "").strip()
    cached = _cached_model_sizes()

    def _has(size: str) -> bool:
        return size in cached or size in _MODEL_CACHE

    if preferred and _has(preferred):
        return preferred
    for size in _FALLBACK_SIZES:
        if _has(size):
            return size
    if cached:
        return cached[0]
    return None


def load_whisper(preferred: Optional[str] = None):
    """加载（并缓存）本地 Whisper 模型；一个可用模型都没有时返回 None。"""
    size = resolve_whisper_size(preferred)
    if not size:
        logger.warning("本地没有可用的 Whisper 模型（~/.cache/whisper 为空），语音识别不可用")
        return None
    with_lock = _MODEL_CACHE.get(size)
    if with_lock is not None:
        return with_lock
    try:
        import whisper
        model = whisper.load_model(size)
        _MODEL_CACHE[size] = model
        logger.info(f"Whisper 模型已加载并缓存: {size}")
        return model
    except Exception as e:
        logger.warning(f"Whisper 模型加载失败（{size}）: {e}")
        return None


def pcm_to_wav(pcm_data: bytes, sample_rate: int = 16000) -> bytes:
    """16bit mono PCM → WAV 字节"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


def transcribe_pcm(
    pcm_data: bytes,
    sample_rate: int = 16000,
    language: Optional[str] = None,
    model_size: Optional[str] = None,
) -> Optional[str]:
    """PCM(16bit mono) → 文本；失败返回 None"""
    if not pcm_data:
        return None
    wav_data = pcm_to_wav(pcm_data, sample_rate)
    try:
        from config.settings import settings
        backend = getattr(settings, "PERCEPTION_VOICE_BACKEND", "local")
        lang = language or getattr(settings, "PERCEPTION_VOICE_LANGUAGE", "zh") or "zh"
        size = model_size or getattr(settings, "PERCEPTION_VOICE_MODEL", "base")
        if backend == "api":
            from infra.data_process.core.speech_recognizer import transcribe_with_api
            text = transcribe_with_api(wav_data, lang, sample_rate)
            return (text or "").strip() or None
        model = load_whisper(size)
        if model is None:
            return None
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_data)
            tmp_path = f.name
        try:
            result = model.transcribe(tmp_path, language=lang)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return (result.get("text", "") or "").strip() or None
    except Exception as e:
        logger.debug(f"语音识别失败: {e}")
        return None
