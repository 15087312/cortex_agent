"""utils/async_utils 测试：async_wrap / 并发 gather / 超时 / 任务组"""
import asyncio

import pytest

from utils.async_utils import (
    async_wrap, gather_with_concurrency, run_with_timeout, AsyncTaskGroup,
)


def test_async_wrap_sync_func():
    wrapped = async_wrap(lambda a, b: a + b)
    assert asyncio.run(wrapped(2, 3)) == 5


async def test_gather_with_concurrency_limits():
    running, peak = 0, 0

    async def task(i):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.03)
        running -= 1
        return i

    res = await gather_with_concurrency(2, *[task(i) for i in range(4)])
    assert sorted(res) == [0, 1, 2, 3]
    assert peak <= 2


async def test_run_with_timeout_ok():
    assert await run_with_timeout(asyncio.sleep(0), 1) is None


async def test_run_with_timeout_raises():
    with pytest.raises(TimeoutError):
        await run_with_timeout(asyncio.sleep(1), 0.01)


async def test_async_task_group_run_all():
    async def t(i):
        return i * 2

    g = AsyncTaskGroup(max_concurrent=2)
    res = await g.run_all([t(1), t(2)])
    assert res == [2, 4]


async def test_async_task_group_run_all_exceptions():
    async def ok():
        return 1

    async def bad():
        raise ValueError("x")

    g = AsyncTaskGroup()
    res = await g.run_all([ok(), bad()])
    assert res[0] == 1
    assert isinstance(res[1], ValueError)
