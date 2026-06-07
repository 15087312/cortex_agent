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

        # 检查是否有变化
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
        """获取当前活跃窗口标题和应用名"""
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
        from AppKit import NSWorkspace
        workspace = NSWorkspace.sharedWorkspace()
        app = workspace.frontmostApplication()
        app_name = app.localizedName() if app else ""

        # 尝试用 Quartz 获取窗口标题
        window_title = ""
        try:
            from Quartz import (
                CGWindowListCopyWindowInfo,
                kCGNullWindowID,
                kCGWindowListOptionOnScreenOnly,
            )
            info = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID)
            if info:
                window_title = info[0].get("kCGWindowName", "") or ""
        except Exception:
            pass

        return window_title or app_name, app_name

    def reset(self) -> None:
        self._last_window = None
        self._last_app = None
