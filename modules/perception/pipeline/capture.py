"""屏幕捕获后端抽象

定义 CaptureBackend ABC 和平台工厂函数。
各平台实现见 capture/windows.py, capture/macos.py, capture/fallback.py。
"""
import platform
from abc import ABC, abstractmethod
from typing import Optional, Tuple

import numpy as np

from utils.logger import setup_logger

logger = setup_logger("perception_capture")


class CaptureBackend(ABC):
    """屏幕捕获后端抽象

    实现要求:
    - start() 启动后台捕获线程
    - stop() 停止并释放资源
    - get_frame() 返回最新帧（BGR numpy array）或 None
    - 线程安全
    """

    @abstractmethod
    def start(self, fps: int = 5, roi: Optional[Tuple[int, int, int, int]] = None) -> None:
        """启动捕获

        Args:
            fps: 目标帧率
            roi: 可选裁剪区域 (x, y, w, h)，None 表示全屏
        """

    @abstractmethod
    def stop(self) -> None:
        """停止捕获并释放资源"""

    @abstractmethod
    def get_frame(self) -> Optional[np.ndarray]:
        """获取最新帧

        Returns:
            BGR numpy array (h, w, 3) 或 None（无新帧）
        """

    @abstractmethod
    def is_available(self) -> bool:
        """后端是否可用（依赖是否安装等）"""

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """平台标识: "windows" / "macos" / "linux" """

    @property
    def resolution(self) -> Optional[Tuple[int, int]]:
        """当前捕获分辨率 (w, h)，未知返回 None"""
        return None


def create_capture_backend() -> CaptureBackend:
    """工厂函数 — 根据平台自动选择最佳后端

    优先级:
    1. Windows: DXcam (GPU 抓屏)
    2. macOS: MSS (跨平台稳定)
    3. 降级: PIL.ImageGrab (跨平台，慢)
    """
    system = platform.system()

    if system == "Windows":
        try:
            from modules.perception.capture.windows import DXcamBackend
            backend = DXcamBackend()
            if backend.is_available():
                logger.info("捕获后端: DXcam (Windows GPU)")
                return backend
        except Exception as e:
            logger.debug(f"DXcam 不可用: {e}")

    elif system == "Darwin":
        try:
            from modules.perception.capture.macos import MSSBackend
            backend = MSSBackend()
            if backend.is_available():
                logger.info("捕获后端: MSS (macOS)")
                return backend
        except Exception as e:
            logger.debug(f"MSS 不可用: {e}")

    # 降级
    try:
        from modules.perception.capture.fallback import PILBackend
        backend = PILBackend()
        if backend.is_available():
            logger.info("捕获后端: PIL.ImageGrab (降级)")
            return backend
    except Exception as e:
        logger.debug(f"PIL 降级也不可用: {e}")

    logger.warning("无可用捕获后端，返回 NullBackend")
    return _NullBackend()


class _NullBackend(CaptureBackend):
    """空后端 — 无可用捕获时的兜底"""

    def start(self, fps: int = 5, roi: Optional[Tuple[int, int, int, int]] = None) -> None:
        pass

    def stop(self) -> None:
        pass

    def get_frame(self) -> Optional[np.ndarray]:
        return None

    def is_available(self) -> bool:
        return False  # 无捕获能力

    @property
    def platform_name(self) -> str:
        return "null"
