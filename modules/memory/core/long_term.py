"""
长期记忆

永久保存，重启不丢。
存储用户偏好、习惯、思考总结、代码修改历史等。
使用 JSONL 格式，支持增量写入。
"""
import os
import json
import time
import threading
import numpy as np
from typing import Dict, Any, List, Optional
from pathlib import Path
from utils.logger import setup_logger


def _safe_ts(memory: Dict[str, Any]) -> float:
    """
    安全提取时间戳用于排序，兼容 timestamp 为 dict/int/float/str 的异常数据。

    部分旧数据 timestamp 可能是 dict（如 {"start": 123, "end": 456}），
    直接用 > 比较会抛 TypeError。
    """
    ts = memory.get("timestamp", 0)
    if isinstance(ts, dict):
        return float(ts.get("start", ts.get("created", 0)))
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return float(ts)
    except (TypeError, ValueError):
        return 0.0


class LongTermMemory:
    """
    长期记忆管理器
    
    负责：
    - 本地 JSONL 存储
    - 按时间、主题、重要度分类
    - 支持增量写入
    - 关键词检索
    """

    def __init__(self, data_dir: str = "data/memory/long_term", enable_rag: bool = True):
        """
        初始化长期记忆

        Args:
            data_dir: 存储目录
            enable_rag: 是否启用 RAG（语义搜索）
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logger = setup_logger("long_term_memory")

        self.enable_rag = enable_rag
        self._rag_initialized = False
        self._embedding_gen = None
        self._faiss_manager = None

        # 不同类型的记忆文件
        self.files = {
            "dialog": self.data_dir / "dialogs.jsonl",
            "thought": self.data_dir / "thoughts.jsonl",
            "preference": self.data_dir / "preferences.jsonl",
            "summary": self.data_dir / "summaries.jsonl",
            "evolution": self.data_dir / "evolution.jsonl",
            "event": self.data_dir / "events.jsonl"
        }

        # CONC-1: Per-file locks for concurrent write safety
        self._file_locks = {mem_type: threading.Lock() for mem_type in self.files.keys()}

        # 确保文件存在
        for file_path in self.files.values():
            if not file_path.exists():
                file_path.touch()

        if self.enable_rag:
            self._init_rag()

        self.logger.info("长期记忆初始化完成 (目录: %s, RAG: %s)", self.data_dir, "启用" if enable_rag else "禁用")

    def _init_rag(self):
        """初始化 RAG 组件（FAISS + embedding）"""
        if self._rag_initialized:
            return

        try:
            from modules.memory.utils.embeddings import get_embedding_generator
            from modules.memory.utils.faiss_index import get_faiss_index_manager

            self._embedding_gen = get_embedding_generator()
            self._faiss_manager = get_faiss_index_manager()
            self._rag_initialized = True
            self.logger.info("RAG 组件已就绪")

        except Exception as e:
            self.logger.warning(f"RAG 组件初始化失败: {e}")
            self.enable_rag = False

    def save(self, memory_type: str, content: Dict[str, Any]) -> Dict[str, Any]:
        """
        保存长期记忆

        Args:
            memory_type: 记忆类型 (dialog, thought, preference, summary, evolution, event)
            content: 记忆内容

        Returns:
            保存的记忆记录
        """
        if memory_type not in self.files:
            raise ValueError(f"不支持的记忆类型: {memory_type}")

        # 格式化记忆
        memory = {
            "id": f"ltm_{memory_type}_{int(time.time())}_{hash(str(content)) % 10000}",
            "type": memory_type,
            "content": content,
            "timestamp": time.time(),
            "importance": content.get("importance", 0.5),
            "tags": content.get("tags", [])
        }

        # CONC-1: Use per-file lock to prevent concurrent write corruption
        file_path = self.files[memory_type]
        try:
            with self._file_locks[memory_type]:
                with open(file_path, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(memory, ensure_ascii=False) + '\n')

            self.logger.debug(
                "保存长期记忆 [%s]: %s...",
                memory_type,
                str(content)[:100]
            )

            return memory
        except Exception as e:
            self.logger.error("保存长期记忆失败: %s", e)
            raise

    def load(self, memory_type: str, limit: int = 50, reverse: bool = True) -> List[Dict[str, Any]]:
        """
        加载长期记忆

        Args:
            memory_type: 记忆类型
            limit: 返回数量限制
            reverse: 是否倒序（最新的在前）

        Returns:
            记忆列表
        """
        if memory_type not in self.files:
            raise ValueError(f"不支持的记忆类型: {memory_type}")

        file_path = self.files[memory_type]

        if not file_path.exists():
            return []

        memories = []
        try:
            # CONC-1: Hold lock during read to prevent inconsistent reads
            # while another thread is writing
            with self._file_locks[memory_type]:
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                memory = json.loads(line)
                                memories.append(memory)
                            except json.JSONDecodeError:
                                continue

            # 按时间排序（兼容 timestamp 为 dict/int/str 的异常数据）
            memories.sort(key=_safe_ts, reverse=reverse)

            return memories[:limit]
        except Exception as e:
            self.logger.error("加载长期记忆失败: %s", e)
            return []

    def search(self, memory_type: str, keywords: List[str], limit: int = 20) -> List[Dict[str, Any]]:
        """
        搜索长期记忆
        
        Args:
            memory_type: 记忆类型
            keywords: 关键词列表
            limit: 返回数量限制
            
        Returns:
            匹配的记忆列表
        """
        memories = self.load(memory_type, limit=1000)
        
        results = []
        for mem in memories:
            content_str = json.dumps(mem.get("content", {}), ensure_ascii=False).lower()
            score = sum(1 for kw in keywords if kw.lower() in content_str)
            
            if score > 0:
                results.append({**mem, "search_score": score})
        
        # 按相关度排序
        results.sort(key=lambda x: x.get("search_score", 0), reverse=True)
        
        return results[:limit]

    def delete(self, memory_id: str, memory_type: str = None) -> int:
        """
        删除指定记忆

        Args:
            memory_id: 记忆 ID
            memory_type: 记忆类型（可选，不指定则搜索所有类型）

        Returns:
            删除的记录数
        """
        deleted_count = 0

        types_to_search = [memory_type] if memory_type else list(self.files.keys())

        for mem_type in types_to_search:
            file_path = self.files[mem_type]

            if not file_path.exists():
                continue

            # CONC-1: Hold lock during entire read-filter-write operation
            # Prevents TOCTOU race condition where data written between
            # read and write would be lost
            with self._file_locks[mem_type]:
                # 读取所有记忆
                memories = []
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                memories.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue

                # 过滤掉要删除的记忆
                original_count = len(memories)
                memories = [m for m in memories if m.get("id") != memory_id]
                deleted = original_count - len(memories)

                if deleted > 0:
                    # 写回文件
                    with open(file_path, 'w', encoding='utf-8') as f:
                        for memory in memories:
                            f.write(json.dumps(memory, ensure_ascii=False) + '\n')

                    deleted_count += deleted
                    self.logger.info("删除长期记忆 [%s]: %s", mem_type, memory_id)

        return deleted_count

    def get_statistics(self) -> Dict[str, Any]:
        """获取长期记忆统计"""
        stats = {}
        total_size = 0
        
        for mem_type, file_path in self.files.items():
            if file_path.exists():
                file_size = file_path.stat().st_size
                total_size += file_size
                
                # 计算记录数
                count = 0
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            count += 1
                
                stats[mem_type] = {
                    "count": count,
                    "size_kb": file_size / 1024
                }
        
        stats["total_size_kb"] = total_size / 1024
        
        return stats

    def compact(self, memory_type: str) -> int:
        """
        压缩记忆文件（去重、清理无效数据）

        Args:
            memory_type: 记忆类型

        Returns:
            清理的记录数
        """
        file_path = self.files[memory_type]

        if not file_path.exists():
            return 0

        # CONC-1: Hold lock during entire read-deduplicate-write operation
        with self._file_locks[memory_type]:
            # 读取所有记忆
            memories = []
            seen_ids = set()
            removed_count = 0

            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            memory = json.loads(line)
                            mem_id = memory.get("id")

                            if mem_id and mem_id not in seen_ids:
                                seen_ids.add(mem_id)
                                memories.append(memory)
                            else:
                                removed_count += 1
                        except json.JSONDecodeError:
                            removed_count += 1

            # 写回
            with open(file_path, 'w', encoding='utf-8') as f:
                for memory in memories:
                    f.write(json.dumps(memory, ensure_ascii=False) + '\n')

            self.logger.info("压缩长期记忆 [%s]: 删除 %d 条重复/无效记录", memory_type, removed_count)

        return removed_count

    # ========== 专家模型专有记忆 ==========

    @property
    def expert_memories_dir(self) -> Path:
        """专家记忆目录"""
        expert_dir = self.data_dir / "expert_memories"
        expert_dir.mkdir(parents=True, exist_ok=True)
        return expert_dir

    def get_expert_dir(self, expert_name: str) -> Path:
        """获取指定专家的记忆目录"""
        safe_name = expert_name.replace("/", "_").replace("\\", "_")
        expert_dir = self.expert_memories_dir / safe_name
        expert_dir.mkdir(parents=True, exist_ok=True)
        return expert_dir

    def save_expert_memory(
        self,
        expert_name: str,
        memory_type: str,
        content: Dict[str, Any],
        importance: float = 0.5
    ) -> Dict[str, Any]:
        """
        保存专家专有记忆
        
        Args:
            expert_name: 专家名称 (如 "Perception", "Memory", "Decision")
            memory_type: 记忆类型 (如 "analysis", "insight", "pattern", "preference")
            content: 记忆内容
            importance: 重要度 0-1
            
        Returns:
            保存的记忆记录
        """
        expert_dir = self.get_expert_dir(expert_name)
        file_path = expert_dir / f"{memory_type}.jsonl"
        
        memory = {
            "id": f"exp_{expert_name}_{memory_type}_{int(time.time())}_{hash(str(content)) % 10000}",
            "expert": expert_name,
            "type": memory_type,
            "content": content,
            "timestamp": time.time(),
            "importance": importance
        }
        
        try:
            with open(file_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(memory, ensure_ascii=False) + '\n')
            
            self.logger.debug(
                "保存专家记忆 [%s/%s]: %s...",
                expert_name, memory_type,
                str(content)[:50]
            )
            
            return memory
        except Exception as e:
            self.logger.error("保存专家记忆失败: %s", e)
            raise

    def load_expert_memory(
        self,
        expert_name: str,
        memory_type: str = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        加载专家专有记忆
        
        Args:
            expert_name: 专家名称
            memory_type: 记忆类型 (None 则加载所有类型)
            limit: 返回数量限制
            
        Returns:
            记忆列表
        """
        expert_dir = self.get_expert_dir(expert_name)
        
        if not expert_dir.exists():
            return []
        
        memories = []
        
        if memory_type:
            file_path = expert_dir / f"{memory_type}.jsonl"
            if file_path.exists():
                memories.extend(self._read_jsonl(file_path, limit))
        else:
            for jsonl_file in expert_dir.glob("*.jsonl"):
                memories.extend(self._read_jsonl(jsonl_file, limit))
        
        memories.sort(key=_safe_ts, reverse=True)
        return memories[:limit]

    def _read_jsonl(self, file_path: Path, limit: int = None) -> List[Dict[str, Any]]:
        """读取 JSONL 文件"""
        memories = []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            memories.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            self.logger.debug("加载专家记忆文件失败: %s", e)
        return memories[:limit] if limit else memories

    def search_expert_memory(
        self,
        expert_name: str,
        keywords: List[str],
        memory_type: str = None,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        搜索专家专有记忆
        
        Args:
            expert_name: 专家名称
            keywords: 关键词列表
            memory_type: 记忆类型 (可选)
            limit: 返回数量限制
            
        Returns:
            匹配的记忆列表
        """
        memories = self.load_expert_memory(expert_name, memory_type, limit=100)
        
        results = []
        for mem in memories:
            content_str = json.dumps(mem.get("content", {}), ensure_ascii=False).lower()
            score = sum(1 for kw in keywords if kw.lower() in content_str)
            
            if score > 0:
                results.append({**mem, "search_score": score})
        
        results.sort(key=lambda x: x.get("search_score", 0), reverse=True)
        return results[:limit]

    def get_expert_memory_summary(self, expert_name: str) -> Dict[str, Any]:
        """获取专家记忆摘要"""
        expert_dir = self.get_expert_dir(expert_name)
        
        if not expert_dir.exists():
            return {"expert": expert_name, "total_memories": 0, "types": {}}
        
        summary = {
            "expert": expert_name,
            "total_memories": 0,
            "types": {},
            "total_size_kb": 0
        }
        
        for jsonl_file in expert_dir.glob("*.jsonl"):
            mem_type = jsonl_file.stem
            count = 0
            size_kb = jsonl_file.stat().st_size / 1024
            
            with open(jsonl_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        count += 1
            
            summary["types"][mem_type] = {
                "count": count,
                "size_kb": size_kb
            }
            summary["total_memories"] += count
            summary["total_size_kb"] += size_kb
        
        return summary

    def get_all_experts_summary(self) -> Dict[str, Dict[str, Any]]:
        """获取所有专家记忆摘要"""
        summary = {}
        expert_dir = self.expert_memories_dir
        
        if not expert_dir.exists():
            return summary
        
        for expert_subdir in expert_dir.iterdir():
            if expert_subdir.is_dir():
                summary[expert_subdir.name] = self.get_expert_memory_summary(expert_subdir.name)
        
        return summary

    def delete_expert_memory(
        self,
        expert_name: str,
        memory_id: str = None,
        memory_type: str = None,
        before_timestamp: float = None
    ) -> int:
        """
        删除专家记忆
        
        Args:
            expert_name: 专家名称
            memory_id: 记忆 ID (删除指定记忆)
            memory_type: 记忆类型 (删除指定类型)
            before_timestamp: 删除此时间之前的记忆
            
        Returns:
            删除的记录数
        """
        expert_dir = self.get_expert_dir(expert_name)
        
        if not expert_dir.exists():
            return 0
        
        deleted_count = 0
        
        if memory_type:
            file_path = expert_dir / f"{memory_type}.jsonl"
            if file_path.exists():
                deleted_count += self._delete_from_jsonl(
                    file_path, memory_id, before_timestamp
                )
        else:
            for jsonl_file in expert_dir.glob("*.jsonl"):
                deleted_count += self._delete_from_jsonl(
                    jsonl_file, memory_id, before_timestamp
                )
        
        return deleted_count

    def _delete_from_jsonl(
        self,
        file_path: Path,
        memory_id: str = None,
        before_timestamp: float = None
    ) -> int:
        """从 JSONL 文件中删除记录"""
        memories = self._read_jsonl(file_path)
        original_count = len(memories)
        
        if memory_id:
            memories = [m for m in memories if m.get("id") != memory_id]
        elif before_timestamp:
            memories = [m for m in memories if m.get("timestamp", 0) >= before_timestamp]
        
        deleted = original_count - len(memories)
        
        if deleted > 0:
            with open(file_path, 'w', encoding='utf-8') as f:
                for memory in memories:
                    f.write(json.dumps(memory, ensure_ascii=False) + '\n')
        
        return deleted

    # ==================== RAG 增强功能 ====================

    def _get_index_name(self, memory_type: str, expert_name: str = None) -> str:
        """获取索引名称"""
        if expert_name:
            return f"expert_{expert_name}_{memory_type}"
        return f"memory_{memory_type}"

    def _extract_text_content(self, content: Dict[str, Any]) -> str:
        """从内容中提取文本用于 embedding"""
        if isinstance(content, str):
            return content

        if isinstance(content, dict):
            text_fields = ['text', 'content', 'message', 'thought', 'summary', 'description']
            for field in text_fields:
                if field in content and content[field]:
                    return str(content[field])

            return json.dumps(content, ensure_ascii=False)

        return str(content)

    def _get_or_create_index(self, memory_type: str, expert_name: str = None):
        """获取或创建 FAISS 索引"""
        self._init_rag()

        if not hasattr(self, '_rag_initialized') or not self._rag_initialized:
            return None

        index_name = self._get_index_name(memory_type, expert_name)
        dimension = self._embedding_gen.embedding_dim

        if expert_name:
            return self._faiss_manager.create_expert_index(expert_name, memory_type, dimension)
        else:
            return self._faiss_manager.create_memory_index(memory_type, dimension)

    def save_with_embedding(
        self,
        memory_type: str,
        content: Dict[str, Any],
        expert_name: str = None,
        save_to_index: bool = True
    ) -> Dict[str, Any]:
        """
        保存记忆（带 embedding）

        Args:
            memory_type: 记忆类型
            content: 记忆内容
            expert_name: 专家名称（可选）
            save_to_index: 是否保存到向量索引

        Returns:
            保存的记忆记录
        """
        memory = self.save(memory_type, content)

        if not save_to_index:
            return memory

        self._init_rag()

        if not self._rag_initialized:
            return memory

        try:
            text = self._extract_text_content(content)
            vector = self._embedding_gen.get_embedding(text)

            if vector is not None:
                if vector.ndim > 1:
                    vector = vector.reshape(-1, vector.shape[-1])
                else:
                    vector = vector.reshape(1, -1)
                index = self._get_or_create_index(memory_type, expert_name)
                if index:
                    index.add(vector, [memory["id"]])
                    index.save()
                    self.logger.debug(f"保存向量索引: {memory['id']}")

        except Exception as e:
            self.logger.warning(f"保存向量索引失败: {e}")

        return memory

    def search_hybrid(
        self,
        query: str,
        memory_types: List[str] = None,
        expert_name: str = None,
        limit: int = 10,
        use_semantic: bool = True,
        min_score: float = -2.0
    ) -> List[Dict[str, Any]]:
        """
        混合检索 - 语义搜索 + 分类过滤

        Args:
            query: 查询文本
            memory_types: 记忆类型列表（None 则搜索所有）
            expert_name: 专家名称（可选）
            limit: 返回结果数量
            use_semantic: 是否使用语义搜索
            min_score: 最小分数阈值（负数，因为 L2 距离为负）

        Returns:
            检索结果列表
        """
        self._init_rag()

        results = []

        if use_semantic and self._rag_initialized:
            results = self._search_semantic(
                query, memory_types, expert_name, limit * 2
            )
            # 语义搜索回退：FAISS 索引为空或返回不足时，退到关键词搜索
            if not results:
                self.logger.debug("语义搜索无结果，回退到关键词搜索")
                keywords = query.split()
                results = self._search_keyword_fallback(keywords, memory_types, expert_name, limit)
        else:
            keywords = query.split()
            results = self._search_keyword_fallback(keywords, memory_types, expert_name, limit)

        filtered_results = [r for r in results if r.get("score", 0) >= min_score]

        final_results = []
        for r in filtered_results[:limit]:
            full_content = self._load_by_id(r.get("id"), expert_name)
            if full_content:
                final_results.append({
                    "id": r.get("id"),
                    "content": full_content,
                    "score": r.get("score", 0),
                    "type": full_content.get("type") if isinstance(full_content, dict) else memory_types[0] if memory_types else "unknown"
                })

        return final_results

    def _search_semantic(
        self,
        query: str,
        memory_types: Optional[List[str]],
        expert_name: Optional[str],
        limit: int
    ) -> List[Dict[str, Any]]:
        """语义搜索"""
        results = []

        try:
            query_vector = self._embedding_gen.get_embedding(query)
            if query_vector is None:
                return []

            if query_vector.ndim > 1:
                query_vector = query_vector.reshape(-1, query_vector.shape[-1])

            types_to_search = memory_types if memory_types else list(self.files.keys())

            for mem_type in types_to_search:
                index = self._get_or_create_index(mem_type, expert_name)
                if index and index.count() > 0:
                    search_results = index.search(query_vector, k=limit)
                    for r in search_results:
                        results.append({
                            "id": r["id"],
                            "score": r["score"],
                            "type": mem_type,
                            "expert": expert_name
                        })

            results.sort(key=lambda x: x.get("score", 0), reverse=True)

        except Exception as e:
            self.logger.warning(f"语义搜索失败: {e}")

        return results

    def _search_keyword_fallback(
        self,
        keywords: List[str],
        memory_types: Optional[List[str]],
        expert_name: Optional[str],
        limit: int
    ) -> List[Dict[str, Any]]:
        """关键词搜索降级方案"""
        results = []

        if expert_name:
            memories = self.load_expert_memory(expert_name, memory_type=None, limit=1000)
        else:
            types_to_search = memory_types if memory_types else list(self.files.keys())
            memories = []
            for mem_type in types_to_search:
                try:
                    type_memories = self.load(mem_type, limit=500)
                    memories.extend(type_memories)
                except Exception as e:
                    self.logger.debug("加载长期记忆类型 %s 失败: %s", mem_type, e)

        for mem in memories:
            content_str = json.dumps(mem.get("content", {}), ensure_ascii=False).lower()
            score = sum(1 for kw in keywords if kw.lower() in content_str)

            if score > 0:
                results.append({
                    "id": mem.get("id"),
                    "score": float(score),
                    "type": mem.get("type", "unknown"),
                    "expert": expert_name
                })

        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return results[:limit]

    def _load_by_id(self, memory_id: str, expert_name: Optional[str] = None) -> Optional[Dict]:
        """根据 ID 加载记忆"""
        if expert_name:
            memories = self.load_expert_memory(expert_name, limit=1000)
            for mem in memories:
                if mem.get("id") == memory_id:
                    return mem
        else:
            for mem_type in self.files.keys():
                memories = self.load(mem_type, limit=1000)
                for mem in memories:
                    if mem.get("id") == memory_id:
                        return mem

        return None

    def build_index(
        self,
        memory_type: str = None,
        expert_name: str = None,
        batch_size: int = 100
    ) -> Dict[str, int]:
        """
        批量构建索引

        Args:
            memory_type: 记忆类型（None 则构建所有）
            expert_name: 专家名称
            batch_size: 批处理大小

        Returns:
            统计信息
        """
        self._init_rag()

        stats = {"total": 0, "success": 0, "failed": 0}

        try:
            if expert_name:
                memories = self.load_expert_memory(expert_name, memory_type)
            else:
                if memory_type:
                    memories = self.load(memory_type, limit=10000)
                else:
                    memories = []
                    for mem_type in self.files.keys():
                        memories.extend(self.load(mem_type, limit=5000))

            stats["total"] = len(memories)

            index = self._get_or_create_index(
                memory_type or "general",
                expert_name
            )

            if not index:
                return stats

            vectors = []
            mem_ids = []

            for mem in memories:
                try:
                    text = self._extract_text_content(mem.get("content", {}))
                    vector = self._embedding_gen.get_embedding(text)

                    if vector is not None:
                        if vector.ndim > 1:
                            vector = vector.reshape(-1, vector.shape[-1])
                        else:
                            vector = vector.reshape(1, -1)
                        vectors.append(vector)
                        mem_ids.append(mem["id"])

                    if len(vectors) >= batch_size:
                        index.add(np.vstack(vectors), mem_ids)
                        stats["success"] += len(vectors)
                        vectors = []
                        mem_ids = []

                except Exception as e:
                    stats["failed"] += 1
                    self.logger.warning(f"处理记忆失败: {e}")

            if vectors:
                index.add(np.vstack(vectors), mem_ids)
                stats["success"] += len(vectors)

            index.save()
            self.logger.info(f"索引构建完成: {stats}")

        except Exception as e:
            self.logger.error(f"批量构建索引失败: {e}")

        return stats

    def get_index_stats(self, memory_type: str = None, expert_name: str = None) -> Dict[str, Any]:
        """获取索引统计信息"""
        self._init_rag()

        if not self._rag_initialized:
            return {"status": "not_initialized"}

        try:
            index_name = self._get_index_name(memory_type or "general", expert_name)
            index = self._faiss_manager.get_index(index_name, auto_create=False)

            if index:
                return {
                    "index_name": index_name,
                    "vector_count": index.count(),
                    "dimension": index.dimension
                }

            return {"index_name": index_name, "vector_count": 0}

        except Exception as e:
            return {"error": str(e)}

    def save_expert_memory_with_embedding(
        self,
        expert_name: str,
        memory_type: str,
        content: Dict[str, Any],
        importance: float = 0.5
    ) -> Dict[str, Any]:
        """保存专家记忆（带 embedding）"""
        memory = self.save_expert_memory(expert_name, memory_type, content, importance)

        self._init_rag()

        if not self._rag_initialized:
            return memory

        try:
            text = self._extract_text_content(content)
            vector = self._embedding_gen.get_embedding(text)

            if vector is not None:
                index = self._get_or_create_index(memory_type, expert_name)
                if index:
                    index.add(np.array([vector]), [memory["id"]])
                    index.save()

        except Exception as e:
            self.logger.warning(f"保存专家向量索引失败: {e}")

        return memory

    def search_expert_memory_hybrid(
        self,
        expert_name: str,
        query: str,
        memory_type: str = None,
        limit: int = 10,
        use_semantic: bool = True
    ) -> List[Dict[str, Any]]:
        """混合检索专家记忆"""
        return self.search_hybrid(
            query=query,
            memory_types=[memory_type] if memory_type else None,
            expert_name=expert_name,
            limit=limit,
            use_semantic=use_semantic
        )

    def save_index(self):
        """保存所有索引"""
        if hasattr(self, '_faiss_manager'):
            self._faiss_manager.save_all()
            self.logger.info("所有索引已保存")
