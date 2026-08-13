"""utils/suspension.py — 挂起（暂停计时）机制"""
import asyncio
import time

import pytest

from utils.suspension import Suspension, effective_elapsed_since, pausable_wait_for


def _reset():
    import utils.suspension as s
    with s._lock:
        s._suspended_at = None
        s._total_suspended = 0.0


def test_suspend_resume():
    _reset()
    assert Suspension.is_suspended() is False
    Suspension.suspend()
    assert Suspension.is_suspended() is True
    Suspension.resume()
    assert Suspension.is_suspended() is False


def test_suspend_idempotent_double():
    _reset()
    Suspension.suspend()
    Suspension.suspend()  # 第二次不重复计时
    Suspension.resume()
    assert Suspension.suspended_duration() < 0.1


def test_resume_without_suspend_noop():
    _reset()
    Suspension.resume()  # 不抛异常
    assert Suspension.suspended_duration() == 0.0


def test_suspended_duration_accumulates():
    _reset()
    Suspension.suspend()
    time.sleep(0.05)
    Suspension.resume()
    assert 0.03 < Suspension.suspended_duration() < 1.0
    # 再次挂起期间 duration 持续增长
    Suspension.suspend()
    time.sleep(0.05)
    assert Suspension.suspended_duration() > 0.05


def test_effective_elapsed_since():
    _reset()
    start = time.monotonic()
    Suspension.suspend()
    time.sleep(0.05)
    Suspension.resume()
    susp_start = 0.0
    elapsed = effective_elapsed_since(start, susp_start)
    # 扣除了挂起的 0.05s
    assert elapsed < 0.05


def test_pausable_wait_for_success():
    async def _run():
        return await pausable_wait_for(asyncio.sleep(0), timeout=5)

    assert asyncio.run(_run()) is None


def test_pausable_wait_for_timeout():
    async def _run():
        with pytest.raises(asyncio.TimeoutError):
            await pausable_wait_for(asyncio.sleep(10), timeout=0.05)

    asyncio.run(_run())


def test_pausable_wait_for_result():
    async def _run():
        return await pausable_wait_for(_value(), timeout=5)

    async def _value():
        return 42

    assert asyncio.run(_run()) == 42


def test_pausable_wait_for_cancels_pending():
    """超时/异常后未完成任务应被取消"""
    async def _run():
        task_cancelled = []

        async def slow():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                task_cancelled.append(True)
                raise

        with pytest.raises(asyncio.TimeoutError):
            await pausable_wait_for(slow(), timeout=0.05)
        await asyncio.sleep(0)  # 让取消完成
        assert task_cancelled == [True]

    asyncio.run(_run())


def test_pausable_wait_for_suspension_extends_timeout():
    """挂起期间不计入超时"""
    _reset()
    async def _run():
        started = asyncio.get_event_loop().time()

        async def wait():
            # 挂起一段时间后再继续
            Suspension.suspend()
            await asyncio.sleep(0.05)
            Suspension.resume()
            return "done"

        result = await pausable_wait_for(wait(), timeout=0.01)
        # 挂起的 0.05s 不计入超时 → 不抛 TimeoutError
        return result

    assert asyncio.run(_run()) == "done"
