"""
异步工具 - 异步任务包装、并发控制
"""
import asyncio
from typing import List, Callable, Any
from functools import wraps


def async_wrap(func: Callable) -> Callable:
    """将同步函数包装为异步函数"""

    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        # Q-14: Use asyncio.to_thread() instead of deprecated get_event_loop()
        # Compatible with Python 3.9+
        return await asyncio.to_thread(func, *args, **kwargs)

    return async_wrapper


async def gather_with_concurrency(n: int, *coros) -> List[Any]:
    """带并发限制的 gather"""
    semaphore = asyncio.Semaphore(n)
    
    async def sem_coro(coro):
        async with semaphore:
            return await coro
    
    return await asyncio.gather(*(sem_coro(coro) for coro in coros))


async def run_with_timeout(coro, timeout_seconds: float) -> Any:
    """带超时的异步任务"""
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        raise TimeoutError(f"Task timed out after {timeout_seconds} seconds")


class AsyncTaskGroup:
    """异步任务组"""
    
    def __init__(self, max_concurrent: int = 10):
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
    
    async def add_task(self, coro) -> Any:
        """添加任务"""
        async with self.semaphore:
            return await coro
    
    async def run_all(self, coros: List) -> List[Any]:
        """运行所有任务"""
        tasks = [self.add_task(coro) for coro in coros]
        return await asyncio.gather(*tasks, return_exceptions=True)
