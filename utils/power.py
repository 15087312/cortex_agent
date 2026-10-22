"""防休眠（macOS caffeinate）——让 prevent_sleep 配置真实生效。

后端用 caffeinate -dimsu 保持系统不睡眠（比前端 Web Wake Lock 在 Qt
WebEngine 里可靠）。启用时启动常驻子进程，关闭时终止。
"""
import subprocess
import sys
import threading

from utils.logger import setup_logger

logger = setup_logger("power")

_proc = None
_lock = threading.Lock()


def get_process() -> subprocess.Popen:
    """返回 caffeinate 子进程句柄（供后端 shutdown 收编；无则 None）"""
    return _proc

# @link:block Power_Apply = Guard · Tool
def apply(enabled: bool) -> bool:
    """应用防休眠设置。返回是否成功。"""
    global _proc
    if sys.platform != "darwin":
        logger.debug("[防休眠] 仅 macOS 支持")
        return False
    with _lock:
        if enabled and _proc is None:
            try:
                _proc = subprocess.Popen(
                    ["caffeinate", "-dimsu"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                try:
                    from cortex.process_registry import register
                    register("caffeinate", _proc)
                except Exception as _swallowed_err:
                    logger.debug(f"[apply] ignored Exception: {_swallowed_err}")
                logger.info("[防休眠] 已启用 (caffeinate)")
            except Exception as e:
                logger.warning(f"[防休眠] 启动失败: {e}")
                _proc = None
                return False
        elif not enabled and _proc is not None:
            try:
                _proc.terminate()
                try:
                    _proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _proc.kill()
            except Exception as _swallowed_err:
                logger.debug(f"[apply] ignored Exception: {_swallowed_err}")
            _proc = None
            logger.info("[防休眠] 已关闭")
        return True


def is_active() -> bool:
    return _proc is not None and _proc.poll() is None
