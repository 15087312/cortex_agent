"""
声纹识别与说话人分类 - 基于 Resemblyzer (d-vector)
支持：声纹特征提取、说话人注册、说话人识别、声纹库管理
"""
import asyncio
import json
import os
import io
import tempfile
import numpy as np
from typing import Dict, Any, Optional, List
from utils.logger import setup_logger

logger = setup_logger("speaker_classifier")

DEFAULT_EMBEDDING_DIM = 256
DEFAULT_SIMILARITY_THRESHOLD = 0.75


class SpeakerClassifier:
    """声纹分类器：提取说话人嵌入、注册/识别说话人"""

    def __init__(self, data_dir: str = "data/speaker_profiles"):
        self.data_dir = data_dir
        self.encoder = None
        self._initialized = False
        self._speakers: Dict[str, np.ndarray] = {}
        self._speaker_meta: Dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def initialize(self):
        if self._initialized:
            return
        await self._load_encoder()
        self._load_profiles()
        self._initialized = True
        logger.info(f"声纹分类器初始化完成 (已加载 {len(self._speakers)} 个说话人)")

    async def _load_encoder(self):
        try:
            from resemblyzer import VoiceEncoder
            self.encoder = VoiceEncoder()
            logger.info("Resemblyzer VoiceEncoder 加载成功")
        except ImportError:
            raise ImportError(
                "Resemblyzer 未安装。请运行 'pip install resemblyzer' 安装声纹识别依赖"
            )

    def _profiles_path(self) -> str:
        return os.path.join(self.data_dir, "speaker_profiles.json")

    def _embeddings_path(self) -> str:
        return os.path.join(self.data_dir, "speaker_embeddings.npz")

    def _load_profiles(self):
        pp = self._profiles_path()
        if os.path.exists(pp):
            try:
                with open(pp, "r", encoding="utf-8") as f:
                    self._speaker_meta = json.load(f)
            except Exception as e:
                logger.warning(f"加载说话人元数据失败: {e}")
                self._speaker_meta = {}

        ep = self._embeddings_path()
        if os.path.exists(ep):
            try:
                data = np.load(ep)
                for name in data.files:
                    self._speakers[name] = data[name]
            except Exception as e:
                logger.warning(f"加载说话人嵌入失败: {e}")
                self._speakers = {}

    def _save_profiles(self):
        os.makedirs(self.data_dir, exist_ok=True)
        try:
            with open(self._profiles_path(), "w", encoding="utf-8") as f:
                json.dump(self._speaker_meta, f, ensure_ascii=False, indent=2)
            if self._speakers:
                np.savez(self._embeddings_path(), **self._speakers)
            else:
                ep = self._embeddings_path()
                if os.path.exists(ep):
                    os.remove(ep)
        except Exception as e:
            logger.error(f"保存说话人档案失败: {e}")

    async def extract_embedding(self, audio_bytes: bytes, sample_rate: int = 16000) -> Optional[np.ndarray]:
        """从音频字节提取说话人嵌入向量 (256-dim)"""
        if not self._initialized:
            await self.initialize()

        try:
            from resemblyzer import preprocess_wav
            import soundfile as sf

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            try:
                wav, sr = sf.read(tmp_path, dtype="float32")
                if len(wav.shape) > 1:
                    wav = wav.mean(axis=1)
                if sr != sample_rate:
                    import librosa
                    wav = librosa.resample(wav, orig_sr=sr, target_sr=sample_rate)
                    sr = sample_rate

                processed = preprocess_wav(wav, source_sr=sample_rate)
                if len(processed) < sample_rate * 0.3:
                    logger.warning("音频太短（<0.3s），无法提取有效声纹")
                    return None

                embedding = self.encoder.embed_utterance(processed)
                return embedding
            finally:
                os.unlink(tmp_path)

        except Exception as e:
            logger.error(f"声纹提取失败: {e}")
            return None

    async def identify_speaker(
        self,
        audio_bytes: bytes,
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> Optional[Dict[str, Any]]:
        """识别说话人：提取声纹 → 与已注册说话人比对 → 返回最匹配结果"""
        embedding = await self.extract_embedding(audio_bytes)
        if embedding is None:
            return None

        async with self._lock:
            if not self._speakers:
                return {
                    "identified": False,
                    "speaker": None,
                    "similarity": 0.0,
                    "embedding": embedding.tolist(),
                }

            best_name = None
            best_sim = -1.0
            for name, stored_emb in self._speakers.items():
                sim = float(np.dot(embedding, stored_emb) / (
                    np.linalg.norm(embedding) * np.linalg.norm(stored_emb) + 1e-8
                ))
                if sim > best_sim:
                    best_sim = sim
                    best_name = name

            identified = best_sim >= threshold
            return {
                "identified": identified,
                "speaker": best_name if identified else None,
                "similarity": round(best_sim, 4),
                "threshold": threshold,
                "embedding": embedding.tolist(),
            }

    async def register_speaker(self, name: str, audio_bytes: bytes) -> Dict[str, Any]:
        """注册说话人：从音频提取声纹并存储"""
        embedding = await self.extract_embedding(audio_bytes)
        if embedding is None:
            return {"success": False, "error": "声纹提取失败，请检查音频质量"}

        async with self._lock:
            normalized = embedding / (np.linalg.norm(embedding) + 1e-8)
            self._speakers[name] = normalized
            self._speaker_meta[name] = {
                "name": name,
                "embedding_dim": len(embedding),
                "registered_at": __import__("datetime").datetime.now().isoformat(),
            }
            self._save_profiles()

        logger.info(f"说话人注册成功: {name}")
        return {"success": True, "speaker": name, "embedding_dim": len(embedding)}

    async def register_speaker_from_embedding(self, name: str, embedding_list: List[float]) -> Dict[str, Any]:
        """从已有嵌入向量注册说话人（前端识别后直接标记）"""
        async with self._lock:
            emb = np.array(embedding_list, dtype=np.float32)
            normalized = emb / (np.linalg.norm(emb) + 1e-8)
            self._speakers[name] = normalized
            self._speaker_meta[name] = {
                "name": name,
                "embedding_dim": len(embedding_list),
                "registered_at": __import__("datetime").datetime.now().isoformat(),
            }
            self._save_profiles()

        logger.info(f"说话人注册成功 (from embedding): {name}")
        return {"success": True, "speaker": name}

    async def list_speakers(self) -> List[Dict[str, Any]]:
        async with self._lock:
            result = []
            for name, meta in self._speaker_meta.items():
                result.append({
                    "name": name,
                    "registered_at": meta.get("registered_at", ""),
                    "embedding_dim": meta.get("embedding_dim", DEFAULT_EMBEDDING_DIM),
                })
            return result

    async def delete_speaker(self, name: str) -> Dict[str, Any]:
        async with self._lock:
            if name not in self._speakers:
                return {"success": False, "error": f"说话人 '{name}' 不存在"}
            del self._speakers[name]
            self._speaker_meta.pop(name, None)
            self._save_profiles()
        logger.info(f"说话人删除: {name}")
        return {"success": True, "speaker": name}

    async def compare_embeddings(
        self, emb1: List[float], emb2: List[float]
    ) -> float:
        a = np.array(emb1, dtype=np.float32)
        b = np.array(emb2, dtype=np.float32)
        sim = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))
        return round(sim, 4)


_default_classifier: Optional[SpeakerClassifier] = None


async def get_default_classifier() -> SpeakerClassifier:
    global _default_classifier
    if _default_classifier is None:
        from config.settings import settings
        data_dir = getattr(settings, "VOICEPRINT_DATA_DIR", "data/speaker_profiles")
        _default_classifier = SpeakerClassifier(data_dir=data_dir)
        await _default_classifier.initialize()
    return _default_classifier
