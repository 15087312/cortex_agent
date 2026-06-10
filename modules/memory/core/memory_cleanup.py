"""
记忆清理核心逻辑
"""
from typing import List, Dict, Any
from datetime import datetime, timedelta
from utils.logger import setup_logger

logger = setup_logger("memory_cleanup")


class MemoryCleanup:
    """记忆清理器"""
    
    def __init__(self):
        self._initialized = False
    
    async def initialize(self):
        """初始化连接"""
        self._initialized = True
        logger.info("[记忆清理] 初始化完成")
    
    async def cleanup_expired(self, memory_type: str = "all") -> int:
        """
        清理过期记忆
        
        Args:
            memory_type: 记忆类型 (short_term/long_term/all)
            
        Returns:
            清理的记忆数量
        """
        if not self._initialized:
            await self.initialize()
        
        total_cleaned = 0
        
        if memory_type in ["short_term", "all"]:
            from modules.database.repository import short_term_repo
            cleaned = short_term_repo.cleanup_expired()
            total_cleaned += cleaned
            logger.info(f"[记忆清理] 短期记忆清理 {cleaned} 条过期记录")
        
        if memory_type in ["long_term", "all"]:
            from modules.memory.core.long_term import LongTermMemory
            # 创建临时实例进行清理
            temp_ltm = LongTermMemory(data_dir="data/memory/long_term")
            cleaned = self._cleanup_long_term_expired(temp_ltm)
            total_cleaned += cleaned
            logger.info(f"[记忆清理] 长期记忆清理 {cleaned} 条过期记录")
        
        return total_cleaned
    
    def _cleanup_long_term_expired(self, ltm_instance) -> int:
        """清理长期记忆中的过期文件"""
        import os

        cleaned = 0
        now = datetime.now()
        max_age_days = 90  # 默认保留90天

        for mem_type, file_path in ltm_instance.files.items():
            if not file_path.exists():
                continue

            memories = []
            lock = ltm_instance._file_locks.get(mem_type)
            if lock:
                with lock:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    import json
                                    memory = json.loads(line)
                                    created_at = memory.get("timestamp", 0)

                                    # timestamp是Unix时间戳
                                    if isinstance(created_at, (int, float)):
                                        from datetime import datetime as dt
                                        created_time = dt.fromtimestamp(created_at)
                                        age_days = (now - created_time).days

                                        if age_days <= max_age_days:
                                            memories.append(memory)
                                        else:
                                            cleaned += 1
                                    else:
                                        memories.append(memory)
                                except Exception as e:
                                    logger.debug("解析长期记忆条目失败，已跳过: %s", e)
                                    cleaned += 1

                    # 写回未过期的记忆
                    if memories:
                        with open(file_path, 'w', encoding='utf-8') as f:
                            for memory in memories:
                                import json
                                f.write(json.dumps(memory, ensure_ascii=False) + '\n')
            else:
                # No lock available, read without lock (fallback)
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                import json
                                memory = json.loads(line)
                                created_at = memory.get("timestamp", 0)

                                if isinstance(created_at, (int, float)):
                                    from datetime import datetime as dt
                                    created_time = dt.fromtimestamp(created_at)
                                    age_days = (now - created_time).days

                                    if age_days <= max_age_days:
                                        memories.append(memory)
                                    else:
                                        cleaned += 1
                                else:
                                    memories.append(memory)
                            except Exception as e:
                                logger.debug("解析长期记忆条目失败，已跳过: %s", e)
                                cleaned += 1

                if memories:
                    with open(file_path, 'w', encoding='utf-8') as f:
                        for memory in memories:
                            import json
                            f.write(json.dumps(memory, ensure_ascii=False) + '\n')

        return cleaned
    
    async def cleanup_by_importance(
        self,
        threshold: float = 0.3,
        memory_type: str = "all",
        min_count: int = 100
    ) -> int:
        """
        清理低重要性记忆
        
        Args:
            threshold: 重要性阈值（低于此值被清理）
            memory_type: 记忆类型 (short_term/long_term/all)
            min_count: 最少保留数量（即使重要性低也保留最近min_count条）
            
        Returns:
            清理的记忆数量
        """
        if not self._initialized:
            await self.initialize()
        
        total_cleaned = 0
        
        if memory_type in ["long_term", "all"]:
            cleaned = self._cleanup_low_importance_long_term(threshold, min_count)
            total_cleaned += cleaned
            logger.info(f"[记忆清理] 长期记忆清理 {cleaned} 条低重要性记录 (阈值={threshold})")
        
        return total_cleaned
    
    def _cleanup_low_importance_long_term(
        self,
        threshold: float,
        min_count: int
    ) -> int:
        """清理长期记忆中的低重要性记录"""
        from modules.memory.core.long_term import LongTermMemory
        
        cleaned = 0
        ltm_instance = LongTermMemory(data_dir="data/memory/long_term")

        for mem_type, file_path in ltm_instance.files.items():
            if not file_path.exists():
                continue

            memories = []
            lock = ltm_instance._file_locks.get(mem_type)
            if lock:
                with lock:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    import json
                                    memory = json.loads(line)
                                    memories.append(memory)
                                except Exception as e:
                                    logger.debug(f"清理时解析记忆条目失败: {e}")

                    if len(memories) <= min_count:
                        continue

                    # 按重要性和时间排序
                    memories.sort(
                        key=lambda m: (
                            m.get("importance", 0.5),
                            m.get("timestamp", 0)
                        ),
                        reverse=True
                    )

                    # 保留前min_count条或重要性>=threshold的
                    kept_memories = []
                    for i, memory in enumerate(memories):
                        importance = memory.get("importance", 0.5)
                        if i < min_count or importance >= threshold:
                            kept_memories.append(memory)
                        else:
                            cleaned += 1

                    # 写回
                    if kept_memories:
                        with open(file_path, 'w', encoding='utf-8') as f:
                            for memory in kept_memories:
                                import json
                                f.write(json.dumps(memory, ensure_ascii=False) + '\n')
            else:
                # No lock available (fallback)
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                import json
                                memory = json.loads(line)
                                memories.append(memory)
                            except Exception as e:
                                logger.debug(f"清理时解析记忆条目失败: {e}")

                if len(memories) <= min_count:
                    continue

                memories.sort(
                    key=lambda m: (
                        m.get("importance", 0.5),
                        m.get("timestamp", 0)
                    ),
                    reverse=True
                )

                kept_memories = []
                for i, memory in enumerate(memories):
                    importance = memory.get("importance", 0.5)
                    if i < min_count or importance >= threshold:
                        kept_memories.append(memory)
                    else:
                        cleaned += 1

                if kept_memories:
                    with open(file_path, 'w', encoding='utf-8') as f:
                        for memory in kept_memories:
                            import json
                            f.write(json.dumps(memory, ensure_ascii=False) + '\n')

        return cleaned
    
    async def compact_all(self) -> Dict[str, int]:
        """
        压缩所有记忆文件（去重、清理无效数据）
        
        Returns:
            各类型记忆的清理数量
        """
        if not self._initialized:
            await self.initialize()
        
        from modules.memory.core.long_term import LongTermMemory
        
        results = {}
        ltm_instance = LongTermMemory(data_dir="data/memory/long_term")
        for mem_type in ltm_instance.files.keys():
            cleaned = ltm_instance.compact(mem_type)
            results[mem_type] = cleaned
        
        logger.info(f"[记忆清理] 压缩完成: {results}")
        return results
    
    async def close(self):
        """关闭连接"""
        self._initialized = False
        logger.info("[记忆清理] 已关闭")
