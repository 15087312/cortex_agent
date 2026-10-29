"""utils/activity.py — 活动感知计时（有产出就重新计时 / 工具执行冻结计时）"""
import asyncio
import time

import pytest

import utils.suspension as susp_mod
from utils.activity import ActivityClock, drop_activity_clock, get_activity_clock
from utils.suspension import Suspension, pausable_wait_for


def _reset_suspension():
    with susp_mod._lock:
        susp_mod._suspended_at = None
        susp_mod._total_suspended = 0.0


def test_touch_resets_idle():
    clock = ActivityClock("touch")
    time.sleep(0.05)
    assert clock.idle() >= 0.04
    clock.touch()
    assert clock.idle() < 0.02


def test_pause_freezes_idle():
    """工具执行（pause）期间不计入静默时间"""
    clock = ActivityClock("pause")
    clock.touch()
    clock.pause()
    time.sleep(0.08)
    assert clock.idle() < 0.02
    clock.resume()
    assert clock.idle() < 0.02
    time.sleep(0.05)
    assert clock.idle() >= 0.04


def test_pause_nested():
    clock = ActivityClock("nested")
    clock.pause()
    clock.pause()
    clock.resume()
    time.sleep(0.05)
    # 还差一层 resume，仍然冻结
    assert clock.idle() < 0.02
    clock.resume()
    time.sleep(0.05)
    assert clock.idle() >= 0.04


def test_suspension_freezes_idle():
    """等待用户审批（Suspension）期间不计入静默时间"""
    _reset_suspension()
    try:
        clock = ActivityClock("susp")
        clock.touch()
        Suspension.suspend()
        time.sleep(0.08)
        assert clock.idle() < 0.02
        Suspension.resume()
    finally:
        _reset_suspension()


def test_registry_shares_clock_per_session():
    first = get_activity_clock("sess-shared")
    assert get_activity_clock("sess-shared") is first
    drop_activity_clock("sess-shared")
    assert get_activity_clock("sess-shared") is not first
    drop_activity_clock("sess-shared")


def test_wait_for_activity_keeps_alive_on_progress():
    """只要持续有产出，超过 timeout 也不会被判超时"""
    async def _run():
        clock = ActivityClock("alive")

        async def work():
            for _ in range(8):
                await asyncio.sleep(0.03)
                clock.touch()
            return "done"

        # 静默超时 0.12s；总耗时约 0.24s，但每 0.03s 有活动
        return await pausable_wait_for(work(), timeout=0.12, activity=clock)

    assert asyncio.run(_run()) == "done"


def test_wait_for_activity_times_out_when_silent():
    async def _run():
        clock = ActivityClock("silent")

        async def work():
            await asyncio.sleep(2)

        with pytest.raises(asyncio.TimeoutError):
            await pausable_wait_for(work(), timeout=0.1, activity=clock)

    asyncio.run(_run())


def test_wait_for_activity_pause_extends_deadline():
    """工具执行期间 pause，超过 timeout 也不超时"""
    async def _run():
        clock = ActivityClock("paused")

        async def work():
            clock.pause()
            await asyncio.sleep(0.25)
            clock.resume()
            return "done"

        return await pausable_wait_for(work(), timeout=0.1, activity=clock)

    assert asyncio.run(_run()) == "done"


def test_wait_for_activity_max_wall():
    """有活动也要受整体墙钟上限约束"""
    async def _run():
        clock = ActivityClock("wall")

        async def work():
            for _ in range(30):
                await asyncio.sleep(0.02)
                clock.touch()

        with pytest.raises(asyncio.TimeoutError):
            await pausable_wait_for(work(), timeout=10, activity=clock, max_wall=0.12)

    asyncio.run(_run())
