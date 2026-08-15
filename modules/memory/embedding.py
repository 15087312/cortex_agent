"""
Embedding 工具 — 向量化文本
复用项目已有的 SentenceTransformer 模式，提供延迟加载和缓存。
"""
import os
import threading
from typing import List, Optional
import numpy as np

from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("memory_embedding")


class EmbeddingEngine:
    """向量化引擎（单例，延迟加载）"""

    _instance: Optional["EmbeddingEngine"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._model = None
        self._loaded = False
        self._attempted = False
        # 模型加载锁：EventStore 后台向量化线程与主线程可能并发触发 _load_model，
        # 并发 import torch + from_pretrained 会段错误（macOS libomp/线程竞态），必须串行
        self._load_lock = threading.Lock()
        # 推理锁：双 libomp（faiss+torch 各捆绑一份）并存时，并发 torch 推理会段错误。
        # worker 后台线程与主线程同时 embed 必须串行（见 docs/ERRORS_AND_FIXES.md §27）
        self._infer_lock = threading.Lock()
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
        # 双检锁：只允许一个线程真正加载（后台 worker 与主线程可能同时触发）
        with self._load_lock:
            if self._attempted:
                return self._loaded
            self._attempted = True

            try:
                model_name = getattr(settings, "EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
                cache_folder = getattr(settings, "EMBEDDING_CACHE_FOLDER", None)
                local_only = bool(getattr(settings, "EMBEDDING_LOCAL_FILES_ONLY", False))

                # 本地模式时彻底禁止联网
                if local_only:
                    os.environ["HF_HUB_OFFLINE"] = "1"
                    os.environ["TRANSFORMERS_OFFLINE"] = "1"

                # 支持 HuggingFace 镜像（国内环境：https://hf-mirror.com）
                hf_mirror = getattr(settings, "HF_MIRROR", "")
                if hf_mirror:
                    os.environ["HF_ENDPOINT"] = hf_mirror

                # 直接用 transformers 加载（比 SentenceTransformer 轻量，无 post-init 卡死问题）
                from transformers import AutoModel, AutoTokenizer

                repo_id = f"sentence-transformers/{model_name}"
                cache_dir = str(os.path.abspath(cache_folder)) if cache_folder else None

                # 先本地加载（缓存存在时快速离线，不触发网络）；缓存缺失时（全新部署）
                # 才联网下载。EMBEDDING_LOCAL_FILES_ONLY=True 时只允许本地。
                last_err = None
                for allow_download in ((False,) if local_only else (False, True)):
                    try:
                        self._model = AutoModel.from_pretrained(
                            repo_id, cache_dir=cache_dir, local_files_only=not allow_download)
                        self._tokenizer = AutoTokenizer.from_pretrained(
                            repo_id, cache_dir=cache_dir, local_files_only=not allow_download)
                        break
                    except Exception as e:  # noqa: PERF203
                        last_err = e
                else:
                    raise last_err or RuntimeError(f"模型加载失败: {repo_id}")

                self.dim = self._model.config.hidden_size
                self._loaded = True
                logger.info(f"[Embedding] 模型加载成功: {model_name} (dim={self.dim})")

                # 重建 FAISS 索引（维度变更时不兼容的旧索引会导致向量搜索返回空）
                self._rebuild_faiss_if_needed()

                return True
            except Exception as e:
                logger.warning(f"[Embedding] 模型加载失败: {e}")
                return False

    def _rebuild_faiss_if_needed(self):
        """检查 FAISS 索引维度，不一致时重建（新记忆库切换后索引不存在也会触发）"""
        import os, json
        import numpy as np
        index_path = getattr(settings, "MEMORY_FAISS_INDEX", "data/events_faiss.index")
        id_map_path = getattr(settings, "MEMORY_ID_MAP", "data/events_id_map.json")

        from utils.faiss_lock import faiss_file_lock

        with faiss_file_lock(index_path):
            old_dim = None
            if os.path.exists(index_path):
                try:
                    import faiss
                    old = faiss.read_index(index_path)
                    old_dim = old.d
                except Exception:
                    old_dim = None

            if old_dim == self.dim:
                return  # 维度一致，不需要重建

            # 新记忆库索引不存在 或 维度不一致 → 重建
            if not os.path.exists(index_path):
                logger.info(f"[Embedding] FAISS 索引不存在（新记忆库），创建索引 (dim={self.dim})")
            else:
                logger.info(f"[Embedding] FAISS 索引维度不匹配（旧={old_dim}, 新={self.dim}），重建中...")
            for path in [index_path, id_map_path]:
                if os.path.exists(path):
                    os.remove(path)

            # 从 EventStore 读取所有事件，重新向量化并写入 FAISS
            try:
                from modules.memory.event_store import EventStore
                store = EventStore.get_instance()
                events = store.list_events(limit=5000)
                if events:
                    texts = [f"{ev.fact} {ev.thought} {ev.lesson}".strip() for ev in events]
                    vecs = self.embed_batch(texts)
                    valid = [(ev, v) for ev, v in zip(events, vecs) if v is not None]
                    if valid:
                        import faiss
                        idx = faiss.IndexFlatIP(self.dim)
                        matrix = np.array([v for _, v in valid], dtype=np.float32)
                        idx.add(matrix)
                        faiss.write_index(idx, index_path)
                        with open(id_map_path, "w") as fp:
                            json.dump([ev.id for ev, _ in valid], fp)
                        logger.info(f"[Embedding] FAISS 索引重建完成: {len(valid)} 向量 (dim={self.dim})")
            except Exception as e:
                logger.warning(f"[Embedding] FAISS 索引重建失败，向量搜索暂时不可用: {e}")

    def embed(self, text: str) -> Optional[List[float]]:
        if not self._loaded:
            self._load_model()  # 延迟加载：首次调用自动加载，避免向量检索静默失效
        if not self._loaded or self._model is None:
            return None
        try:
            # 串行化 torch 推理：worker 后台线程与主线程不可并发进入（双 libomp 段错误）
            with self._infer_lock:
                import torch
                inputs = self._tokenizer(text, return_tensors="pt", padding=True, truncation=True, max_length=128)
                with torch.no_grad():
                    outputs = self._model(**inputs)
                vec = outputs.last_hidden_state.mean(dim=1).squeeze().numpy()
                # L2 归一化（与 SentenceTransformer 默认行为一致）
                norm = float(np.sqrt((vec ** 2).sum()))
                if norm > 1e-12:
                    vec = vec / norm
                return vec.tolist()
        except Exception as e:
            logger.warning(f"[Embedding] 编码失败: {e}")
            return None

    def embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        if not texts:
            return []
        if not self._loaded:
            self._load_model()
        if not self._loaded or self._model is None:
            return [None] * len(texts)
        try:
            # 串行化 torch 推理（同 embed，双 libomp 环境下并发推理段错误）
            with self._infer_lock:
                import torch
                inputs = self._tokenizer(texts, return_tensors="pt", padding=True, truncation=True, max_length=128)
                with torch.no_grad():
                    outputs = self._model(**inputs)
                vecs = outputs.last_hidden_state.mean(dim=1).numpy()
                norms = np.sqrt((vecs ** 2).sum(axis=1)).reshape(-1, 1)
                vecs = np.divide(vecs, norms, out=np.zeros_like(vecs), where=norms > 1e-12)
                return [v.tolist() for v in vecs]
        except Exception as e:
            logger.warning(f"[Embedding] 批量编码失败: {e}")
            return [None] * len(texts)
