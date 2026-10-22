"""窗口状态检测器

Windows: pywin32 (GetForegroundWindow)
macOS: pyobjc (NSWorkspace + Accessibility API)

检测窗口切换、活跃应用变化。
"""
import platform
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
        self._detect_count = 0

    def _init_backend(self):
        """根据平台初始化窗口检测后端

        Windows: pywin32 (win32gui.GetForegroundWindow)
        macOS: Quartz (CGWindowListCopyWindowInfo, 线程安全)
        """
        if self._platform == "Windows":
            try:
                import win32gui  # noqa: F401
                self._backend = "win32"
            except ImportError:
                logger.debug("pywin32 不可用")
        elif self._platform == "Darwin":
            try:
                from Quartz import CGWindowListCopyWindowInfo  # noqa: F401
                self._backend = "quartz"
            except ImportError:
                logger.debug("Quartz 不可用")

    def is_available(self) -> bool:
        if self._backend is None:
            self._init_backend()
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
        if self._backend is None:
            self._init_backend()
        if not self.is_available():
            return []

        window_title, app_name = self._get_active_window()

        if window_title is None:
            logger.warning("窗口检测: _get_active_window 返回 None（系统API可能失败）")
            return []

        self._detect_count += 1

        # 窗口和应用均无变化时跳过，避免重复触发事件
        if window_title == self._last_window and app_name == self._last_app:
            return []

        logger.info(f"窗口切换: {app_name} — {window_title[:80]}")

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

        return [event]

    def _get_active_window(self):
        """获取当前活跃窗口标题和应用名

        根据后端类型调用不同的系统 API。
        """
        try:
            if self._backend == "win32":
                return self._get_window_win32()
            elif self._backend == "quartz":
                return self._get_window_quartz()
        except Exception as e:
            logger.warning(f"获取窗口信息失败: {type(e).__name__}: {e}")
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
    def _get_window_quartz():
        """macOS 窗口信息获取（纯 Quartz，线程安全）

        CGWindowListCopyWindowInfo 是 C API，可在任意线程调用。
        返回的窗口列表按前后顺序排列，第一个 layer==0 的窗口即前端窗口。
        """
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGNullWindowID,
            kCGWindowListOptionOnScreenOnly,
        )

        all_windows = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly, kCGNullWindowID
        )
        if not all_windows:
            logger.warning("CGWindowListCopyWindowInfo 返回空")
            return None, None

        for w in all_windows:
            if w.get("kCGWindowLayer", 0) == 0:
                app_name = w.get("kCGWindowOwnerName", "") or ""
                window_title = w.get("kCGWindowName", "") or ""
                return window_title or app_name, app_name

        logger.warning("未找到 layer==0 的窗口")
        return None, None

    def reset(self) -> None:
        """重置窗口状态缓存"""
        self._last_window = None
        self._last_app = None
