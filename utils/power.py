"""防休眠（macOS caffeinate）——让 prevent_sleep 配置真实生效。

后端用 caffeinate -dimsu 保持系统不睡眠（比前端 Web Wake Lock 在 Qt
WebEngine 里可靠）。启用时启动常驻子进程，关闭时终止。

与进程登记的分工
    本模块只负责「起停 caffeinate」，**不负责收编**。子进程句柄经
    `get_process()` 暴露，由装配层（api/main.py）登记进进程登记表。
    这样做是为了让 utils 不 import infra —— 否则 utils ⇄ infra 会构成循环
    （infra 各处都依赖 utils.logger）。登记属生命周期关切，本就该由装配层持有。
"""
import subprocess
import sys
import threading
from typing import Optional

from utils.logger import setup_logger

logger = setup_logger("power")

_proc = None
_lock = threading.Lock()


def get_process() -> Optional[subprocess.Popen]:
    """返回 caffeinate 子进程句柄（供装配层登记收编；未启用时为 None）"""
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
