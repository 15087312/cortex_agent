"""
全局上下文池 — 所有数据的唯一存储地

设计原则：
- 文件只存一份，所有模型共享 hash/embedding/summary
- 全局状态单一来源，避免不一致
- 事件日志自动 TTL 裁剪
- 线程安全 (RLock)
"""
import time
import threading
import copy
from typing import Dict, List, Optional, Any
from pathlib import Path

from utils.logger import setup_logger
from .types import (
    FileInfo, ProjectMetadata, GlobalState, EventRecord,
    ContextView, ModelRole, EventType
)

logger = setup_logger("global_context_pool")

# 配置常量
DEFAULT_MAX_EVENTS = 10000       # 事件日志最大条数
DEFAULT_EVENT_TTL_SECONDS = 3600  # 事件 TTL (1 小时)
DEFAULT_MAX_SESSIONS = 1000      # 会话上下文最大数


class GlobalContextPool:
    """
    全局上下文池 — 单例

    所有数据唯一存储地：
    - 项目元数据
    - 文件内容缓存（含预计算的 hash/embedding/summary）
    - 全局状态（任务进度）
    - 事件日志（所有模型输出、工具调用、探针信号）
    - 记忆索引引用（不复制，仅引用）
    """

    def __init__(self):
        self._lock = threading.RLock()

        # 项目元数据
        self.metadata = ProjectMetadata()

        # 文件缓存: path → FileInfo
        self._files: Dict[str, FileInfo] = {}

        # 全局状态
        self._state = GlobalState()

        # 事件日志: List[EventRecord], 按时间倒序
        self._events: List[EventRecord] = []
        self._max_events = DEFAULT_MAX_EVENTS
        self._event_ttl_seconds = DEFAULT_EVENT_TTL_SECONDS

        # 记忆索引引用 (不复制，仅存引用)
        self._memory_index: Any = None

        # 会话上下文
        self._session_contexts: Dict[str, Dict[str, Any]] = {}

        logger.info("GlobalContextPool 初始化完成")

    # ========================================================================
    # 文件缓存
    # ========================================================================

    def get_file(self, filepath: str) -> Optional[FileInfo]:
        """获取文件信息（含缓存）"""
        with self._lock:
            return self._files.get(filepath)

    def put_file(self, filepath: str, info: FileInfo) -> None:
        """存储文件信息"""
        with self._lock:
            self._files[filepath] = info
            logger.debug("文件缓存: %s (%d bytes)", filepath, info.size_bytes)

    def remove_file(self, filepath: str) -> None:
        """移除文件缓存"""
        with self._lock:
            self._files.pop(filepath, None)

    def get_file_hash(self, filepath: str) -> Optional[str]:
        """获取文件哈希（用于变更检测）"""
        info = self.get_file(filepath)
        return info.hash if info else None

    def file_count(self) -> int:
        with self._lock:
            return len(self._files)

    def get_all_file_paths(self) -> List[str]:
        """获取所有缓存文件路径（线程安全）"""
        with self._lock:
            return list(self._files.keys())

    def get_all_files(self) -> Dict[str, FileInfo]:
        """获取所有缓存文件（深拷贝，线程安全）"""
        with self._lock:
            return copy.deepcopy(self._files)

    def get_all_events(self) -> List[EventRecord]:
        """获取所有事件（深拷贝，线程安全）"""
        with self._lock:
            return copy.deepcopy(self._events)

    # ========================================================================
    # 全局状态
    # ========================================================================

    def get_state(self) -> GlobalState:
        """获取全局状态快照（深拷贝，外部修改不影响池内数据）"""
        with self._lock:
            return copy.deepcopy(self._state)

    def update_state(self, **kwargs) -> None:
        """更新全局状态（线程安全）"""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._state, key):
                    setattr(self._state, key, value)
                else:
                    self._state.user_context[key] = value
            logger.debug("全局状态更新: %s", list(kwargs.keys()))

    def add_task(self, task: Dict[str, Any]) -> None:
        """添加活跃任务"""
        with self._lock:
            task["_added_at"] = time.time()
            self._state.active_tasks.append(task)

    def complete_task(self, task_id: str, result: Dict[str, Any] = None) -> bool:
        """标记任务完成"""
        with self._lock:
            for task in self._state.active_tasks:
                if task.get("id") == task_id:
                    self._state.active_tasks.remove(task)
                    task["completed_at"] = time.time()
                    task["result"] = result or {}
                    self._state.completed_tasks.append(task)
                    self._state.current_step += 1
                    if self._state.todos:
                        total = len(self._state.todos)
                        if total > 0:
                            self._state.overall_progress = min(
                                1.0, self._state.current_step / total
                            )
                    return True
        return False

    # ========================================================================
    # 事件日志
    # ========================================================================

    def add_event(self, record: EventRecord) -> None:
        """追加事件到日志（优化：减少列表重建次数）"""
        with self._lock:
            self._events.insert(0, record)  # 新事件在列表头部

            # 先进行 TTL 裁剪，然后再按数量裁剪（避免两次列表重建）
            cutoff = time.time() - self._event_ttl_seconds

            # 同时进行 TTL 和数量裁剪，只创建一次新列表
            filtered_events = []
            for e in self._events:
                if e.timestamp > cutoff:
                    filtered_events.append(e)
                    if len(filtered_events) >= self._max_events:
                        break

            self._events = filtered_events

    def get_events(
        self,
        source_role: str = None,
        event_type: EventType = None,
        limit: int = 100,
        min_importance: float = 0.0
    ) -> List[EventRecord]:
        """查询事件（支持按角色/类型过滤）"""
        with self._lock:
            results = []
            for e in self._events:
                if source_role and e.source_role != source_role:
                    continue
                if event_type and e.event_type != event_type:
                    continue
                if e.importance < min_importance:
                    continue
                results.append(e)
                if len(results) >= limit:
                    break
            return results

    def get_recent_outputs(self, limit: int = 20) -> List[str]:
        """获取最近的模型输出文本列表"""
        events = self.get_events(
            event_type=EventType.MODEL_OUTPUT, limit=limit
        )
        return [
            str(e.content)[:500]
            for e in events
            if isinstance(e.content, str)
        ]

    def event_count(self) -> int:
        with self._lock:
            return len(self._events)

    # ========================================================================
    # 记忆索引引用
    # ========================================================================

    def set_memory_index(self, index: Any) -> None:
        """设置记忆索引引用（不复制，仅存引用）"""
        with self._lock:
            self._memory_index = index

    def get_memory_index(self) -> Any:
        """获取记忆索引引用"""
        return self._memory_index

    # ========================================================================
    # 会话上下文
    # ========================================================================

    def set_session_context(self, session_id: str, context: Dict[str, Any]) -> None:
        """设置会话级上下文"""
        with self._lock:
            self._session_contexts[session_id] = context

    def get_session_context(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取会话级上下文（线程安全）"""
        with self._lock:
            return copy.deepcopy(self._session_contexts.get(session_id))

    def clear_session(self, session_id: str) -> None:
        """清除会话上下文"""
        with self._lock:
            self._session_contexts.pop(session_id, None)

    # ========================================================================
    # 维护
    # ========================================================================

    def get_stats(self) -> Dict[str, Any]:
        """获取池统计"""
        with self._lock:
            return {
                "files_cached": len(self._files),
                "events_stored": len(self._events),
                "active_tasks": len(self._state.active_tasks),
                "completed_tasks": len(self._state.completed_tasks),
                "sessions": len(self._session_contexts),
                "progress": self._state.overall_progress
            }

    def clear(self) -> None:
        """清空所有数据（慎用）"""
        with self._lock:
            self._files.clear()
            self._state = GlobalState()
            self._events.clear()
            self._session_contexts.clear()
            logger.warning("GlobalContextPool 已清空")


# 模块级工厂函数 + 向后兼容
import threading as _threading

_instance = None
_init_lock = _threading.Lock()


def get_global_context_pool() -> GlobalContextPool:
    global _instance
    if _instance is None:
        with _init_lock:
            if _instance is None:
                _instance = GlobalContextPool()
    return _instance


# 向后兼容
gcm_pool = get_global_context_pool()
