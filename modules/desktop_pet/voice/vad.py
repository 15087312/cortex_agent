"""Silero VAD — ML 语音活动检测

比 energy threshold 准确得多，能区分人声和键盘/风扇等噪声。
模型 ~2MB，首次调用 torch.hub.load 时自动下载。
"""
import threading
from typing import Optional

import numpy as np

from utils.logger import setup_logger

logger = setup_logger("pet_vad")


class SileroVAD:
    """Silero VAD 封装：16kHz 16bit mono PCM → 语音概率

    线程安全：内部锁保护模型推理（torch 模型非线程安全）。
    """

    def __init__(self, threshold: float = 0.5, sample_rate: int = 16000):
        self._threshold = threshold
        self._sample_rate = sample_rate
        self._model = None
        self._utils = None
        self._lock = threading.Lock()
        self._loaded = False
        self._load_error: Optional[str] = None

    def _ensure_loaded(self) -> bool:
        if self._loaded:
            return True
        if self._load_error:
            return False
        try:
            import torch
            model, utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                trust_repo=True,
            )
            self._model = model
            self._utils = utils
            self._loaded = True
            logger.info("Silero VAD 模型加载完成")
            return True
        except Exception as e:
            self._load_error = str(e)
            logger.warning(f"Silero VAD 加载失败: {e}")
            return False

    def is_speech(self, audio_chunk: bytes) -> bool:
        """判断音频块是否包含人声

        Args:
            audio_chunk: 16kHz 16bit mono PCM 字节（512 samples = 32ms）
        """
        if not self._ensure_loaded():
            return False
        model = self._model
        if model is None:
            return False
        try:
            import torch
            audio_np = np.frombuffer(audio_chunk, dtype=np.int16).astype(np.float32) / 32768.0
            tensor = torch.from_numpy(audio_np)
            with self._lock:
                confidence = model(tensor, self._sample_rate).item()
            return confidence >= self._threshold
        except Exception as e:
            logger.debug(f"VAD 推理失败: {e}")
            return False

    @property
    def threshold(self) -> float:
        return self._threshold

    @threshold.setter
    def threshold(self, value: float):
        self._threshold = max(0.0, min(1.0, value))
