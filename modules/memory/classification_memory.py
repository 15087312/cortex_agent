"""
分类记忆系统 - 支持按类别组织的记忆管理
"""
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime
from utils.logger import setup_logger


class ClassificationMemory:
    """
    分类记忆系统，支持按类别组织记忆，并提供三种记忆层次：
    - 短期记忆：30分钟内，全部加载到内存
    - 中期记忆：30分钟到7天，通过RAG检索
    - 长期记忆：7天以上，通过RAG检索
    """

    def __init__(self, data_dir: str = "./data/classified_memories", enable_rag: bool = False):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.enable_rag = enable_rag
        self.rag_available = False
        self.rag_reason = "disabled_at_startup"

        # 按类别组织的记忆目录
        self.category_dirs = {
            "skills": self.data_dir / "skills",
            "communication": self.data_dir / "communication",
            "knowledge": self.data_dir / "knowledge",
            "experience": self.data_dir / "experience",
            "preferences": self.data_dir / "preferences",
            "general": self.data_dir / "general"
        }

        # 创建类别目录
        for dir_path in self.category_dirs.values():
            dir_path.mkdir(exist_ok=True)

        # 短期记忆（30分钟内）- 存储在内存中
        self.short_term_memories = {}

        # FAISS索引和映射
        self.faiss_indexes = {}
        self.memory_id_map = {}
        self.embedding_gen = None

        # 日志
        self.logger = setup_logger("classification_memory")

        # RAG 默认延迟初始化，避免服务启动阶段因 HuggingFace 网络探测阻塞。
        if self.enable_rag:
            self._init_rag()

        # 加载短期记忆
        self._load_short_term_memories()

        # 仅在 RAG 可用时冷启动重建索引
        if self.rag_available:
            self._rebuild_indexes_on_startup()

        self.logger.info(
            "分类记忆系统初始化完成 (RAG: %s)",
            "启用" if self.rag_available else f"禁用/{self.rag_reason}",
        )

    def _init_rag(self):
        """初始化RAG组件"""
        try:
            # 关键：先加载 EmbeddingGenerator（触发 numpy + SentenceTransformer），再 import faiss
            # 避免 numpy 2.x / faiss 1.x ABI 冲突导致 segfault
            from modules.memory.utils.embeddings import get_embedding_generator

            self.embedding_gen = get_embedding_generator()
            probe = self.embedding_gen.get_embedding("memory_probe")
            if probe is None:
                self.rag_available = False
                self.rag_reason = "embedding_unavailable"
                self.logger.warning("RAG组件初始化降级: embedding不可用")
                return

            import faiss  # noqa: F401

            self.rag_available = True
            self.rag_reason = ""
            self.logger.info("RAG组件初始化成功")
        except Exception as e:
            self.embedding_gen = None
            self.rag_available = False
            self.rag_reason = str(e)
            self.logger.warning(f"RAG组件初始化失败: {e}")

    def _ensure_rag_initialized(self) -> bool:
        """按需初始化 RAG，启动阶段不阻塞。"""
        if self.rag_available:
            return True
        if not self.enable_rag:
            return False
        self._init_rag()
        if self.rag_available and not self.faiss_indexes:
            self._rebuild_indexes_on_startup()
        return self.rag_available

    def _load_short_term_memories(self):
        """加载30分钟内的短期记忆到内存"""
        cutoff_time = time.time() - 30 * 60

        for category in self.category_dirs.keys():
            self.short_term_memories[category] = []

            dir_path = self.category_dirs[category]
            for file_path in dir_path.glob("*.jsonl"):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    memory = json.loads(line)
                                    if memory.get("timestamp", 0) > cutoff_time:
                                        self.short_term_memories[category].append(memory)
                                except json.JSONDecodeError:
                                    continue
                except Exception as e:
                    self.logger.warning(f"加载短期记忆失败 {file_path}: {e}")

    def _save_to_file(self, category: str, memory_data: Dict[str, Any]) -> str:
        """将记忆保存到对应的类别文件中"""
        if category not in self.category_dirs:
            category = "general"

        today = datetime.now().strftime("%Y-%m-%d")
        file_path = self.category_dirs[category] / f"{today}.jsonl"

        memory_data["timestamp"] = time.time()
        memory_data["created_at"] = datetime.now().isoformat()

        with open(file_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(memory_data, ensure_ascii=False) + '\n')

        if memory_data["timestamp"] > time.time() - 30 * 60:
            if category not in self.short_term_memories:
                self.short_term_memories[category] = []
            self.short_term_memories[category].append(memory_data)

        return memory_data.get("id", str(hash(str(memory_data))))

    def save_memory(self, category: str, content: str, metadata: Optional[Dict] = None) -> str:
        """
        保存记忆到指定类别

        Args:
            category: 记忆类别 (skills, communication, knowledge, experience, preferences, general)
            content: 记忆内容
            metadata: 元数据信息

        Returns:
            记忆ID
        """
        memory_id = f"mem_{int(time.time())}_{hash(content) % 10000}"

        memory_data = {
            "id": memory_id,
            "category": category,
            "content": content,
            "metadata": metadata or {},
            "importance": metadata.get("importance", 0.5) if metadata else 0.5
        }

        saved_id = self._save_to_file(category, memory_data)

        if self._ensure_rag_initialized() and self.embedding_gen:
            try:
                text = content
                vector = self.embedding_gen.get_embedding(text)

                if vector is not None:
                    if vector.ndim > 1:
                        vector = vector.reshape(-1, vector.shape[-1])
                    else:
                        vector = vector.reshape(1, -1)

                    import faiss
                    import numpy as np
                    if category not in self.faiss_indexes:
                        self.faiss_indexes[category] = faiss.IndexFlatIP(vector.shape[1])
                        self.memory_id_map[category] = {}

                    index = self.faiss_indexes[category]
                    current_idx = index.ntotal
                    index.add(vector.astype(np.float32))
                    self.memory_id_map[category][current_idx] = memory_id

            except Exception as e:
                self.logger.warning(f"保存记忆向量索引失败: {e}")

        self.logger.debug(f"保存记忆到类别 '{category}': {memory_id}")
        return saved_id

    def get_short_term_memories(self, category: str = None) -> List[Dict[str, Any]]:
        """获取短期记忆（30分钟内）"""
        if category:
            return self.short_term_memories.get(category, []).copy()
        else:
            all_memories = []
            for cat_memories in self.short_term_memories.values():
                all_memories.extend(cat_memories)
            all_memories.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
            return all_memories

    def search_mid_term_memories(self, query: str, category: str = None, limit: int = 10) -> List[Dict[str, Any]]:
        """搜索中期记忆（30分钟到7天）"""
        categories_to_search = [category] if category else list(self.category_dirs.keys())

        results = []
        cutoff_min = time.time() - 7 * 24 * 3600
        cutoff_max = time.time() - 30 * 60

        rag_ready = self._ensure_rag_initialized()
        for cat in categories_to_search:
            if rag_ready and cat in self.faiss_indexes and self.embedding_gen:
                try:
                    import numpy as np
                    query_vector = self.embedding_gen.get_embedding(query)
                    if query_vector is not None:
                        if query_vector.ndim > 1:
                            query_vector = query_vector.reshape(1, -1)

                        index = self.faiss_indexes[cat]
                        scores, indices = index.search(query_vector.astype(np.float32), limit * 2)

                        for i, idx in enumerate(indices[0]):
                            if idx != -1 and idx in self.memory_id_map[cat]:
                                memory_id = self.memory_id_map[cat][idx]
                                memory = self._load_memory_by_id(cat, memory_id)
                                if memory:
                                    timestamp = float(memory.get("timestamp", 0) or 0)
                                    if cutoff_min < timestamp <= cutoff_max:
                                        memory["similarity_score"] = float(scores[0][i])
                                        results.append(memory)
                except Exception as e:
                    self.logger.warning(f"搜索中期记忆失败 ({cat}): {e}")

        if not results:
            return self._keyword_search_with_window(query, categories_to_search, cutoff_min, cutoff_max, limit)

        results.sort(key=lambda x: x.get("similarity_score", 0), reverse=True)
        return results[:limit]

    def search_long_term_memories(self, query: str, category: str = None, limit: int = 10) -> List[Dict[str, Any]]:
        """搜索长期记忆（7天以上）"""
        categories_to_search = [category] if category else list(self.category_dirs.keys())

        results = []
        cutoff = time.time() - 7 * 24 * 3600

        rag_ready = self._ensure_rag_initialized()
        for cat in categories_to_search:
            if rag_ready and cat in self.faiss_indexes and self.embedding_gen:
                try:
                    import numpy as np
                    query_vector = self.embedding_gen.get_embedding(query)
                    if query_vector is not None:
                        if query_vector.ndim > 1:
                            query_vector = query_vector.reshape(1, -1)

                        index = self.faiss_indexes[cat]
                        scores, indices = index.search(query_vector.astype(np.float32), limit * 2)

                        for i, idx in enumerate(indices[0]):
                            if idx != -1 and idx in self.memory_id_map[cat]:
                                memory_id = self.memory_id_map[cat][idx]
                                memory = self._load_memory_by_id(cat, memory_id)
                                if memory:
                                    timestamp = float(memory.get("timestamp", 0) or 0)
                                    if timestamp <= cutoff:
                                        memory["similarity_score"] = float(scores[0][i])
                                        results.append(memory)
                except Exception as e:
                    self.logger.warning(f"搜索长期记忆失败 ({cat}): {e}")

        if not results:
            return self._keyword_search_with_window(query, categories_to_search, None, cutoff, limit)

        results.sort(key=lambda x: x.get("similarity_score", 0), reverse=True)
        return results[:limit]

    def _load_memory_by_id(self, category: str, memory_id: str) -> Optional[Dict[str, Any]]:
        """根据ID从文件中加载记忆"""
        if category not in self.category_dirs:
            return None

        dir_path = self.category_dirs[category]
        for file_path in dir_path.glob("*.jsonl"):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                memory = json.loads(line)
                                if memory.get("id") == memory_id:
                                    return memory
                            except json.JSONDecodeError:
                                continue
            except Exception as e:
                self.logger.debug("读取分类记忆文件失败，跳过: %s", e)
                continue

        return None

    def _iter_memories(self, category: str):
        """遍历某类别下的所有记忆"""
        if category not in self.category_dirs:
            return

        dir_path = self.category_dirs[category]
        for file_path in dir_path.glob("*.jsonl"):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                self.logger.debug("遍历分类记忆文件失败，跳过: %s", e)
                continue

    def _rebuild_indexes_on_startup(self):
        """冷启动重建内存索引"""
        if not self.embedding_gen:
            return

        try:
            import faiss
        except Exception as e:
            self.logger.warning(f"FAISS不可用，跳过索引重建: {e}")
            return

        for category in self.category_dirs.keys():
            vectors = []
            id_pairs = []
            for memory in self._iter_memories(category):
                content = memory.get("content", "")
                if not content:
                    continue
                try:
                    vector = self.embedding_gen.get_embedding(str(content))
                    if vector is None:
                        continue
                    if vector.ndim > 1:
                        vector = vector.reshape(-1, vector.shape[-1])
                    else:
                        vector = vector.reshape(1, -1)
                    vectors.append(vector[0])
                    id_pairs.append(memory.get("id"))
                except Exception as e:
                    self.logger.debug("生成记忆向量失败，跳过: %s", e)
                    continue

            if not vectors:
                continue

            import numpy as np
            mat = np.asarray(vectors, dtype=np.float32)
            index = faiss.IndexFlatIP(mat.shape[1])
            index.add(mat)
            self.faiss_indexes[category] = index
            self.memory_id_map[category] = {i: mem_id for i, mem_id in enumerate(id_pairs)}

    def _keyword_search_with_window(
            self,
            query: str,
            categories_to_search: List[str],
            min_ts: Optional[float],
            max_ts: Optional[float],
            limit: int,
    ) -> List[Dict[str, Any]]:
        """关键词+时间窗回退检索"""
        query_l = (query or "").lower()
        results = []

        for cat in categories_to_search:
            for memory in self._iter_memories(cat):
                ts = float(memory.get("timestamp", 0) or 0)
                if min_ts is not None and ts < min_ts:
                    continue
                if max_ts is not None and ts > max_ts:
                    continue

                content = str(memory.get("content", ""))
                if not content:
                    continue

                if query_l and query_l not in content.lower():
                    continue

                memory_copy = dict(memory)
                memory_copy["keyword_score"] = 1.0
                results.append(memory_copy)

        results.sort(
            key=lambda x: (float(x.get("importance", 0.5) or 0.5), float(x.get("timestamp", 0) or 0)),
            reverse=True,
        )
        return results[:limit]

    def search_memories_by_category(self, query: str, category: str, memory_age: str = "all", limit: int = 10) -> List[
        Dict[str, Any]]:
        """
        按类别和记忆年龄搜索记忆

        Args:
            query: 查询内容
            category: 记忆类别
            memory_age: short, mid, long, all
            limit: 返回数量限制
        """
        if memory_age == "short":
            short_memories = self.get_short_term_memories(category)
            results = []
            for mem in short_memories:
                if query.lower() in mem.get("content", "").lower():
                    results.append(mem)
            return results[:limit]
        elif memory_age == "mid":
            return self.search_mid_term_memories(query, category, limit)
        elif memory_age == "long":
            return self.search_long_term_memories(query, category, limit)
        else:
            results = []

            short_results = self.search_memories_by_category(query, category, "short", limit)
            results.extend([{"age": "short", **mem} for mem in short_results])

            mid_results = self.search_mid_term_memories(query, category, limit)
            results.extend([{"age": "mid", **mem} for mem in mid_results])

            long_results = self.search_long_term_memories(query, category, limit)
            results.extend([{"age": "long", **mem} for mem in long_results])

            results.sort(key=lambda x: x.get("similarity_score", x.get("timestamp", 0)), reverse=True)
            return results[:limit]

    def get_all_categories(self) -> List[str]:
        """获取所有记忆类别"""
        return list(self.category_dirs.keys())

    def get_category_stats(self) -> Dict[str, int]:
        """获取各类别记忆数量统计"""
        stats = {}
        for category, dir_path in self.category_dirs.items():
            count = 0
            for file_path in dir_path.glob("*.jsonl"):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        count += sum(1 for line in f if line.strip())
                except Exception as e:
                    self.logger.debug("读取分类记忆统计文件失败，跳过: %s", e)
                    continue
            stats[category] = count
        return stats
