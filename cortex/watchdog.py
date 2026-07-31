"""防残留 — 父进程存活监控

背景: `cortex` 用 subprocess 拉起 uvicorn 后端 / 前端服务等子进程。
若 cortex 被强杀（kill -9 / 崩溃），SIGINT/SIGTERM 处理器不会执行，
子进程会变成孤儿持续占用内存和端口。

原理: cortex 启动子进程时注入 CORTEX_PARENT_PID 环境变量（指向自己）。
各子进程导入本模块后，启动一个守护线程定期用 os.kill(pid, 0) 探测
父进程是否存活；父进程消失则 os._exit(0) 自杀。

特性: 未设置 CORTEX_PARENT_PID（手动独立运行后端/前端）时自动禁用，
不影响正常独立部署。
"""
import os
import threading
import time

_ENV_KEY = "CORTEX_PARENT_PID"
_DEFAULT_INTERVAL = 2.0

_started = False
_logger = None


def _log(msg: str) -> None:
    global _logger
    if _logger is None:
        try:
            from utils.logger import setup_logger
            _logger = setup_logger("orphan_watchdog")
        except Exception:
            _logger = False
    if _logger:
        try:
            _logger.info(msg)
        except Exception:
            pass


def _parent_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def enable(interval: float = _DEFAULT_INTERVAL) -> bool:
    """按 CORTEX_PARENT_PID 环境变量启用父进程监控。已启用则直接返回。"""
    global _started
    if _started:
        return True
    raw = os.environ.get(_ENV_KEY, "").strip()
    if not raw:
        return False
    try:
        ppid = int(raw)
    except (TypeError, ValueError):
        return False
    if ppid <= 1 or ppid == os.getpid():
        return False

    def _watch() -> None:
        while True:
            time.sleep(interval)
            if not _parent_alive(ppid):
                _log(f"父进程 {ppid} 已退出，自动退出以避免残留")
                os._exit(0)

    threading.Thread(target=_watch, daemon=True, name="orphan-watchdog").start()
    _started = True
    return True
