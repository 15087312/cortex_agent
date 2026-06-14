"""
Embedding 工具 — 向量化文本
复用项目已有的 SentenceTransformer 模式，提供延迟加载和缓存。
"""
import threading
from typing import List, Optional

from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("memory_embedding")


class EmbeddingEngine:
    """向量化引擎（单例，延迟加载）"""

    _instance: "EmbeddingEngine" = None
    _lock = threading.Lock()

    def __init__(self):
        self._model = None
        self._loaded = False
        self._attempted = False
        self.dim = 768  # paraphrase-multilingual-MiniLM-L12-v2

    @classmethod
    def get_instance(cls) -> "EmbeddingEngine":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _load_model(self) -> bool:
        if self._attempted:
            return self._loaded
        self._attempted = True

        try:
            from sentence_transformers import SentenceTransformer

            model_name = getattr(settings, "EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
            cache_folder = getattr(settings, "EMBEDDING_CACHE_FOLDER", None)
            local_files_only = getattr(settings, "EMBEDDING_LOCAL_FILES_ONLY", False)

            model_kwargs = {"cache_folder": cache_folder}
            try:
                self._model = SentenceTransformer(model_name, local_files_only=local_files_only, **model_kwargs)
            except TypeError:
                model_kwargs.pop("local_files_only", None)
                self._model = SentenceTransformer(model_name, **model_kwargs)

            self.dim = self._model.get_sentence_embedding_dimension()
            self._loaded = True
            logger.info(f"[Embedding] 模型加载成功: {model_name} (dim={self.dim})")
            return True
        except Exception as e:
            logger.warning(f"[Embedding] 模型加载失败: {e}")
            return False

    def embed(self, text: str) -> Optional[List[float]]:
        """将文本转为向量"""
        if not self._load_model():
            return None
        try:
            vec = self._model.encode(text, normalize_embeddings=True)
            return vec.tolist()
        except Exception as e:
            logger.warning(f"[Embedding] 编码失败: {e}")
            return None

    def embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        """批量向量化"""
        if not texts:
            return []
        if not self._load_model():
            return [None] * len(texts)
        try:
            vecs = self._model.encode(texts, normalize_embeddings=True)
            return [v.tolist() for v in vecs]
        except Exception as e:
            logger.warning(f"[Embedding] 批量编码失败: {e}")
            return [None] * len(texts)
