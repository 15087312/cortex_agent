"""
FAISS 向量索引管理器

提供:
- FAISS 索引创建与管理
- ID 映射持久化
- 增量索引构建
- 索引保存与加载
"""
import os
import json
import threading
import numpy as np
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple, Union
from utils.logger import setup_logger

logger = setup_logger("faiss_index")


class FaissIndex:
    """FAISS 向量索引管理器"""

    def __init__(
        self,
        index_name: str,
        dimension: int = 384,
        index_type: str = "FLAT",
        data_dir: str = "data/memory/embeddings/indexes"
    ):
        """
        初始化 FAISS 索引

        Args:
            index_name: 索引名称
            dimension: 向量维度
            index_type: 索引类型 (FLAT/IVF_FLAT/HNSW)
            data_dir: 索引存储目录
        """
        self.index_name = index_name
        self.dimension = dimension
        self.index_type = index_type
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._index = None
        self._id_to_row: Dict[str, int] = {}
        self._row_to_id: Dict[int, str] = {}
        self._loaded = False
        self._deleted_count = 0  # BUG-2: Track deletions for cleanup detection

        self._index_file = self.data_dir / f"{index_name}.index"
        self._meta_file = self.data_dir / f"{index_name}.meta.jsonl"

    def _init_faiss(self):
        """初始化 FAISS 索引"""
        try:
            import faiss

            if self.index_type == "FLAT":
                self._index = faiss.IndexFlatL2(self.dimension)
            elif self.index_type == "FLAT_IP":
                self._index = faiss.IndexFlatIP(self.dimension)
            elif self.index_type == "IVF_FLAT":
                nlist = 100
                quantizer = faiss.IndexFlatL2(self.dimension)
                self._index = faiss.IndexIVFFlat(quantizer, self.dimension, nlist)
            elif self.index_type == "HNSW":
                self._index = faiss.IndexHNSWFlat(self.dimension, 32)
            else:
                self._index = faiss.IndexFlatL2(self.dimension)

            logger.info(f"FAISS 索引初始化: {self.index_name}, type={self.index_type}")

        except ImportError:
            logger.error("faiss-cpu 未安装，请运行: pip install faiss-cpu")
            raise

    def _load_meta(self) -> bool:
        """加载元数据"""
        if not self._meta_file.exists():
            return False

        try:
            self._id_to_row.clear()
            self._row_to_id.clear()

            with open(self._meta_file, 'r', encoding='utf-8') as f:
                for row_idx, line in enumerate(f):
                    if line.strip():
                        meta = json.loads(line)
                        mem_id = meta.get("id")
                        if mem_id:
                            self._id_to_row[mem_id] = row_idx
                            self._row_to_id[row_idx] = mem_id

            logger.info(f"加载元数据: {len(self._id_to_row)} 条")
            return True

        except Exception as e:
            logger.warning(f"加载元数据失败: {e}")
            return False

    def _save_meta_full(self):
        """保存完整元数据"""
        try:
            with open(self._meta_file, 'w', encoding='utf-8') as f:
                for row_idx, mem_id in self._row_to_id.items():
                    meta = {
                        "id": mem_id,
                        "row": row_idx
                    }
                    f.write(json.dumps(meta, ensure_ascii=False) + '\n')

            logger.info(f"保存元数据: {len(self._id_to_row)} 条")

        except Exception as e:
            logger.error(f"保存元数据失败: {e}")

    def load(self) -> bool:
        """加载索引"""
        if self._loaded:
            return True

        self._init_faiss()

        try:
            import faiss

            if self._index_file.exists():
                self._index = faiss.read_index(str(self._index_file))
                logger.info(f"加载索引: {self._index.ntotal} 条向量")
            else:
                logger.info("索引文件不存在，将创建新索引")

            self._load_meta()
            self._loaded = True

            return True

        except Exception as e:
            logger.error(f"加载索引失败: {e}")
            return False

    def save(self):
        """保存索引"""
        if self._index is None:
            logger.warning("索引未初始化，无法保存")
            return

        try:
            import faiss

            faiss.write_index(self._index, str(self._index_file))
            self._save_meta_full()

            logger.info(f"保存索引: {self._index.ntotal} 条向量")

        except Exception as e:
            logger.error(f"保存索引失败: {e}")

    def add(
        self,
        vectors: np.ndarray,
        mem_ids: Optional[List[str]] = None
    ) -> List[str]:
        """
        添加向量到索引

        Args:
            vectors: 向量数组 (n, dimension)
            mem_ids: 记忆 ID 列表

        Returns:
            添加的 ID 列表
        """
        if self._index is None:
            self._init_faiss()

        if vectors.ndim == 1:
            vectors = vectors.reshape(1, -1)

        n_vectors = len(vectors)

        if mem_ids is None:
            import uuid
            mem_ids = [str(uuid.uuid4()) for _ in range(n_vectors)]

        # BUG-2: Use actual index size not mapping dict size (fixes ghost vector issue)
        # len(self._row_to_id) can be less than index.ntotal after deletions
        start_row = self._index.ntotal

        try:
            import faiss
            import numpy as np

            vectors = vectors.astype('float32')

            if self.index_type == "IVF_FLAT":
                if not self._index.is_trained:
                    if n_vectors >= 10:
                        logger.info("训练 IVF 索引...")
                        train_size = min(1000, n_vectors)
                        idx = np.random.choice(n_vectors, train_size, replace=False)
                        self._index.train(vectors[idx])
                    else:
                        logger.warning("向量数量不足，跳过训练")

            self._index.add(vectors)

            for i, mem_id in enumerate(mem_ids):
                row_idx = start_row + i
                self._id_to_row[mem_id] = row_idx
                self._row_to_id[row_idx] = mem_id

            logger.debug(f"添加 {n_vectors} 条向量到索引")

            return mem_ids

        except Exception as e:
            logger.error(f"添加向量失败: {e}")
            return []

    def search(
        self,
        query_vector: np.ndarray,
        k: int = 10,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        搜索相似向量

        Args:
            query_vector: 查询向量
            k: 返回数量
            filters: 过滤条件（暂不支持）

        Returns:
            搜索结果列表
        """
        if self._index is None:
            self.load()

        if self._index.ntotal == 0:
            return []

        try:
            import faiss

            if query_vector.ndim == 1:
                query_vector = query_vector.reshape(1, -1)

            query_vector = query_vector.astype('float32')

            if self.index_type in ("FLAT", "IVF_FLAT", "HNSW"):
                distances, indices = self._index.search(query_vector, k)
            else:
                distances, indices = self._index.search(query_vector, k)

            results = []
            for dist, idx in zip(distances[0], indices[0]):
                if idx < 0:
                    continue

                row_idx = int(idx)
                mem_id = self._row_to_id.get(row_idx)

                if mem_id is None:
                    continue

                score = float(-dist) if self.index_type == "FLAT" else float(dist)

                results.append({
                    "id": mem_id,
                    "score": score,
                    "row": row_idx
                })

            return results

        except Exception as e:
            logger.error(f"搜索失败: {e}")
            return []

    def delete(self, mem_ids: List[str]) -> int:
        """
        删除向量（仅标记，不实际删除）

        Args:
            mem_ids: 要删除的记忆 ID

        Returns:
            删除数量
        """
        deleted = 0

        for mem_id in mem_ids:
            if mem_id in self._id_to_row:
                del self._id_to_row[mem_id]
                deleted += 1

        for row_idx, mem_id in list(self._row_to_id.items()):
            if mem_id in mem_ids:
                del self._row_to_id[row_idx]

        # BUG-2: Track deletion count to trigger cleanup
        self._deleted_count += deleted

        # Warn if garbage accumulation is significant
        if self._index and self._deleted_count > self._index.ntotal * 0.2:
            logger.warning(
                f"FAISS索引垃圾过多: {self._deleted_count}/{self._index.ntotal} "
                f"({100*self._deleted_count/self._index.ntotal:.1f}%). 建议调用 rebuild() 进行清理。"
            )

        logger.info(f"标记删除 {deleted} 条向量 (累计删除: {self._deleted_count})")

        return deleted

    def count(self) -> int:
        """获取向量数量"""
        if self._index is None:
            self.load()

        return self._index.ntotal if self._index else 0

    def get_id(self, row: int) -> Optional[str]:
        """根据行号获取 ID"""
        return self._row_to_id.get(row)

    def get_row(self, mem_id: str) -> Optional[int]:
        """根据 ID 获取行号"""
        return self._id_to_row.get(mem_id)

    def clear(self):
        """清空索引"""
        if self._index is not None:
            self._index.reset()

        self._id_to_row.clear()
        self._row_to_id.clear()
        self._deleted_count = 0

        logger.info("索引已清空")

    def rebuild(self) -> bool:
        """
        BUG-2: Rebuild index to remove ghost vectors from deleted items

        This method extracts all active vectors and rebuilds the index,
        eliminating space wasted by deleted-but-not-removed vectors.
        """
        if self._index is None or self._index.ntotal == 0:
            logger.info("索引为空，无需重建")
            return True

        try:
            import faiss
            import numpy as np

            logger.info(f"开始重建索引，当前 {self._index.ntotal} 条向量，"
                       f"有效 {len(self._row_to_id)} 条，垃圾 {self._deleted_count} 条")

            # Extract only active vectors
            active_vectors = []
            active_rows = []

            for row_idx, mem_id in self._row_to_id.items():
                if row_idx < self._index.ntotal:
                    # Reconstruct vector from index
                    vec = self._index.reconstruct(int(row_idx))
                    active_vectors.append(vec)
                    active_rows.append((row_idx, mem_id))

            if not active_vectors:
                logger.info("没有有效向量，清空索引")
                self.clear()
                return True

            # Create new index
            self._init_faiss()
            vectors = np.array(active_vectors, dtype='float32')
            self.add(vectors, [mem_id for _, mem_id in active_rows])

            self._deleted_count = 0
            self.save()

            logger.info(f"索引重建完成: {len(active_vectors)} 条有效向量")
            return True

        except Exception as e:
            logger.error(f"索引重建失败: {e}")
            return False


class FaissIndexManager:
    """FAISS 索引管理器 - 管理多个索引"""

    def __init__(self, data_dir: str = "data/memory/embeddings/indexes"):
        """
        初始化索引管理器

        Args:
            data_dir: 索引存储目录
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._indexes: Dict[str, FaissIndex] = {}

    def get_index(
        self,
        index_name: str,
        dimension: int = 384,
        auto_create: bool = True
    ) -> Optional[FaissIndex]:
        """
        获取或创建索引

        Args:
            index_name: 索引名称
            dimension: 向量维度
            auto_create: 是否自动创建

        Returns:
            索引对象
        """
        if index_name in self._indexes:
            return self._indexes[index_name]

        if not auto_create:
            return None

        index = FaissIndex(
            index_name=index_name,
            dimension=dimension,
            data_dir=str(self.data_dir)
        )
        index.load()

        self._indexes[index_name] = index

        return index

    def create_memory_index(
        self,
        memory_type: str,
        dimension: int = 384
    ) -> FaissIndex:
        """
        创建记忆索引

        Args:
            memory_type: 记忆类型
            dimension: 向量维度

        Returns:
            索引对象
        """
        index_name = f"memory_{memory_type}"
        return self.get_index(index_name, dimension)

    def create_expert_index(
        self,
        expert_name: str,
        memory_type: str,
        dimension: int = 384
    ) -> FaissIndex:
        """
        创建专家记忆索引

        Args:
            expert_name: 专家名称
            memory_type: 记忆类型
            dimension: 向量维度

        Returns:
            索引对象
        """
        index_name = f"expert_{expert_name}_{memory_type}"
        return self.get_index(index_name, dimension)

    def save_all(self):
        """保存所有索引"""
        for index in self._indexes.values():
            index.save()

    def close(self):
        """关闭并保存所有索引"""
        self.save_all()
        self._indexes.clear()


_index_manager: Optional[FaissIndexManager] = None
_index_lock = threading.Lock()


def get_faiss_index_manager() -> FaissIndexManager:
    """获取全局 FAISS 索引管理器（线程安全）"""
    global _index_manager

    if _index_manager is None:
        with _index_lock:
            if _index_manager is None:
                _index_manager = FaissIndexManager()

    return _index_manager