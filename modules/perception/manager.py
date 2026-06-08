"""
感知管理器 - 动态感知外部变化

功能：
1. 文件/目录变化感知（watchdog 事件驱动，跨平台）
2. 对话上下文变化感知（自动对接消息流）
3. 画面/视觉变化感知（跨平台截图+信息处理API）

架构：PerceptionManager → AttentionPool → AI处理
"""
import os
import time
import platform
import hashlib
import threading
import asyncio
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from utils.logger import setup_logger

logger = setup_logger("perception")

PERCEPTION_PLATFORM = platform.system()


def watch_changes(watch_types=None, max_age_seconds=10.0):
    """
    Decorator to monitor perception changes after function execution.
    
    Args:
        watch_types: List of change types to watch ('file', 'dialog', 'screen').
                    If None, watches all.
        max_age_seconds: Maximum age of changes to consider (default 10s).
    
    Returns:
        Decorator that wraps a function and returns (result, changes_dict)
        where changes_dict contains lists of ChangeEvent objects per type.
    
    Usage:
        @watch_changes(watch_types=['file', 'dialog'])
        def my_function():
            # do something
            return result
            
        result, changes = my_function()
        # changes = {'file': [...], 'dialog': [...]}
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            # Execute the original function
            result = func(*args, **kwargs)
            
            # Gather changes
            changes = {}
            perception_mgr = perception_manager  # singleton
            
            if watch_types is None or 'file' in watch_types:
                changes['file'] = perception_mgr.file_perception.check_changes()
            if watch_types is None or 'dialog' in watch_types:
                # For dialog we need old and new messages; we'll skip unless provided via kwargs
                # For simplicity, we'll check auto dialog changes if messages available in kwargs
                dialog_old = kwargs.get('dialog_old_messages')
                dialog_new = kwargs.get('dialog_new_messages')
                if dialog_old is not None and dialog_new is not None:
                    changes['dialog'] = perception_mgr.dialog_perception.check_changes(dialog_old, dialog_new)
                else:
                    changes['dialog'] = []  # No dialog change detection without snapshots
            if watch_types is None or 'screen' in watch_types:
                changes['screen'] = perception_mgr.screen_perception.check_changes()
                
            # Filter by max_age
            now = time.time()
            for change_type in changes:
                changes[change_type] = [
                    c for c in changes[change_type]
                    if now - c.timestamp <= max_age_seconds
                ]
                
            return result, changes
        return wrapper
    return decorator


@dataclass
class ChangeEvent:
    """变化事件"""
    change_type: str
    target_type: str
    target: str
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    
    def to_prompt(self) -> str:
        """转换为提示文本"""
        if self.target_type == "file":
            icon_map = {
                "created": "📄", "modified": "📝", "deleted": "🗑️", "moved": "📦"
            }
            icon = icon_map.get(self.change_type, "📁")
            if self.change_type == "moved":
                return f"{icon} 移动: {self.details.get('from', self.target)} → {self.target}"
            return f"{icon} {self.change_type.title()}: {self.target}"
        
        elif self.target_type == "dialog":
            icon = "💬" if self.change_type == "created" else "🔄"
            return f"{icon} {self.target}"
        
        elif self.target_type == "screen":
            return f"🖥️ {self.target}: {self.details.get('change_desc', '画面变化')}"
        
        elif self.target_type == "speech":
            return f"🎤 语音输入: {self.target}"
        
        return f"[{self.target_type}] {self.change_type}: {self.target}"


@dataclass
class AttentionItem:
    """注意力项"""
    change: ChangeEvent
    urgency: float
    content: str


class PerceptionManager:
    """
    感知管理器
    
    改进版：
    - 使用 watchdog 事件驱动（跨平台）
    - 对接信息处理 API
    - 自动集成到主流程
    """
    
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(
        self,
        watch_paths: List[str] = None,
        check_interval: float = 2.0
    ):
        if self._initialized:
            return
        
        self.watch_paths = watch_paths or ["./", "data/"]
        self.check_interval = check_interval
        
        # 三类感知器
        self.file_perception = FilePerception(self.watch_paths)
        self.dialog_perception = DialogPerception()
        self.screen_perception = ScreenPerception()
        
        # 注意力池
        self.attention_pool: List[AttentionItem] = []
        self.max_attention_items = 50
        
        # 回调
        self.on_change_callbacks: List[Callable] = []
        
        # 后台监控
        self._running = False
        self._thread = None
        
        self._initialized = True
        logger.info("感知管理器初始化完成 (平台: %s)", PERCEPTION_PLATFORM)

    # ========== 文件感知 ==========
    
    def take_snapshot(self) -> None:
        """拍摄文件快照"""
        self.file_perception.take_snapshot()
    
    def check_file_changes(self) -> List[ChangeEvent]:
        """检查文件变化"""
        return self.file_perception.check_changes()
    
    # ========== 对话感知 ==========
    
    def update_dialog_snapshot(self, messages: List[Dict]) -> None:
        """更新对话快照"""
        self.dialog_perception.update_snapshot(messages)
    
    def check_dialog_changes(self, old_messages: List[Dict], new_messages: List[Dict]) -> List[ChangeEvent]:
        """检查对话变化"""
        return self.dialog_perception.check_changes(old_messages, new_messages)
    
    # ========== 屏幕感知（跨平台）==========

    def check_screen_changes(self) -> List[ChangeEvent]:
        """检查屏幕变化"""
        return self.screen_perception.check_changes()
    
    def capture_screen(self) -> Optional[bytes]:
        """捕获屏幕（跨平台）"""
        return self.screen_perception.capture()
    
    # ========== 注意力池 ==========
    
    def add_to_attention(self, change: ChangeEvent, urgency: float = 0.5) -> None:
        """添加变化到注意力池，并推送到差异检测器"""
        item = AttentionItem(
            change=change,
            urgency=urgency,
            content=change.to_prompt()
        )

        self.attention_pool.insert(0, item)

        if len(self.attention_pool) > self.max_attention_items:
            self.attention_pool = self.attention_pool[:self.max_attention_items]

        # 推送到差异检测器（推送模式）
        try:
            from modules.difference_detector import get_detector
            get_detector().ingest(
                target_type=change.target_type,
                change_type=change.change_type,
                target=change.target,
                details=change.details,
                urgency=urgency,
            )
        except Exception as e:
            logger.debug(f"推送到差异检测器失败: {e}")

        for callback in self.on_change_callbacks:
            try:
                callback(change, urgency)
            except Exception as e:
                logger.error(f"回调执行失败: {e}")
    
    def get_attention_items(self, max_age_seconds: float = 10.0) -> List[AttentionItem]:
        """获取注意力池内容"""
        now = time.time()
        return [
            item for item in self.attention_pool
            if now - item.change.timestamp <= max_age_seconds
        ]
    
    def get_attention_prompt(self) -> str:
        """获取注意力提示词（供AI使用）"""
        items = self.get_attention_items(max_age_seconds=10.0)
        
        if not items:
            return ""
        
        lines = ["【外部状态变化】(最近10秒)"]
        for item in items:
            lines.append(f"- {item.content}")
        
        return "\n".join(lines)
    
    def get_full_context(self) -> Dict[str, Any]:
        """获取完整感知上下文"""
        return {
            "attention_prompt": self.get_attention_prompt(),
            "recent_changes": [
                {
                    "type": item.change.target_type,
                    "content": item.content,
                    "urgency": item.urgency,
                    "timestamp": item.change.timestamp
                }
                for item in self.get_attention_items(max_age_seconds=30.0)
            ],
            "stats": {
                "total_items": len(self.attention_pool),
                "file_changes": len([e for e in self.attention_pool if e.change.target_type == "file"]),
                "dialog_changes": len([e for e in self.attention_pool if e.change.target_type == "dialog"]),
                "screen_changes": len([e for e in self.attention_pool if e.change.target_type == "screen"])
            }
        }
    
    def on_change(self, callback: Callable) -> None:
        """注册变化回调"""
        self.on_change_callbacks.append(callback)
    
    def clear_attention_pool(self) -> None:
        """清空注意力池"""
        self.attention_pool.clear()
    
    # ========== 后台监控 ==========
    
    def start_monitoring(self) -> None:
        """开始后台监控"""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("开始后台监控")
    
    def stop_monitoring(self) -> None:
        """停止后台监控"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        self.file_perception.stop()
        logger.info("停止后台监控")
    
    def _monitor_loop(self) -> None:
        """监控循环"""
        while self._running:
            try:
                for change in self.check_file_changes():
                    urgency = self._calculate_urgency(change)
                    self.add_to_attention(change, urgency)
                
                screen_changes = self.check_screen_changes()
                for change in screen_changes:
                    urgency = self._calculate_urgency(change)
                    self.add_to_attention(change, urgency)
                
            except Exception as e:
                logger.error(f"监控循环错误: {e}")
            
            time.sleep(self.check_interval)
    
    def _calculate_urgency(self, change: ChangeEvent) -> float:
        """计算紧急程度"""
        base = 0.5
        
        if change.change_type == "deleted":
            base = 0.8
        elif change.change_type == "created":
            base = 0.6
        
        if change.target_type == "file":
            ext = os.path.splitext(change.target)[1].lower()
            if ext in (".py", ".json", ".yaml", ".yml", ".md"):
                base += 0.2
        
        return min(1.0, base)


class FilePerception:
    """
    文件感知器 - 使用 watchdog 事件驱动
    跨平台支持：macOS / Windows / Linux
    """
    
    def __init__(self, watch_paths: List[str]):
        self.watch_paths = watch_paths
        self.snapshot: Dict[str, Dict[str, Any]] = {}
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
        # Q-15: Deduplicate changes from both watchdog and snapshot
        # Use set to track (path, event_type) tuples to avoid double-reporting
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

        # 增量快照对比 (only add if not already from watchdog)
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


class DialogPerception:
    """对话感知器"""
    
    def __init__(self):
        self.last_snapshot: List[Dict[str, Any]] = []
        self._messages_cache: List[Dict] = []
    
    def update_snapshot(self, messages: List[Dict[str, Any]]) -> None:
        """更新对话快照"""
        self._messages_cache = messages
        self.last_snapshot = [
            {"id": m.get("id", i), "role": m.get("role"), "content": m.get("content", "")[:100]}
            for i, m in enumerate(messages)
        ]
    
    def check_changes(self, old_messages: List[Dict], new_messages: List[Dict]) -> List[ChangeEvent]:
        """检查对话变化"""
        changes = []
        
        old_ids = {m.get("id") or i for i, m in enumerate(old_messages)}
        new_ids = {m.get("id") or i for i, m in enumerate(new_messages)}
        
        for i, msg in enumerate(new_messages):
            msg_id = msg.get("id") or i
            
            if msg_id not in old_ids:
                content = msg.get("content", "")[:100]
                role = msg.get("role", "user")
                changes.append(ChangeEvent(
                    change_type="created",
                    target_type="dialog",
                    target=f"[{role}] {content}",
                    details={"role": role, "id": msg_id}
                ))
        
        return changes
    
    def auto_check(self, messages: List[Dict]) -> List[ChangeEvent]:
        """自动检查变化（使用上次快照）"""
        return self.check_changes(self.last_snapshot, messages)


class ScreenPerception:
    """
    屏幕感知器 - 跨平台支持
    macOS: screencapture
    Windows: PIL + MSS
    Linux: scrot
    """
    
    def __init__(self, hash_size: int = 8):
        self.hash_size = hash_size
        self.last_hash = ""
        self.last_capture_time = 0
        self._platform = PERCEPTION_PLATFORM
    
    def _capture_macos(self) -> Optional[bytes]:
        """macOS 截图"""
        try:
            import subprocess
            result = subprocess.run(
                ["screencapture", "-x", "-"],
                capture_output=True,
                timeout=2
            )
            if result.returncode == 0:
                return result.stdout
        except Exception as e:
            logger.debug("macOS 截图失败 (非致命): %s", e)
        return None

    def _capture_windows(self) -> Optional[bytes]:
        """Windows 截图"""
        try:
            import io
            from PIL import ImageGrab
            img = ImageGrab.grab()
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            return buf.getvalue()
        except Exception as e:
            logger.debug("Windows 截图失败 (非致命): %s", e)
        return None

    def _capture_linux(self) -> Optional[bytes]:
        """Linux 截图"""
        try:
            import subprocess
            result = subprocess.run(
                ["scrot", "-o", "-"],
                capture_output=True,
                timeout=2
            )
            if result.returncode == 0:
                return result.stdout
        except Exception as e:
            logger.debug("Linux 截图失败 (非致命): %s", e)
        return None
    
    def capture(self) -> Optional[bytes]:
        """跨平台截图"""
        if self._platform == "Darwin":
            return self._capture_macos()
        elif self._platform == "Windows":
            return self._capture_windows()
        else:
            return self._capture_linux()
    
    def _calculate_hash(self, image_data: bytes) -> str:
        """
        Q-16: Calculate perceptual hash instead of cryptographic hash.

        MD5 is too sensitive to minor pixel changes. Use PIL to downsample
        and compare grayscale values for better robustness to noise.
        """
        try:
            from PIL import Image
            import io

            img = Image.open(io.BytesIO(image_data))
            # Resize to small grid (8x8) for perceptual hashing
            img_small = img.resize((8, 8), Image.Resampling.LANCZOS)
            img_gray = img_small.convert('L')

            # Calculate average pixel value
            pixels = list(img_gray.getdata())
            avg_pixel = sum(pixels) // len(pixels)

            # Create hash: 1 if pixel > average, 0 otherwise
            hash_bits = ''.join(['1' if p > avg_pixel else '0' for p in pixels])
            # Convert to hex
            hash_hex = hex(int(hash_bits, 2))[2:].zfill(16)
            return hash_hex[:self.hash_size]

        except Exception as e:
            # Fallback to MD5 if PIL fails
            logger.warning(f"Perceptual hash calculation failed: {e}, falling back to MD5")
            return hashlib.md5(image_data).hexdigest()[:self.hash_size]
    
    def check_changes(self) -> List[ChangeEvent]:
        """检查屏幕变化"""
        changes = []
        
        image_data = self.capture()
        
        if image_data:
            current_hash = self._calculate_hash(image_data)
            
            if current_hash != self.last_hash and self.last_hash:
                changes.append(ChangeEvent(
                    change_type="changed",
                    target_type="screen",
                    target="桌面画面",
                    details={"old_hash": self.last_hash, "new_hash": current_hash}
                ))
            
            self.last_hash = current_hash
            self.last_capture_time = time.time()
        
        return changes


# CONC-7: Use lazy factory instead of module-level singleton
# Avoid initializing hardware at import time (breaks CI/headless environments)
_perception_manager_instance = None

def get_perception_manager() -> PerceptionManager:
    """Get or create perception manager instance (lazy factory)"""
    global _perception_manager_instance
    if _perception_manager_instance is None:
        _perception_manager_instance = PerceptionManager()
    return _perception_manager_instance

# Backwards compatibility: module-level access via property
class _PerceptionManagerProxy:
    def __getattr__(self, name):
        return getattr(get_perception_manager(), name)

perception_manager = _PerceptionManagerProxy()
