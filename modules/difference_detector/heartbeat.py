"""
存在心跳 — 1Hz daemon 线程驱动持续扫描

完全复用 ResourceManager 的后台 daemon thread 模式，改进点：
- 使用 threading.Event 替代 time.sleep()，支持可中断等待
- 1Hz 频率 = 每秒扫描一次
"""
import time
import threading
from typing import Optional

from utils.logger import setup_logger

logger = setup_logger("existential_heartbeat")


class ExistentialHeartbeat:
    """存在心跳 — 持续感知的时钟源"""

    def __init__(self):

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._interval: float = 1.0  # 1Hz
        self._beat_count: int = 0
        self._started_at: float = 0.0

    def start(self, detector=None) -> None:
        """启动心跳

        Args:
            detector: DifferenceDetector 实例，若为 None 则延迟导入
        """
        if self._running:
            return

        if detector is None:
            from modules.difference_detector.detector import get_detector
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
        """停止心跳"""
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
        """主循环 — 1Hz 扫描"""
        while self._running and not self._stop_event.is_set():
            try:
                self._detector.scan()
                self._beat_count += 1
            except Exception as e:
                logger.error(f"心跳扫描异常: {type(e).__name__}: {e}")

            # 可中断等待 (每秒一次)
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


# Thread-safe lazy factory (consolidated from __init__.py)
_heartbeat_instance = None
_heartbeat_lock = threading.Lock()

def get_heartbeat() -> ExistentialHeartbeat:
    """Get or create ExistentialHeartbeat instance (lazy factory, thread-safe)"""
    global _heartbeat_instance
    if _heartbeat_instance is None:
        with _heartbeat_lock:
            if _heartbeat_instance is None:
                _heartbeat_instance = ExistentialHeartbeat()
    return _heartbeat_instance
