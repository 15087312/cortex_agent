"""文件感知器 — watchdog 事件驱动 + 快照对比

跨平台支持：macOS / Windows / Linux
"""
import os
import threading
from typing import Dict, Any, List

from utils.logger import setup_logger
from modules.perception.change_event import ChangeEvent

logger = setup_logger("file_perception")


class FilePerception:
    """文件感知器 - 使用 watchdog 事件驱动"""

    def __init__(self, watch_paths: List[str], enabled: bool = True):
        self.watch_paths = watch_paths
        self.snapshot: Dict[str, Dict[str, Any]] = {}
        self.enabled = enabled
        self._observer = None
        self._event_queue = []
        self._lock = threading.Lock()

        self.take_snapshot()
        self._start_watching()

    def _start_watching(self) -> None:
        """启动文件监控"""
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            class ChangeHandler(FileSystemEventHandler):
                def __init__(self, perception):
                    self.perception = perception

                def on_any_event(self, event):
                    if event.is_directory:
                        return
                    self.perception._handle_event(event)

            self._observer = Observer()
            handler = ChangeHandler(self)

            for path in self.watch_paths:
                if os.path.exists(path):
                    self._observer.schedule(handler, path, recursive=True)

            self._observer.start()
            logger.info("文件监控已启动 (watchdog)")

        except ImportError:
            logger.warning("watchdog 未安装，使用轮询模式")
        except Exception as e:
            logger.error(f"文件监控启动失败: {e}")

    def _handle_event(self, event) -> None:
        """处理文件系统事件"""
        with self._lock:
            change_type = event.event_type
            if hasattr(event, 'src_path'):
                self._event_queue.append({
                    "type": change_type,
                    "path": event.src_path,
                    "is_directory": event.is_directory
                })

    def take_snapshot(self) -> None:
        """拍摄快照"""
        new_snapshot = {}

        for base_path in self.watch_paths:
            if not os.path.exists(base_path):
                continue

            for root, dirs, files in os.walk(base_path):
                dirs[:] = [d for d in dirs if not d.startswith('.')]

                for filename in files:
                    if filename.startswith('.'):
                        continue

                    filepath = os.path.join(root, filename)

                    try:
                        stat = os.stat(filepath)
                        new_snapshot[filepath] = {
                            "mtime": stat.st_mtime,
                            "size": stat.st_size
                        }
                    except Exception as e:
                        logger.debug("文件快照 stat 失败 (可能已删除): %s", e)

        with self._lock:
            self.snapshot = new_snapshot

    def check_changes(self) -> List[ChangeEvent]:
        """检查文件变化"""
        seen_changes = set()
        changes = []

        # 处理 watchdog 事件
        with self._lock:
            events = self._event_queue[:]
            self._event_queue.clear()

        for event in events:
            if event["is_directory"]:
                continue

            path = event["path"]
            event_type = event["type"]

            if event_type == "created":
                change_key = (path, "created")
                if change_key not in seen_changes:
                    changes.append(ChangeEvent("created", "file", path))
                    seen_changes.add(change_key)
            elif event_type == "deleted":
                change_key = (path, "deleted")
                if change_key not in seen_changes:
                    changes.append(ChangeEvent("deleted", "file", path))
                    seen_changes.add(change_key)
            elif event_type == "modified":
                change_key = (path, "modified")
                if change_key not in seen_changes:
                    changes.append(ChangeEvent("modified", "file", path))
                    seen_changes.add(change_key)
            elif event_type == "moved":
                change_key = (event.get("dest_path", path), "moved")
                if change_key not in seen_changes:
                    changes.append(ChangeEvent("moved", "file", event.get("dest_path", path), {
                        "from": path
                    }))
                    seen_changes.add(change_key)

        # 增量快照对比
        old_snapshot = self.snapshot.copy()
        self.take_snapshot()
        new_snapshot = self.snapshot

        old_files = set(old_snapshot.keys())
        new_files = set(new_snapshot.keys())

        for filepath in new_files - old_files:
            change_key = (filepath, "created")
            if change_key not in seen_changes:
                changes.append(ChangeEvent("created", "file", filepath))
                seen_changes.add(change_key)

        for filepath in old_files - new_files:
            change_key = (filepath, "deleted")
            if change_key not in seen_changes:
                changes.append(ChangeEvent("deleted", "file", filepath))
                seen_changes.add(change_key)

        for filepath in old_files & new_files:
            old_info = old_snapshot[filepath]
            new_info = new_snapshot[filepath]

            if old_info["mtime"] != new_info["mtime"]:
                change_key = (filepath, "modified")
                if change_key not in seen_changes:
                    changes.append(ChangeEvent("modified", "file", filepath))
                    seen_changes.add(change_key)

        return changes

    def stop(self) -> None:
        """停止监控"""
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2.0)
