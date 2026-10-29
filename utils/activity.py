"""活动感知计时（ActivityClock）— “AI 有反应就重新计时”。

把原来“整轮固定 N 秒超时”改成“静默 N 秒才算超时”：

- AI 每产出一次（流式 token / 模型响应 / 工具调用 / 控制指令）调用 ``touch()``，
  静默倒计时归零；
- 工具执行期间调用 ``pause()`` / ``resume()``，工具耗时**不计入** AI 的静默时间
  （工具自身有独立的 ``TOOL_CALL_TIMEOUT``）；
- 等待用户审批时 ``Suspension`` 生效，静默时间同样冻结。

用法::

    clock = get_activity_clock(session_id)
    clock.touch("stream")
    await pausable_wait_for(coro, timeout=300, activity=clock)
"""
import threading
import time
from typing import Dict

from utils.logger import setup_logger
from utils.suspension import Suspension

logger = setup_logger(__name__)


class ActivityClock:
    """线程安全的活动时钟：``idle()`` 返回距上次活动的“有效静默秒数”。

    - ``touch()``: 有进展，重新计时
    - ``pause()`` / ``resume()``: 冻结/恢复计时（工具执行等与 AI 产出无关的阻塞）
    - ``idle()``: 当前有效静默秒数（已扣除暂停与审批挂起时间）
    """

    def __init__(self, key: str = "") -> None:
        self.key = key or ""
        self._lock = threading.RLock()
        self._last = time.monotonic()
        self._suspended_base = Suspension.suspended_duration()
        self._paused_depth = 0
        self._paused_at = 0.0

    # ── 活动 ──

    def touch(self, reason: str = "") -> None:
        """记录一次活动：静默倒计时归零。"""
        now = time.monotonic()
        with self._lock:
            self._last = now
            self._suspended_base = Suspension.suspended_duration()
        if reason and logger.isEnabledFor(10):
            logger.debug(f"[ActivityClock:{self.key}] touch: {reason}")

    # ── 暂停（工具执行，不计入 AI 静默时间）──

    def pause(self) -> None:
        with self._lock:
            if self._paused_depth == 0:
                self._paused_at = time.monotonic()
            self._paused_depth += 1

    def resume(self) -> None:
        with self._lock:
            if self._paused_depth <= 0:
                return
            self._paused_depth -= 1
            if self._paused_depth == 0:
                # 暂停时间整体丢弃：以恢复时刻作为新的计时起点
                self._last = time.monotonic()
                self._suspended_base = Suspension.suspended_duration()
                self._paused_at = 0.0

    # ── 查询 ──

    def idle(self) -> float:
        """距上次活动的有效静默秒数（>=0，已扣除暂停/挂起时间）。"""
        with self._lock:
            now = time.monotonic()
            # 暂停中：静默时间冻结在暂停开始那一刻
            reference = self._paused_at if self._paused_depth else now
            suspended = Suspension.suspended_duration() - self._suspended_base
            return max(0.0, reference - self._last - suspended)

    def reset(self) -> None:
        """完全复位（新一轮思考开始时）"""
        with self._lock:
            self._last = time.monotonic()
            self._suspended_base = Suspension.suspended_duration()
            self._paused_at = self._last if self._paused_depth else 0.0


_clocks: Dict[str, ActivityClock] = {}
_clocks_lock = threading.Lock()


def get_activity_clock(key: str) -> ActivityClock:
    """按 session（或任意 key）取共享的活动时钟，不存在则创建。"""
    if not key:
        return ActivityClock("")
    with _clocks_lock:
        clock = _clocks.get(key)
        if clock is None:
            clock = ActivityClock(key)
            _clocks[key] = clock
        return clock


def drop_activity_clock(key: str) -> None:
    """会话结束时清理，防止无界增长。"""
    if not key:
        return
    with _clocks_lock:
        _clocks.pop(key, None)
