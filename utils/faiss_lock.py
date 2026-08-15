"""
FAISS 索引跨实例文件锁

多记忆库 EventStore 共用同一份
data/events_faiss.index + data/events_id_map.json，且都在同一进程内运行。
本模块提供「进程内可重入 RLock + 跨进程 flock」的文件锁，防止两套
EventStore 并发写同一索引导致文件损坏或 id_map 与向量数不一致。
"""
import contextlib
import os
import threading

# 解释器关闭/GC 阶段 builtins 可能已被清空（EventStore.__del__ 落盘会走这里），
# open 等内建查找会抛 NameError。在模块加载时绑定到模块级全局，避免解析 builtins。
_open = open

try:
    import fcntl
except ImportError:  # Windows 等无 fcntl 平台退化为仅线程锁
    fcntl = None  # type: ignore[assignment]


class _PathLock:
    """单个锁文件路径的锁：进程内可重入，跨进程 flock 互斥。

    锁文件与被保护的数据文件分离：在数据路径后追加 ".lock" 后缀，
    绝不以可写模式打开数据文件本身（避免截断索引/映射文件）。
    """

    def __init__(self, path: str):
        # 独立锁文件路径：数据文件 events_faiss.index → 锁文件 events_faiss.index.lock
        self.lock_path = path + ".lock"
        self._rlock = threading.RLock()
        self._depth = 0
        self._fd = None

    def acquire(self):
        with self._rlock:
            if self._depth == 0:
                os.makedirs(os.path.dirname(self.lock_path) or ".", exist_ok=True)
                # 追加模式打开（不截断）；锁文件内容无关紧要，仅用于 flock
                fd = _open(self.lock_path, "a+")
                if fcntl is not None:
                    try:
                        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
                    except Exception:
                        fd.close()
                        raise
                self._fd = fd
            self._depth += 1

    def release(self):
        with self._rlock:
            self._depth -= 1
            if self._depth == 0 and self._fd is not None:
                try:
                    if fcntl is not None:
                        fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
                finally:
                    self._fd.close()
                    self._fd = None


_registry: dict = {}
_registry_lock = threading.Lock()


def _get_path_lock(lock_path: str) -> _PathLock:
    key = os.path.abspath(lock_path)
    with _registry_lock:
        if key not in _registry:
            _registry[key] = _PathLock(key)
        return _registry[key]


@contextlib.contextmanager
def faiss_file_lock(lock_path: str):
    """对指定锁文件加互斥锁（进程内可重入 + 跨进程 flock）。

    用法::

        with faiss_file_lock("/abs/path/events_faiss.index"):
            ...读写 FAISS 索引 / id_map...
    """
    pl = _get_path_lock(lock_path)
    pl.acquire()
    try:
        yield
    finally:
        pl.release()
