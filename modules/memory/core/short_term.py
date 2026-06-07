"""
短期记忆与上下文记忆

像人脑的工作记忆，用完就丢，只存当前对话/任务。
特性：速度最快、容量小、重启后清空。
"""
import time
from typing import Dict, Any, List, Optional, Deque
from collections import deque
from utils.logger import setup_logger


class ShortTermMemory:
    """
    短期记忆管理器
    
    负责：
    - 工作记忆池（当前任务）
    - 对话上下文队列（最近 N 轮）
    - 单轮思考缓存
    """

    def __init__(self, max_context_turns: int = 20, max_working_items: int = 50):
        """
        初始化短期记忆
        
        Args:
            max_context_turns: 最大对话轮次
            max_working_items: 最大工作记忆项
        """
        self.max_context_turns = max_context_turns
        self.max_working_items = max_working_items
        self.logger = setup_logger("short_term_memory")
        
        # 对话上下文队列
        self.context_queue: Deque[Dict[str, Any]] = deque(maxlen=max_context_turns)
        
        # 工作记忆池
        self.working_memory: Dict[str, Any] = {}
        
        # 思考缓存
        self.thought_cache: Dict[str, Any] = {}
        
        # 当前情绪状态
        self.current_emotion: Optional[Dict[str, Any]] = None
        
        # 当前任务目标
        self.current_task: Optional[Dict[str, Any]] = None
        
        self.logger.info(
            "短期记忆初始化完成 (最大对话轮次: %d, 最大工作项: %d)",
            max_context_turns, max_working_items
        )

    # ========== 对话上下文 ==========

    def add_dialog(self, role: str, text: str, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        添加对话到上下文
        
        Args:
            role: 角色 (user, assistant, system)
            text: 对话内容
            metadata: 元数据
            
        Returns:
            添加的对话记录
        """
        dialog = {
            "role": role,
            "text": text,
            "timestamp": time.time(),
            "metadata": metadata or {}
        }
        
        self.context_queue.append(dialog)
        self.logger.debug("添加对话 [%s]: %s...", role, text[:50])
        
        return dialog

    def get_context(self, limit: int = None) -> List[Dict[str, Any]]:
        """
        获取对话上下文
        
        Args:
            limit: 返回轮次限制
            
        Returns:
            对话上下文列表
        """
        context = list(self.context_queue)
        
        if limit:
            context = context[-limit:]
        
        return context

    def get_recent_dialogs(self, count: int = 5) -> List[Dict[str, Any]]:
        """获取最近 N 轮对话"""
        return list(self.context_queue)[-count:]

    def clear_context(self) -> None:
        """清空对话上下文"""
        self.context_queue.clear()
        self.logger.info("对话上下文已清空")

    # ========== 工作记忆池 ==========

    def set_working_memory(self, key: str, value: Any, ttl: float = None) -> None:
        """
        设置工作记忆
        
        Args:
            key: 键
            value: 值
            ttl: 存活时间（秒），None 表示永久
        """
        self.working_memory[key] = {
            "value": value,
            "timestamp": time.time(),
            "ttl": ttl
        }
        
        if len(self.working_memory) > self.max_working_items:
            self._cleanup_working_memory()

    def get_working_memory(self, key: str) -> Optional[Any]:
        """
        获取工作记忆
        
        Args:
            key: 键
            
        Returns:
            值，如果不存在或过期返回 None
        """
        if key not in self.working_memory:
            return None
        
        item = self.working_memory[key]
        
        # 检查是否过期
        if item["ttl"] and (time.time() - item["timestamp"]) > item["ttl"]:
            del self.working_memory[key]
            return None
        
        return item["value"]

    def delete_working_memory(self, key: str) -> bool:
        """删除工作记忆"""
        if key in self.working_memory:
            del self.working_memory[key]
            return True
        return False

    def _cleanup_working_memory(self) -> None:
        """清理过期或超出容量的工作记忆"""
        current_time = time.time()
        expired_keys = [
            k for k, v in self.working_memory.items()
            if v["ttl"] and (current_time - v["timestamp"]) > v["ttl"]
        ]
        
        for key in expired_keys:
            del self.working_memory[key]
        
        # 如果还是超出，删除最旧的
        if len(self.working_memory) > self.max_working_items:
            sorted_items = sorted(
                self.working_memory.items(),
                key=lambda x: x[1]["timestamp"]
            )
            for key, _ in sorted_items[:len(sorted_items) - self.max_working_items]:
                del self.working_memory[key]

    def get_all_working_memory(self) -> Dict[str, Any]:
        """获取所有工作记忆"""
        return {k: v["value"] for k, v in self.working_memory.items()}

    # ========== 思考缓存 ==========

    def cache_thought(self, thought_id: str, thought: Dict[str, Any]) -> None:
        """
        缓存思考结果
        
        Args:
            thought_id: 思考 ID
            thought: 思考内容
        """
        self.thought_cache[thought_id] = {
            "thought": thought,
            "timestamp": time.time()
        }
        self.logger.debug("缓存思考结果: %s", thought_id)

    def get_cached_thought(self, thought_id: str) -> Optional[Dict[str, Any]]:
        """获取缓存的思考"""
        if thought_id in self.thought_cache:
            return self.thought_cache[thought_id]["thought"]
        return None

    def clear_thought_cache(self) -> None:
        """清空思考缓存"""
        self.thought_cache.clear()

    # ========== 情绪与任务 ==========

    def set_current_emotion(self, emotion: Dict[str, Any]) -> None:
        """设置当前情绪状态"""
        self.current_emotion = emotion
        self.logger.debug("更新当前情绪: %s", emotion.get("emotion"))

    def get_current_emotion(self) -> Optional[Dict[str, Any]]:
        """获取当前情绪状态"""
        return self.current_emotion

    def set_current_task(self, task: Dict[str, Any]) -> None:
        """设置当前任务目标"""
        self.current_task = task
        self.logger.debug("更新当前任务: %s", task.get("description"))

    def get_current_task(self) -> Optional[Dict[str, Any]]:
        """获取当前任务目标"""
        return self.current_task

    # ========== 状态与清理 ==========

    def get_status(self) -> Dict[str, Any]:
        """获取短期记忆状态"""
        return {
            "context_turns": len(self.context_queue),
            "working_items": len(self.working_memory),
            "cached_thoughts": len(self.thought_cache),
            "current_emotion": self.current_emotion,
            "current_task": self.current_task.get("description") if self.current_task else None,
            "max_context_turns": self.max_context_turns,
            "max_working_items": self.max_working_items
        }

    def clear_all(self) -> None:
        """清空所有短期记忆"""
        self.context_queue.clear()
        self.working_memory.clear()
        self.thought_cache.clear()
        self.current_emotion = None
        self.current_task = None
        self.logger.info("所有短期记忆已清空")
