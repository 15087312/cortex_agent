"""
Embedding 工具类 - 文本向量化

提供:
- 延迟加载 SentenceTransformer
- 同步/异步 embedding 生成
- 模型缓存
"""
from typing import Optional, List, Union
import threading
import numpy as np
from utils.logger import setup_logger

logger = setup_logger("memory_embeddings")

DEFAULT_MODEL = "all-MiniLM-L6-v2"


class EmbeddingGenerator:
    """Embedding 生成器"""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: str = "cpu",
        cache_folder: str = "data/memory/embeddings/models",
        local_files_only: bool = True,
    ):
        """
        初始化 embedding 生成器

        Args:
            model_name: 模型名称
            device: 设备 (cpu/mps)
            cache_folder: 模型缓存目录
            local_files_only: 仅使用本地缓存，避免服务运行时阻塞下载
        """
        self.model_name = model_name
        self.device = device
        self.cache_folder = cache_folder
        self.local_files_only = local_files_only
        self._model = None
        self._model_loaded = False
        self._load_attempted = False

    def _load_model(self) -> bool:
        """延迟加载模型"""
        if self._load_attempted:
            return self._model_loaded

        self._load_attempted = True

        try:
            from sentence_transformers import SentenceTransformer

            model_kwargs = {
                "device": self.device,
                "cache_folder": self.cache_folder,
                "local_files_only": self.local_files_only,
            }
            try:
                self._model = SentenceTransformer(self.model_name, **model_kwargs)
            except TypeError:
                if self.local_files_only:
                    raise
                model_kwargs.pop("local_files_only", None)
                self._model = SentenceTransformer(self.model_name, **model_kwargs)
            self._model_loaded = True
            logger.info(f"Embedding 模型加载成功: {self.model_name}")

            return True

        except Exception as e:
            logger.error(f"Embedding 模型加载失败: {e}")
            self._model_loaded = False
            return False

    @property
    def embedding_dim(self) -> int:
        """获取向量维度"""
        if not self._load_model():
            # Q-11: Raise exception instead of returning default to prevent dimension mismatch
            raise RuntimeError(
                f"Failed to load embedding model '{self.model_name}'. "
                "Vector dimension cannot be determined. "
                "Check model availability and cache folder."
            )

        return self._model.get_sentence_embedding_dimension()

    def encode(
        self,
        texts: Union[str, List[str]],
        batch_size: int = 32,
        show_progress: bool = False,
        convert_to_numpy: bool = True
    ) -> Optional[np.ndarray]:
        """
        生成 embedding 向量

        Args:
            texts: 单个文本或文本列表
            batch_size: 批处理大小
            show_progress: 是否显示进度
            convert_to_numpy: 是否转换为 numpy 数组

        Returns:
            向量数组，失败返回 None
        """
        if not self._load_model():
            return None

        try:
            if isinstance(texts, str):
                texts = [texts]

            embeddings = self._model.encode(
                texts,
                batch_size=batch_size,
                show_progress_bar=show_progress,
                convert_to_numpy=convert_to_numpy,
                normalize_embeddings=True
            )

            return embeddings

        except Exception as e:
            logger.error(f"生成 embedding 失败: {e}")
            return None

    def encode_async(
        self,
        texts: Union[str, List[str]],
        **kwargs
    ) -> Optional[np.ndarray]:
        """异步生成 embedding（同步封装）"""
        return self.encode(texts, **kwargs)

    def similarity(
        self,
        text1: str,
        text2: str
    ) -> float:
        """
        计算两个文本的相似度

        Args:
            text1: 文本1
            text2: ���本2

        Returns:
            相似度分数 [-1, 1]
        """
        emb1 = self.encode(text1)
        emb2 = self.encode(text2)

        if emb1 is None or emb2 is None:
            return 0.0

        if isinstance(emb1, np.ndarray):
            emb1 = emb1.reshape(1, -1)
        if isinstance(emb2, np.ndarray):
            emb2 = emb2.reshape(1, -1)

        from sklearn.metrics.pairwise import cosine_similarity

        sim = cosine_similarity(emb1, emb2)[0][0]

        return float(sim)

    def batch_encode(
        self,
        texts: List[str],
        batch_size: int = 32
    ) -> List[Optional[np.ndarray]]:
        """
        批量生成 embedding（保持顺序）

        Args:
            texts: 文本列表
            batch_size: 批处理大小

        Returns:
            embedding 列表
        """
        if not texts:
            return []

        embeddings = self.encode(texts, batch_size=batch_size)

        if embeddings is None:
            return [None] * len(texts)

        if isinstance(embeddings, np.ndarray):
            if embeddings.ndim == 1:
                return [embeddings]
            return list(embeddings)

        return embeddings.tolist() if hasattr(embeddings, 'tolist') else embeddings

    def get_embedding(self, text: str) -> Optional[np.ndarray]:
        """
        获取单个文本的 embedding

        Args:
            text: 输入文本

        Returns:
            向量或 None
        """
        return self.encode(text)


_embedding_generator: Optional[EmbeddingGenerator] = None
_embedding_lock = threading.Lock()


def get_embedding_generator() -> EmbeddingGenerator:
    """获取全局 embedding 生成器单例（线程安全）"""
    global _embedding_generator

    if _embedding_generator is None:
        with _embedding_lock:
            if _embedding_generator is None:
                try:
                    from config.settings import settings
                    _embedding_generator = EmbeddingGenerator(
                        model_name=settings.EMBEDDING_MODEL,
                        device=settings.EMBEDDING_DEVICE,
                        cache_folder=settings.EMBEDDING_CACHE_FOLDER,
                        local_files_only=settings.EMBEDDING_LOCAL_FILES_ONLY,
                    )
                except Exception as e:
                    logger.warning("加载自定义 embedding 配置失败，使用默认配置: %s", e)
                    _embedding_generator = EmbeddingGenerator()

    return _embedding_generator


def get_embedding(text: str) -> Optional[np.ndarray]:
    """
    快速获取文本 embedding

    Args:
        text: 输入文本

    Returns:
        向量或 None
    """
    return get_embedding_generator().get_embedding(text)


def get_embeddings(texts: List[str]) -> List[Optional[np.ndarray]]:
    """
    批量获取文本 embedding

    Args:
        texts: 文本列表

    Returns:
        向量列表
    """
    return get_embedding_generator().batch_encode(texts)