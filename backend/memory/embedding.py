"""
Embedding engine — text vectorization via SentenceTransformer.
Ported from reference, minimal changes.
"""
import os
import threading
from typing import List, Optional
import numpy as np

from backend.config.settings import settings
from backend.utils.logger import setup_logger

logger = setup_logger("memory_embedding")


class EmbeddingEngine:
    """Vectorization engine (singleton, lazy-loaded)."""

    _instance: "EmbeddingEngine" = None
    _lock = threading.Lock()

    def __init__(self):
        self._model = None
        self._tokenizer = None
        self._loaded = False
        self._attempted = False
        self.dim = 768

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
            model_name = settings.EMBEDDING_MODEL
            cache_folder = settings.EMBEDDING_CACHE_FOLDER

            if settings.EMBEDDING_LOCAL_FILES_ONLY:
                os.environ["HF_HUB_OFFLINE"] = "1"
                os.environ["TRANSFORMERS_OFFLINE"] = "1"

            hf_mirror = settings.HF_MIRROR
            if hf_mirror:
                os.environ["HF_ENDPOINT"] = hf_mirror

            from transformers import AutoModel, AutoTokenizer

            repo_id = f"sentence-transformers/{model_name}"
            cache_dir = str(os.path.abspath(cache_folder)) if cache_folder else None

            self._model = AutoModel.from_pretrained(repo_id, cache_dir=cache_dir, local_files_only=True)
            self._tokenizer = AutoTokenizer.from_pretrained(repo_id, cache_dir=cache_dir, local_files_only=True)

            self.dim = self._model.config.hidden_size
            self._loaded = True
            logger.info(f"Embedding model loaded: {model_name} (dim={self.dim})")

            self._rebuild_faiss_if_needed()
            return True
        except Exception as e:
            logger.warning(f"Embedding model load failed: {e}")
            return False

    def _rebuild_faiss_if_needed(self):
        index_path = settings.MEMORY_FAISS_INDEX
        id_map_path = settings.MEMORY_ID_MAP

        old_dim = None
        if os.path.exists(index_path):
            try:
                import faiss
                old = faiss.read_index(index_path)
                old_dim = old.d
            except Exception:
                pass

        if old_dim == self.dim:
            return

        logger.info(f"FAISS index dimension mismatch (old={old_dim}, new={self.dim}), rebuilding...")
        for path in [index_path, id_map_path]:
            if os.path.exists(path):
                os.remove(path)

        try:
            from backend.memory.event_store import EventStore
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
                    import json
                    with open(id_map_path, "w") as fp:
                        json.dump([ev.id for ev, _ in valid], fp)
                    logger.info(f"FAISS index rebuilt: {len(valid)} vectors (dim={self.dim})")
        except Exception as e:
            logger.warning(f"FAISS index rebuild failed: {e}")

    def embed(self, text: str) -> Optional[List[float]]:
        if not self._loaded or self._model is None:
            return None
        try:
            import torch
            inputs = self._tokenizer(text, return_tensors="pt", padding=True, truncation=True, max_length=128)
            with torch.no_grad():
                outputs = self._model(**inputs)
            vec = outputs.last_hidden_state.mean(dim=1).squeeze().numpy()
            norm = float(np.sqrt((vec ** 2).sum()))
            if norm > 1e-12:
                vec = vec / norm
            return vec.tolist()
        except Exception as e:
            logger.warning(f"Embedding failed: {e}")
            return None

    def embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        if not texts or not self._loaded or self._model is None:
            return [None] * len(texts) if texts else []
        try:
            import torch
            inputs = self._tokenizer(texts, return_tensors="pt", padding=True, truncation=True, max_length=128)
            with torch.no_grad():
                outputs = self._model(**inputs)
            vecs = outputs.last_hidden_state.mean(dim=1).numpy()
            norms = np.sqrt((vecs ** 2).sum(axis=1)).reshape(-1, 1)
            vecs = np.divide(vecs, norms, out=np.zeros_like(vecs), where=norms > 1e-12)
            return [v.tolist() for v in vecs]
        except Exception as e:
            logger.warning(f"Batch embedding failed: {e}")
            return [None] * len(texts)
