"""挂起（暂停计时）机制 — 当工具调用等待用户审批时，暂停所有思考/轮次计时器。

用法：
    # 安全门控等待用户审批时：
    Suspension.suspend()
    ... await 审批 ...
    Suspension.resume()

    # 各计时器改用 pausable_wait_for 代替 asyncio.wait_for：
    await pausable_wait_for(coro, timeout=120)
"""
import asyncio
import threading
import time
from typing import Any, Awaitable, Optional

_lock = threading.Lock()
_suspended_at: Optional[float] = None   # 当前挂起开始时间（未挂起为 None）
_total_suspended: float = 0.0            # 累计挂起时长（秒）


class Suspension:
    """线程安全的全局面板 — 记录挂起状态与累计挂起时长"""

    @staticmethod
    def suspend() -> None:
        global _suspended_at
        with _lock:
            if _suspended_at is None:
                _suspended_at = time.monotonic()

    @staticmethod
    def resume() -> None:
        global _suspended_at, _total_suspended
        with _lock:
            if _suspended_at is not None:
                _total_suspended += time.monotonic() - _suspended_at
                _suspended_at = None

    @staticmethod
    def suspended_duration() -> float:
        """返回自程序启动以来累计的挂起秒数（含当前正在挂起的时间）"""
        with _lock:
            d = _total_suspended
            if _suspended_at is not None:
                d += time.monotonic() - _suspended_at
            return d

    @staticmethod
    def is_suspended() -> bool:
        with _lock:
            return _suspended_at is not None


def effective_elapsed_since(mono_start: float, susp_start: float) -> float:
    """自某一起点以来的有效耗时（秒），扣除期间挂起的时间。

    Args:
        mono_start: time.monotonic() 记录的起点
        susp_start: 起点时刻 Suspension.suspended_duration() 的值
    """
    return time.monotonic() - mono_start - (
        Suspension.suspended_duration() - susp_start
    )


async def pausable_wait_for(awaitable: Awaitable, timeout: float) -> Any:
    """asyncio.wait_for 的挂起感知版本 — 挂起期间不计入超时。

    在等待审批（挂起）时，倒计时冻结；恢复后继续计时。
    """
    task = asyncio.ensure_future(awaitable)
    start = time.monotonic()
    suspended_before = Suspension.suspended_duration()
    try:
        while True:
            elapsed = time.monotonic() - start - (
                Suspension.suspended_duration() - suspended_before
            )
            remaining = timeout - elapsed
            if remaining <= 0:
                raise asyncio.TimeoutError()
            done, _ = await asyncio.wait({task}, timeout=remaining)
            if task in done:
                return task.result()
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
