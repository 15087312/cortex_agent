"""屏幕捕获工具 — 跨平台统一接口

进程启动时检测一次屏幕录制权限，结果写入 SCREENSHOT_ENABLED。
所有截图调用点直接读这个标志，不需要重复检测。
"""
import base64
import io
import os
import sys
from typing import Optional

from utils.logger import setup_logger

logger = setup_logger("screen_capture")

# ── 进程级全局标志，init_screen_permission() 设置一次 ──
SCREENSHOT_ENABLED: bool = True


def init_screen_permission():
    """进程启动时调用一次，检测屏幕录制权限并设置 SCREENSHOT_ENABLED

    用 CGPreflightScreenCaptureAccess 做纯检查（不弹窗）——直接执行
    screencapture 命令会在每次启动时触发系统的「屏幕录制权限」请求，
    用户即使已授权也可能因 TCC 对命令行工具授权不持久而反复弹窗。
    """
    global SCREENSHOT_ENABLED
    if sys.platform != "darwin":
        SCREENSHOT_ENABLED = True
        return

    try:
        # macOS 10.15+：只检查是否已授权，不触发权限请求
        from Quartz import CGPreflightScreenCaptureAccess
        SCREENSHOT_ENABLED = bool(CGPreflightScreenCaptureAccess())
    except Exception:
        # 无 Quartz（未装 pyobjc）时退回一次性 screencapture 检测
        try:
            import subprocess
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                tmp = f.name
            result = subprocess.run(
                ["screencapture", "-x", "-R", "0,0,1,1", tmp],
                timeout=3, capture_output=True,
            )
            ok = result.returncode == 0 and os.path.getsize(tmp) > 0
            try:
                os.unlink(tmp)
            except OSError:
                pass
            SCREENSHOT_ENABLED = ok
        except Exception:
            SCREENSHOT_ENABLED = False

    if SCREENSHOT_ENABLED:
        logger.info("[屏幕权限] 已授予")
    else:
        logger.warning("[屏幕权限] 未授予，截图功能不可用")
        logger.warning("[屏幕权限] 请在 系统设置 → 隐私与安全性 → 屏幕录制 中授权")


def capture_screen(max_width: int = 1280) -> Optional[str]:
    """截取屏幕，返回 base64 编码的 PNG

    mss 已移除：其 CGDisplayStream 在本机会挂起 ~30s。
    优先 PIL ImageGrab（进程内、快），darwin 上回退 screencapture 命令。
    """
    if not SCREENSHOT_ENABLED:
        return None

    img = _try_imagegrab()
    if img is None and sys.platform == "darwin":
        img = _try_screencapture()
    if img is None:
        return None

    w, h = img.size
    if w > max_width:
        ratio = max_width / w
        img = img.resize((max_width, int(h * ratio)))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


capture_screen_base64 = capture_screen


def _try_imagegrab():
    try:
        from PIL import ImageGrab
        return ImageGrab.grab()
    except Exception:
        return None


def _try_screencapture():
    try:
        import subprocess
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name
        subprocess.run(["screencapture", "-x", tmp_path], timeout=5, check=True)
        from PIL import Image
        img = Image.open(tmp_path)
        os.unlink(tmp_path)
        return img
    except Exception:
        return None
