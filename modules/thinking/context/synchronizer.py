"""
同步器 — 保持 GlobalContextPool 与外部系统一致

职责：
- 文件监听（watchdog）：检测文件变更并更新池
- 模型输出同步：模型生成后自动写入事件日志
- 工具调用同步：工具执行后写入事件日志
- 探针信号同步：探针结果写入事件日志
- 冲突解决：同一文件多写时的合并策略
"""
import os
import time
import threading
from typing import Dict, List, Optional, Any, Callable
from pathlib import Path

from utils.logger import setup_logger
from .types import (
    FileInfo, EventRecord, EventType, ModelRole, GlobalState
)
from .global_context_pool import GlobalContextPool

logger = setup_logger("synchronizer")

# 文件监听安全限制
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB，超过不读内容
BINARY_CHECK_BYTES = 1024               # 读取前 1KB 检测是否为二进制


DEFAULT_WATCH_INTERVAL = 2.0  # 文件轮询间隔（秒）


class Synchronizer:
    """
    同步器 — 单例

    负责将外部变化同步到 GlobalContextPool，确保池始终是"最新真相"。
    """

    def __init__(self):
        self._pool: Optional[GlobalContextPool] = None
        self._watcher_thread: Optional[threading.Thread] = None
        self._watching = False
        self._watch_paths: List[str] = []
        self._watch_interval = DEFAULT_WATCH_INTERVAL
        self._file_mtimes: Dict[str, float] = {}
        self._on_change_callbacks: List[Callable] = []

    # ========================================================================
    # 文件监听
    # ========================================================================

    def start_watching(
        self,
        pool: GlobalContextPool,
        paths: Optional[List[str]] = None,
        interval: float = 2.0,
        recursive: bool = True
    ) -> None:
        """
        启动文件监听（后台线程）

        Args:
            pool: 目标上下文池
            paths: 监听目录列表（默认当前目录）
            interval: 轮询间隔（秒）
            recursive: 是否递归监听
        """
        if self._watching:
            logger.warning("已在监听中")
            return

        self._pool = pool
        self._watch_paths = paths or [os.getcwd()]
        self._watch_interval = interval
        self._watching = True

        # 初始化文件修改时间快照
        self._snapshot_files()

        self._watcher_thread = threading.Thread(
            target=self._watch_loop,
            daemon=True,
            name="gcm-file-watcher"
        )
        self._watcher_thread.start()
        logger.info(
            "文件监听已启动: paths=%s interval=%.1fs",
            self._watch_paths, interval
        )

    def stop_watching(self) -> None:
        """停止文件监听"""
        self._watching = False
        if self._watcher_thread:
            self._watcher_thread.join(timeout=5.0)
        logger.info("文件监听已停止")

    def _snapshot_files(self) -> None:
        """扫描并记录所有文件 mtime"""
        for watch_path in self._watch_paths:
            base = Path(watch_path)
            if not base.exists():
                continue
            for f in base.rglob("*"):
                if f.is_file() and not self._is_ignored(f):
                    try:
                        self._file_mtimes[str(f)] = f.stat().st_mtime
                    except OSError as e:
                        logger.debug(f"[文件快照] 无法获取文件状态: {f}: {e}")

    def _watch_loop(self) -> None:
        """后台监听循环"""
        while self._watching:
            try:
                changed = self._detect_changes()
                if changed:
                    for filepath, action in changed:
                        self._on_file_change(filepath, action)
                    for cb in self._on_change_callbacks:
                        try:
                            cb(changed)
                        except Exception as e:
                            logger.debug(f"变更回调失败: {e}")
            except Exception as e:
                logger.error(f"文件监听异常: {e}")

            time.sleep(self._watch_interval)

    def _detect_changes(self) -> List[tuple]:
        """检测文件变更 → [(filepath, action), ...]"""
        changes = []

        for watch_path in self._watch_paths:
            base = Path(watch_path)
            if not base.exists():
                continue

            for f in base.rglob("*"):
                if not f.is_file() or self._is_ignored(f):
                    continue

                fpath = str(f)
                old_mtime = self._file_mtimes.get(fpath)

                try:
                    new_mtime = f.stat().st_mtime
                except OSError as e:
                    # 文件已删除
                    logger.debug(f"[变更检测] 文件不可访问 (可能已删除): {fpath}: {e}")
                    if old_mtime is not None:
                        changes.append((fpath, "deleted"))
                        self._file_mtimes.pop(fpath, None)
                    continue

                if old_mtime is None:
                    changes.append((fpath, "created"))
                    self._file_mtimes[fpath] = new_mtime
                elif new_mtime > old_mtime:
                    changes.append((fpath, "modified"))
                    self._file_mtimes[fpath] = new_mtime

        return changes

    def _on_file_change(self, filepath: str, action: str) -> None:
        """文件变更处理"""
        if not self._pool:
            return

        try:
            if action == "deleted":
                self._pool.remove_file(filepath)
                logger.debug("文件移除: %s", filepath)
            else:
                fpath = Path(filepath)
                # 大小限制：跳过超大文件
                try:
                    fsize = fpath.stat().st_size
                except OSError:
                    logger.warning("文件不可访问: %s", filepath)
                    return
                if fsize > MAX_FILE_SIZE_BYTES:
                    logger.warning("文件过大 (%d MB)，跳过: %s", fsize // (1024 * 1024), filepath)
                    return
                # 二进制检测：跳过二进制文件
                try:
                    with open(filepath, "rb") as fh:
                        head = fh.read(BINARY_CHECK_BYTES)
                    if b"\x00" in head:
                        logger.debug("跳过二进制文件: %s", filepath)
                        return
                except OSError:
                    pass

                content = fpath.read_text(encoding="utf-8", errors="replace")
                info = FileInfo(path=filepath, content=content)
                self._pool.put_file(filepath, info)

                # 写入变更事件
                event = EventRecord(
                    event_type=EventType.FILE_CHANGE,
                    source_role="system",
                    content=f"文件{action}: {filepath}",
                    metadata={"filepath": filepath, "action": action, "size": info.size_bytes}
                )
                self._pool.add_event(event)
                logger.debug("文件同步: %s (%s)", filepath, action)
        except Exception as e:
            logger.warning(f"文件同步失败 {filepath}: {e}")

    def on_change(self, callback: Callable) -> None:
        """注册变更回调"""
        self._on_change_callbacks.append(callback)

    @staticmethod
    def _is_ignored(path: Path) -> bool:
        """检查是否忽略"""
        ignored_patterns = [
            "__pycache__", ".git", ".venv", "venv", "node_modules",
            ".DS_Store", "*.pyc", "*.pyo", ".tox", ".mypy_cache",
            ".pytest_cache", ".claude", ".omc", "*.egg-info"
        ]
        parts = path.parts
        for pattern in ignored_patterns:
            if pattern.startswith("*"):
                if path.suffix == pattern[1:]:
                    return True
            elif pattern in parts:
                return True
        return False

    # ========================================================================
    # 模型输出同步
    # ========================================================================

    def sync_model_output(
        self,
        pool: GlobalContextPool,
        source_role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        importance: float = 0.5
    ) -> EventRecord:
        """
        同步模型输出到上下文池

        Args:
            pool: 上下文池
            source_role: 模型角色名
            content: 输出内容
            metadata: 附加元数据
            importance: 重要性评分

        Returns:
            创建的 EventRecord
        """
        event = EventRecord(
            event_type=EventType.MODEL_OUTPUT,
            source_role=source_role,
            content=content,
            metadata=metadata or {},
            importance=importance
        )
        pool.add_event(event)
        logger.debug("模型输出同步: role=%s len=%d", source_role, len(str(content)))
        return event

    # ========================================================================
    # 工具调用同步
    # ========================================================================

    def sync_tool_call(
        self,
        pool: GlobalContextPool,
        source_role: str,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_output: Any,
        success: bool = True,
        latency_ms: float = 0.0
    ) -> EventRecord:
        """
        同步工具调用到上下文池

        Args:
            pool: 上下文池
            source_role: 调用方角色
            tool_name: 工具名
            tool_input: 工具输入参数
            tool_output: 工具输出
            success: 是否成功
            latency_ms: 延迟(毫秒)

        Returns:
            创建的 EventRecord
        """
        content = f"[{tool_name}] {'成功' if success else '失败'}: {str(tool_output)[:200]}"
        event = EventRecord(
            event_type=EventType.TOOL_CALL,
            source_role=source_role,
            content=content,
            metadata={
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_output": str(tool_output)[:500],
                "success": success,
                "latency_ms": latency_ms
            },
            importance=0.6 if not success else 0.4
        )
        pool.add_event(event)
        logger.debug("工具调用同步: %s success=%s", tool_name, success)
        return event

    # ========================================================================
    # 探针信号同步
    # ========================================================================

    def sync_probe_signal(
        self,
        pool: GlobalContextPool,
        probe_name: str,
        signal_type: str,
        data: Any,
        importance: float = 0.5
    ) -> EventRecord:
        """
        同步探针信号到上下文池

        Args:
            pool: 上下文池
            probe_name: 探针名
            signal_type: 信号类型
            data: 信号数据
            importance: 重要性

        Returns:
            创建的 EventRecord
        """
        content = f"[Probe:{probe_name}] {signal_type}: {str(data)[:200]}"
        event = EventRecord(
            event_type=EventType.PROBE_SIGNAL,
            source_role="probe",
            content=content,
            metadata={
                "probe_name": probe_name,
                "signal_type": signal_type,
                "data": str(data)[:500]
            },
            importance=importance
        )
        pool.add_event(event)
        logger.debug("探针信号同步: %s/%s", probe_name, signal_type)
        return event

    # ========================================================================
    # 冲突解决
    # ========================================================================

    def resolve_conflict(
        self,
        pool: GlobalContextPool,
        filepath: str,
        incoming: FileInfo,
        strategy: str = "latest"
    ) -> FileInfo:
        """
        解决文件冲突（多个源同时写入同一文件）

        Args:
            pool: 上下文池
            filepath: 冲突文件路径
            incoming: 新文件信息
            strategy: 冲突策略
                - "latest": 取最新修改时间
                - "incoming": 始终接受新版本
                - "merge": 简单文本合并（追加差异行）

        Returns:
            最终文件信息
        """
        existing = pool.get_file(filepath)

        if not existing:
            pool.put_file(filepath, incoming)
            return incoming

        if strategy == "latest":
            if incoming.last_modified >= existing.last_modified:
                pool.put_file(filepath, incoming)
                return incoming
            return existing

        elif strategy == "incoming":
            pool.put_file(filepath, incoming)
            return incoming

        elif strategy == "merge":
            # 简单合并：追加 incoming 中有而 existing 中无的行
            existing_lines = set(existing.content.split('\n'))
            incoming_lines = incoming.content.split('\n')
            new_lines = [l for l in incoming_lines if l not in existing_lines]

            if new_lines:
                merged_content = existing.content + '\n' + '\n'.join(new_lines)
                merged = FileInfo(
                    path=filepath,
                    content=merged_content,
                    last_modified=max(existing.last_modified, incoming.last_modified)
                )
                pool.put_file(filepath, merged)
                logger.info("文件合并: %s (+%d lines)", filepath, len(new_lines))
                return merged
            return existing

        else:
            logger.warning(f"未知冲突策略: {strategy}, 使用 'latest'")
            return self.resolve_conflict(pool, filepath, incoming, "latest")


# 模块级工厂函数 + 向后兼容
import threading as _threading

_instance = None
_init_lock = _threading.Lock()


def get_synchronizer() -> Synchronizer:
    global _instance
    if _instance is None:
        with _init_lock:
            if _instance is None:
                _instance = Synchronizer()
    return _instance


# 向后兼容
synchronizer = get_synchronizer()
