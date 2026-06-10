"""屏幕捕获工具 — 跨平台统一接口

提供 capture_screen() 函数，返回 base64 编码的 PNG。
三级降级：mss → PIL.ImageGrab → screencapture(macOS)
"""
import base64
import io
import sys
from typing import Optional

from utils.logger import setup_logger

logger = setup_logger("screen_capture")


def capture_screen(max_width: int = 1280) -> Optional[str]:
    """截取屏幕，返回 base64 编码的 PNG

    Args:
        max_width: 最大宽度，超过则等比缩放

    Returns:
        base64 字符串，失败返回 None
    """
    img = _try_mss()
    if img is None:
        img = _try_imagegrab()
    if img is None and sys.platform == "darwin":
        img = _try_screencapture()
    if img is None:
        logger.warning("无可用的屏幕捕获方式")
        return None

    # 缩放
    w, h = img.size
    if w > max_width:
        ratio = max_width / w
        img = img.resize((max_width, int(h * ratio)))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def _try_mss():
    """mss 捕获"""
    try:
        import mss
        with mss.MSS() as sct:
            monitor = sct.monitors[1]
            screenshot = sct.grab(monitor)
            from PIL import Image
            return Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
    except Exception:
        return None


def _try_imagegrab():
    """PIL.ImageGrab 捕获"""
    try:
        from PIL import ImageGrab
        return ImageGrab.grab()
    except Exception:
        return None


def _try_screencapture():
    """macOS screencapture CLI"""
    try:
        import subprocess
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name
        subprocess.run(["screencapture", "-x", tmp_path], timeout=5, check=True)
        from PIL import Image
        img = Image.open(tmp_path)
        os.unlink(tmp_path)
        return img
    except Exception:
        return None
