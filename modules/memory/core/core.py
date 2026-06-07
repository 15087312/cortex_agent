"""
记忆核心工具类

提供记忆过期清理、关键词检索、去重、格式化、快照保存/加载等通用功能。
"""
import os
import json
import time
import hashlib
import uuid
from typing import Dict, Any, List, Optional
from pathlib import Path
from utils.logger import setup_logger


class MemoryCore:
    """记忆核心工具类"""

    def __init__(self, data_dir: str = "data/memory"):
        """
        初始化记忆核心
        
        Args:
            data_dir: 记忆数据存储目录
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logger = setup_logger("memory_core")

    def generate_memory_id(self, content: str, timestamp: float = None) -> str:
        """
        生成记忆 ID

        Q-10: Use nanosecond precision timestamp + UUID to avoid collisions

        Args:
            content: 记忆内容
            timestamp: 时间戳

        Returns:
            记忆 ID
        """
        ts = timestamp or time.time()
        # Use nanosecond precision to reduce collision likelihood for same-second entries
        ts_nanos = int(ts * 1_000_000_000) % (2**31)  # Keep reasonable size
        # Add content hash for additional uniqueness
        content_hash = hashlib.md5(content.encode()).hexdigest()[:4]
        # Add UUID component for guaranteed uniqueness
        unique_id = str(uuid.uuid4())[:8]
        return f"mem_{ts_nanos}_{content_hash}_{unique_id}"

    def keyword_search(self, memories: List[Dict[str, Any]], keywords: List[str]) -> List[Dict[str, Any]]:
        """
        关键词检索记忆
        
        Args:
            memories: 记忆列表
            keywords: 关键词列表
            
        Returns:
            匹配的记忆列表
        """
        results = []
        for mem in memories:
            content = str(mem.get("content", "")) + " " + str(mem.get("text", ""))
            score = sum(1 for kw in keywords if kw.lower() in content.lower())
            if score > 0:
                results.append({**mem, "search_score": score})
        
        # 按相关度排序
        return sorted(results, key=lambda x: x.get("search_score", 0), reverse=True)

    def deduplicate_memories(self, memories: List[Dict[str, Any]], threshold: float = 0.9) -> List[Dict[str, Any]]:
        """
        记忆去重
        
        Args:
            memories: 记忆列表
            threshold: 相似度阈值
            
        Returns:
            去重后的记忆列表
        """
        seen = set()
        unique = []
        
        for mem in memories:
            content = str(mem.get("content", "")) + str(mem.get("text", ""))
            content_hash = hashlib.md5(content.encode()).hexdigest()
            
            if content_hash not in seen:
                seen.add(content_hash)
                unique.append(mem)
        
        return unique

    def format_memory(self, memory: Dict[str, Any]) -> Dict[str, Any]:
        """
        格式化记忆数据
        
        Args:
            memory: 原始记忆数据
            
        Returns:
            格式化后的记忆数据
        """
        return {
            "id": memory.get("id", self.generate_memory_id(str(memory))),
            "type": memory.get("type", "unknown"),
            "content": memory.get("content", ""),
            "text": memory.get("text", ""),
            "metadata": memory.get("metadata", {}),
            "timestamp": memory.get("timestamp", time.time()),
            "importance": memory.get("importance", 0.5),
            "tags": memory.get("tags", [])
        }

    def save_snapshot(self, snapshot_name: str, data: Dict[str, Any]) -> str:
        """
        快照保存

        Args:
            snapshot_name: 快照名称
            data: 快照数据

        Returns:
            快照文件路径
        """
        snapshot_file = self.data_dir / f"snapshot_{snapshot_name}.json"
        tmp_file = self.data_dir / f"snapshot_{snapshot_name}.json.tmp"

        try:
            # BUG-4: Write to temporary file first, then atomically replace
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # Atomic rename (POSIX atomic operation)
            import os
            os.replace(str(tmp_file), str(snapshot_file))

            self.logger.info("快照保存成功: %s", snapshot_file)
            return str(snapshot_file)
        except Exception as e:
            self.logger.error("快照保存失败: %s", e)
            # Clean up temp file if it exists
            try:
                tmp_file.unlink(missing_ok=True)
            except Exception as e:
                self.logger.debug("临时快照文件清理失败: %s", e)
            return ""

    def load_snapshot(self, snapshot_name: str) -> Optional[Dict[str, Any]]:
        """
        快照加载
        
        Args:
            snapshot_name: 快照名称
            
        Returns:
            快照数据
        """
        snapshot_file = self.data_dir / f"snapshot_{snapshot_name}.json"
        
        if not snapshot_file.exists():
            self.logger.warning("快照文件不存在: %s", snapshot_file)
            return None
        
        try:
            with open(snapshot_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self.logger.info("快照加载成功: %s", snapshot_file)
            return data
        except Exception as e:
            self.logger.error("快照加载失败: %s", e)
            return None

    def cleanup_expired(self, memories: List[Dict[str, Any]], max_age_seconds: float) -> List[Dict[str, Any]]:
        """
        清理过期记忆
        
        Args:
            memories: 记忆列表
            max_age_seconds: 最大存活时间（秒）
            
        Returns:
            清理后的记忆列表
        """
        current_time = time.time()
        return [
            mem for mem in memories
            if current_time - mem.get("timestamp", 0) < max_age_seconds
        ]

    def get_storage_stats(self) -> Dict[str, Any]:
        """
        获取存储统计
        
        Returns:
            存储统计信息
        """
        total_size = 0
        file_count = 0
        
        for file_path in self.data_dir.rglob("*"):
            if file_path.is_file():
                total_size += file_path.stat().st_size
                file_count += 1
        
        return {
            "total_size_mb": total_size / 1024 / 1024,
            "file_count": file_count,
            "data_dir": str(self.data_dir)
        }
