"""降级捕获后端 — PIL.ImageGrab

跨平台兼容，性能较低，作为最后兜底。
"""
import threading
import time
from typing import Optional, Tuple

import numpy as np

from modules.perception.pipeline.capture import CaptureBackend
from utils.logger import setup_logger

logger = setup_logger("perception_capture_fallback")


class PILBackend(CaptureBackend):
    """PIL.ImageGrab 降级捕获后端"""

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._latest_frame: Optional[np.ndarray] = None
        self._frame_lock = threading.Lock()
        self._fps = 2  # 降级模式用低帧率
        self._roi: Optional[Tuple[int, int, int, int]] = None
        self._frame_count = 0

    def is_available(self) -> bool:
        try:
            from PIL import ImageGrab  # noqa: F401
            return True
        except ImportError:
            return False

    @property
    def platform_name(self) -> str:
        return "fallback"

    def start(self, fps: int = 2, roi: Optional[Tuple[int, int, int, int]] = None) -> None:
        if self._running:
            return

        self._fps = min(fps, 5)  # 降级模式限制最大 5 FPS
        self._roi = roi
        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="pil-capture"
        )
        self._thread.start()
        logger.info(f"PIL 降级捕获启动: fps={self._fps}")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("PIL 降级捕获已停止")

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
        interval = 1.0 / self._fps

        while self._running:
            try:
                from PIL import ImageGrab

                if self._roi:
                    x, y, w, h = self._roi
                    bbox = (x, y, x + w, y + h)
                    img = ImageGrab.grab(bbox=bbox)
                else:
                    img = ImageGrab.grab()

                frame = np.array(img)
                # PIL 返回 RGB，转换为 BGR
                if len(frame.shape) == 3 and frame.shape[2] == 3:
                    frame = frame[:, :, ::-1].copy()

                with self._frame_lock:
                    self._latest_frame = frame
                self._frame_count += 1

                time.sleep(interval)
            except Exception as e:
                logger.warning(f"PIL 捕获异常: {e}")
                time.sleep(0.5)

    def get_stats(self) -> dict:
        return {
            "backend": "pil",
            "running": self._running,
            "fps": self._fps,
            "frame_count": self._frame_count,
            "roi": self._roi,
        }
