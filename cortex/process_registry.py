"""后端子进程登记 — 后端拉起的子进程统一登记，shutdown 时收编。

caffeinate（utils.power）等常驻子进程此前只有模块内句柄、退出时从不终止，
后端重启/退出后会残留。这里提供登记表 + shutdown 收编，与 watchdog 的
「卫星进程绑定后端」互补：登记表覆盖后端直接拉起的子进程，watchdog 覆盖
外部拉起的卫星进程。
"""
import subprocess
import threading
from typing import List, Optional, Tuple

_lock = threading.Lock()
_children: List[Tuple[str, subprocess.Popen]] = []


def register(name: str, proc: Optional[subprocess.Popen]) -> None:
    """登记一个后端拉起的子进程（重复名字覆盖旧登记）"""
    if proc is None:
        return
    with _lock:
        _children[:] = [(n, p) for n, p in _children if n != name]
        _children.append((name, proc))


def unregister(name: str) -> None:
    """取消登记（子进程被正常终止时）"""
    with _lock:
        _children[:] = [(n, p) for n, p in _children if n != name]


def snapshot() -> List[Tuple[str, subprocess.Popen]]:
    with _lock:
        return list(_children)


def shutdown_all_children(timeout: float = 3.0) -> None:
    """终止所有登记的子进程（先 SIGTERM，超时 SIGKILL）。幂等。"""
    with _lock:
        children = list(_children)
        _children.clear()
    for name, proc in children:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
