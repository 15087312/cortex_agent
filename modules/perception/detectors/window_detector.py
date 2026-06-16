"""窗口状态检测器

Windows: pywin32 (GetForegroundWindow)
macOS: pyobjc (NSWorkspace + Accessibility API)

检测窗口切换、活跃应用变化。
"""
import platform
import time
from typing import Any, Dict, List, Optional

import numpy as np

from modules.perception.detectors.base import PerceptionDetector
from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from utils.logger import setup_logger

logger = setup_logger("perception_window_detector")


class WindowDetector(PerceptionDetector):
    """窗口状态检测器

    检测当前活跃窗口的变化，产出 SCREEN_WINDOW 事件。
    不依赖屏幕图像，直接调用系统 API。
    """

    def __init__(self):
        self._platform = platform.system()
        self._last_window: Optional[str] = None
        self._last_app: Optional[str] = None
        self._backend = None
        self._init_backend()

    def _init_backend(self):
        """根据平台初始化窗口检测后端

        Windows: pywin32 (win32gui.GetForegroundWindow)
        macOS: pyobjc (NSWorkspace.frontmostApplication)
        """
        if self._platform == "Windows":
            try:
                import win32gui  # noqa: F401
                self._backend = "win32"
            except ImportError:
                logger.debug("pywin32 不可用")
        elif self._platform == "Darwin":
            try:
                from AppKit import NSWorkspace  # noqa: F401
                self._backend = "appkit"
            except ImportError:
                logger.debug("pyobjc AppKit 不可用")

    def is_available(self) -> bool:
        return self._backend is not None

    @property
    def detector_type(self) -> str:
        return "window"

    def detect(
        self,
        roi_image: np.ndarray,
        roi_name: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[PerceptionEvent]:
        """检测窗口状态变化（忽略 roi_image，直接查系统 API）"""
        if not self.is_available():
            return []

        window_title, app_name = self._get_active_window()

        if window_title is None:
            return []

        # 窗口和应用均无变化时跳过，避免重复触发事件
        if window_title == self._last_window and app_name == self._last_app:
            return []

        event = PerceptionEvent(
            event_type=PerceptionEventType.SCREEN_WINDOW,
            source="window",
            importance=0.5,
            payload={
                "window_title": window_title or "",
                "app_name": app_name or "",
                "prev_window": self._last_window or "",
                "prev_app": self._last_app or "",
            },
        )

        self._last_window = window_title
        self._last_app = app_name

        logger.debug(f"窗口切换: {app_name} — {window_title[:50]}")
        return [event]

    def _get_active_window(self):
        """获取当前活跃窗口标题和应用名

        根据后端类型调用不同的系统 API。
        """
        try:
            if self._backend == "win32":
                return self._get_window_win32()
            elif self._backend == "appkit":
                return self._get_window_appkit()
        except Exception as e:
            logger.debug(f"获取窗口信息失败: {e}")
        return None, None

    @staticmethod
    def _get_window_win32():
        """Windows 窗口信息获取（pywin32 + psutil）"""
        import win32gui
        import win32process
        import psutil

        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            proc = psutil.Process(pid)
            app = proc.name()
        except Exception:
            app = ""
        return title, app

    @staticmethod
    def _get_window_appkit():
        """macOS 窗口信息获取（NSWorkspace + Quartz）

        使用 kCGWindowListOptionOnScreenOnly 获取屏幕上所有窗口，
        按 PID 和窗口层级过滤出前端应用的顶层窗口。
        """
        from AppKit import NSWorkspace
        workspace = NSWorkspace.sharedWorkspace()
        app = workspace.frontmostApplication()
        app_name = app.localizedName() if app else ""

        app_pid = app.processIdentifier() if app else None

        window_title = ""
        try:
            from Quartz import (
                CGWindowListCopyWindowInfo,
                kCGNullWindowID,
                kCGWindowListOptionOnScreenOnly,
            )
            all_windows = CGWindowListCopyWindowInfo(
                kCGWindowListOptionOnScreenOnly, kCGNullWindowID
            )
            if all_windows and app_pid is not None:
                # 过滤出属于前端应用且层级为 0（非 dock/菜单栏）的窗口
                front_windows = [
                    w for w in all_windows
                    if w.get("kCGWindowOwnerPID") == app_pid
                       and w.get("kCGWindowLayer", 0) == 0
                ]
                # 按层级排序取最上层窗口
                front_windows.sort(
                    key=lambda w: w.get("kCGWindowLayer", 0), reverse=True
                )
                if front_windows:
                    window_title = front_windows[0].get("kCGWindowName", "") or ""
        except Exception:
            pass

        return window_title or app_name, app_name

    def reset(self) -> None:
        """重置窗口状态缓存"""
        self._last_window = None
        self._last_app = None
