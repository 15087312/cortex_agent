"""macOS 屏幕捕获 — MSS + Quartz

MSS: 跨平台截图库，稳定可靠。
Quartz: macOS 原生 API，功耗更低（备选）。
"""
import threading
import time
from typing import Optional, Tuple

import numpy as np

from modules.perception.pipeline.capture import CaptureBackend
from utils.logger import setup_logger

logger = setup_logger("perception_capture_macos")


class MSSBackend(CaptureBackend):
    """MSS 屏幕捕获后端 (macOS / 跨平台)"""

    def __init__(self):
        self._sct = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._latest_frame: Optional[np.ndarray] = None
        self._frame_lock = threading.Lock()
        self._fps = 5
        self._roi: Optional[Tuple[int, int, int, int]] = None
        self._frame_count = 0

    def is_available(self) -> bool:
        try:
            import mss  # noqa: F401
            return True
        except ImportError:
            return False

    @property
    def platform_name(self) -> str:
        return "macos"

    def start(self, fps: int = 5, roi: Optional[Tuple[int, int, int, int]] = None) -> None:
        if self._running:
            return

        try:
            import mss
            self._sct = mss.mss()
            self._fps = fps
            self._roi = roi
            self._running = True
            self._thread = threading.Thread(
                target=self._capture_loop, daemon=True, name="mss-capture"
            )
            self._thread.start()
            logger.info(f"MSS 启动: fps={fps} roi={roi}")
        except Exception as e:
            logger.error(f"MSS 启动失败: {e}")
            self._running = False

    def stop(self) -> None:
        self._running = False
        if self._sct:
            try:
                self._sct.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3)
        self._sct = None
        logger.info("MSS 已停止")

    def get_frame(self) -> Optional[np.ndarray]:
        with self._frame_lock:
            return self._latest_frame

    @property
    def resolution(self) -> Optional[Tuple[int, int]]:
        if self._latest_frame is not None:
            h, w = self._latest_frame.shape[:2]
            return (w, h)
        return None

    def _capture_loop(self):
        """后台捕获循环"""
        interval = 1.0 / self._fps

        while self._running:
            try:
                if self._roi:
                    x, y, w, h = self._roi
                    monitor = {"left": x, "top": y, "width": w, "height": h}
                else:
                    monitor = self._sct.monitors[1]  # 主显示器（0=合并区域）

                screenshot = self._sct.grab(monitor)
                # MSS 返回 BGRA，转换为 BGR
                frame = np.array(screenshot)[:, :, :3]

                with self._frame_lock:
                    self._latest_frame = frame
                self._frame_count += 1

                time.sleep(interval)
            except Exception as e:
                logger.warning(f"MSS 捕获异常: {e}")
                time.sleep(0.1)

    def get_stats(self) -> dict:
        return {
            "backend": "mss",
            "running": self._running,
            "fps": self._fps,
            "frame_count": self._frame_count,
            "roi": self._roi,
        }
