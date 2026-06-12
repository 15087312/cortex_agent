"""
行为偏差差异源

检测内容：
- 事件频率偏离基线 3x (EMA, alpha=0.1)
- 异常行为模式
"""
import time
import threading
from typing import List, Dict, Optional

from modules.difference_detector.sources.base import DifferenceSource
from modules.difference_detector.models import Difference
from utils.logger import setup_logger

logger = setup_logger("behavioral_source")

BEHAVIORAL_TTL = 15 * 60  # 15 分钟
EMA_ALPHA = 0.1
DEVIATION_THRESHOLD = 3.0   # 3 倍标准差视为异常


class BehavioralDifferenceSource(DifferenceSource):
    """行为偏差差异源 — 检测事件频率异常"""

    def __init__(self, gcm_pool=None):
        super().__init__()
        self._gcm_pool = gcm_pool
        self._ema_rate: float = 0.0        # 事件速率 EMA (events/sec)
        self._last_event_count: int = 0
        self._last_check: float = time.time()
        self._lock = threading.Lock()

    @property
    def source_type(self) -> str:
        return "behavioral"

    def _get_gcm_pool(self):
        if self._gcm_pool is not None:
            return self._gcm_pool
        from modules.thinking.context import gcm_pool
        return gcm_pool

    def detect(self) -> List[Difference]:
        differences = []
        gcm = self._get_gcm_pool()
        now = time.time()

        with self._lock:
            try:
                current_count = gcm.event_count() if hasattr(gcm, 'event_count') else 0
            except Exception as e:
                logger.warning(f"获取事件计数失败: {e}")
                current_count = 0

            elapsed = now - self._last_check
            if elapsed <= 0 or elapsed > 300:
                # 首次调用或间隔太长，重置基线
                self._last_event_count = current_count
                self._last_check = now
                return differences

            delta = current_count - self._last_event_count
            if delta < 0:
                delta = 0  # 事件可能被裁剪，不倒退

            current_rate = delta / elapsed

            if self._ema_rate == 0.0:
                self._ema_rate = current_rate
            else:
                self._ema_rate = (1 - EMA_ALPHA) * self._ema_rate + EMA_ALPHA * current_rate

            # 检测偏离：当前速率 vs EMA 基线
            if self._ema_rate > 0 and current_rate > self._ema_rate * DEVIATION_THRESHOLD:
                ratio = current_rate / self._ema_rate
                differences.append(Difference(
                    source_type="behavioral",
                    category="event_rate_spike",
                    intensity=min(40.0 + (ratio - DEVIATION_THRESHOLD) * 10, 90.0),
                    ttl=BEHAVIORAL_TTL,
                    payload={
                        "current_rate": round(current_rate, 4),
                        "ema_baseline": round(self._ema_rate, 4),
                        "ratio": round(ratio, 2),
                        "delta_events": delta,
                        "elapsed_seconds": round(elapsed, 1),
                    },
                ))

            # 事件速率骤降 (可能系统停滞)
            if self._ema_rate > 0.01 and current_rate < self._ema_rate * 0.1:
                differences.append(Difference(
                    source_type="behavioral",
                    category="event_rate_drop",
                    intensity=35.0,
                    ttl=BEHAVIORAL_TTL,
                    payload={
                        "current_rate": round(current_rate, 4),
                        "ema_baseline": round(self._ema_rate, 4),
                        "elapsed_seconds": round(elapsed, 1),
                    },
                ))

            self._last_event_count = current_count
            self._last_check = now

        return differences
