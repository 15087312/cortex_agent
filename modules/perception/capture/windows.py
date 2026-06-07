"""Windows 屏幕捕获 — DXcam

DXcam 使用 DirectX Desktop Duplication API，GPU 抓屏，超低延迟。
仅 Windows 可用。
"""
import threading
import time
from typing import Optional, Tuple

import numpy as np

from modules.perception.pipeline.capture import CaptureBackend
from utils.logger import setup_logger

logger = setup_logger("perception_capture_windows")


class DXcamBackend(CaptureBackend):
    """DXcam 屏幕捕获后端 (Windows Only)"""

    def __init__(self):
        self._camera = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._latest_frame: Optional[np.ndarray] = None
        self._frame_lock = threading.Lock()
        self._fps = 5
        self._roi: Optional[Tuple[int, int, int, int]] = None
        self._frame_count = 0

    def is_available(self) -> bool:
        try:
            import dxcam  # noqa: F401
            return True
        except ImportError:
            return False

    @property
    def platform_name(self) -> str:
        return "windows"

    def start(self, fps: int = 5, roi: Optional[Tuple[int, int, int, int]] = None) -> None:
        if self._running:
            return

        try:
            import dxcam
            self._camera = dxcam.create(output_color="BGR")
            if roi:
                self._roi = roi
                # DXcam ROI: (left, top, right, bottom)
                self._camera.start(target_fps=fps, roi=(
                    roi[0], roi[1], roi[0] + roi[2], roi[1] + roi[3]
                ))
            else:
                self._camera.start(target_fps=fps)

            self._fps = fps
            self._running = True
            self._thread = threading.Thread(
                target=self._capture_loop, daemon=True, name="dxcam-capture"
            )
            self._thread.start()
            logger.info(f"DXcam 启动: fps={fps} roi={roi}")
        except Exception as e:
            logger.error(f"DXcam 启动失败: {e}")
            self._running = False

    def stop(self) -> None:
        self._running = False
        if self._camera:
            try:
                self._camera.stop()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3)
        self._camera = None
        logger.info("DXcam 已停止")

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
        while self._running:
            try:
                frame = self._camera.get_latest_frame()
                if frame is not None:
                    with self._frame_lock:
                        self._latest_frame = frame
                    self._frame_count += 1
                else:
                    time.sleep(0.01)
            except Exception as e:
                logger.warning(f"DXcam 捕获异常: {e}")
                time.sleep(0.1)

    def get_stats(self) -> dict:
        return {
            "backend": "dxcam",
            "running": self._running,
            "fps": self._fps,
            "frame_count": self._frame_count,
            "roi": self._roi,
        }
