"""存在心跳 — 周期性扫描差异源，驱动持续感知

以固定频率（默认 1Hz）调用 detector.scan()，
确保差异检测器持续运行。
"""
import time
import threading
from typing import Optional

from utils.logger import setup_logger

logger = setup_logger("existential_heartbeat")


class ExistentialHeartbeat:
    """存在心跳

    后台守护线程，以固定频率触发差异扫描。
    用于维持持续感知（Stage 1: continuous perception）。
    """

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._interval: float = 1.0
        self._beat_count: int = 0
        self._started_at: float = 0.0

    def start(self, detector=None) -> None:
        # @link:block Heartbeat_Guard = Guard
        """启动心跳线程

        如果不传 detector，使用全局单例。
        """
        if self._running:
            return

        if detector is None:
            from modules.perception.difference.detector import get_detector
            detector = get_detector()

        self._detector = detector
        self._running = True
        self._stop_event.clear()
        self._started_at = time.time()
        self._beat_count = 0

        self._thread = threading.Thread(target=self._loop, daemon=True, name="existential-heartbeat")
        self._thread.start()
        logger.info("✓ 存在心跳已启动 (Stage 1: continuous perception @ 1Hz)")

    def stop(self) -> None:
        if not self._running:
            return

        self._running = False
        self._stop_event.set()

        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

        logger.info("存在心跳已停止 (beat_count=%d, uptime=%.1fs)",
                     self._beat_count, time.time() - self._started_at)

    def _loop(self) -> None:
        """心跳主循环 — 固定间隔调用 detector.scan()"""
        while self._running and not self._stop_event.is_set():
            try:
                self._detector.scan()
                self._beat_count += 1
            except Exception as e:
                logger.error(f"心跳扫描异常: {type(e).__name__}: {e}")
            self._stop_event.wait(self._interval)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def beat_count(self) -> int:
        return self._beat_count

    @property
    def uptime(self) -> float:
        if self._started_at == 0.0:
            return 0.0
        return time.time() - self._started_at

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "beat_count": self._beat_count,
            "uptime_seconds": round(self.uptime, 1),
            "interval": self._interval,
        }


_heartbeat_instance = None
_heartbeat_lock = threading.Lock()


def get_heartbeat() -> ExistentialHeartbeat:
    """端口优先取存在心跳：接管 ``heartbeat`` 端口 = 换掉全部调用点。

    取不到端口才回退内置单例（脚本、没跑过 bootstrap 的入口）。bootstrap 注册
    的 provider 必须是 ``_default_heartbeat`` 而不是本函数，否则端口→本函数→端口
    一调用就递归。
    """
    from infra.tool_manager.service_registry import get_capability

    factory = get_capability("heartbeat")
    if factory is None:
        return _default_heartbeat()
    return factory()


def _default_heartbeat() -> ExistentialHeartbeat:
    """获取存在心跳全局单例（线程安全）"""
    global _heartbeat_instance
    if _heartbeat_instance is None:
        with _heartbeat_lock:
            if _heartbeat_instance is None:
                _heartbeat_instance = ExistentialHeartbeat()
    return _heartbeat_instance
