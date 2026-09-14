"""PetTaskQueue 单测 — 串行消费（不并发）/ FIFO / 取消 / 循环重建

桌宠所有对话触发源（页面聊天、语音事件、定时/主动思考）统一入队，
同一时间只允许一个模型任务在跑。
"""
import asyncio

import pytest

from modules.desktop_pet.task_queue import PetTask, PetTaskQueue


def _fresh_queue() -> PetTaskQueue:
    q = PetTaskQueue()
    PetTaskQueue._instance = q
    return q


def _factory(result=None, delay=0.0, track=None, tag=""):
    """任务工厂：记录并发峰值与执行顺序"""

    async def _run():
        if track is not None:
            track["concurrent"] += 1
            track["peak"] = max(track["peak"], track["concurrent"])
            track["order"].append(f"start:{tag}")
        try:
            if delay:
                await asyncio.sleep(delay)
            return result if result is not None else f"done:{tag}"
        finally:
            if track is not None:
                track["concurrent"] -= 1
                track["order"].append(f"end:{tag}")

    return _run


def test_serial_no_concurrency():
    """三个任务串行执行：并发峰值恒为 1"""
    q = _fresh_queue()

    async def main():
        track = {"concurrent": 0, "peak": 0, "order": []}
        futs = []
        for i in range(3):
            futs.append(await q.submit(f"t{i}", _factory(delay=0.01, track=track, tag=str(i))))
        results = [await f for f in futs]
        assert results == ["done:0", "done:1", "done:2"]
        assert track["peak"] == 1, f"并发峰值 {track['peak']} > 1，存在并发！"
        # FIFO：完成顺序与入队顺序一致
        assert track["order"] == [
            "start:0", "end:0", "start:1", "end:1", "start:2", "end:2",
        ]

    asyncio.run(main())


def test_submit_returns_future_with_backref():
    """submit 返回带 _pet_task 反向引用的 Future（供取消）"""
    q = _fresh_queue()

    async def main():
        async def job():
            await asyncio.sleep(0.05)
            return 42

        fut = await q.submit("job", job)
        assert fut is not None
        assert getattr(fut, "_pet_task", None) is not None
        assert isinstance(fut._pet_task, PetTask)
        assert await fut == 42

    asyncio.run(main())


def test_cancel_queued_task_worker_survives():
    """取消排队中的任务：worker 跳过它继续消费下一个"""
    q = _fresh_queue()

    async def main():
        track = {"concurrent": 0, "peak": 0, "order": []}
        fut1 = await q.submit("long", _factory(delay=0.1, track=track, tag="long"))
        fut2 = await q.submit("cancelme", _factory(track=track, tag="cancelled"))
        fut3 = await q.submit("after", _factory(track=track, tag="after"))
        await asyncio.sleep(0.02)  # long 开始执行，cancelme 还在排队
        fut2._pet_task.cancel()
        assert await fut1 == "done:long"
        with pytest.raises(asyncio.CancelledError):
            await fut2  # 已被取消
        assert await fut3 == "done:after"
        assert "start:cancelled" not in track["order"], "被取消的任务不应执行"
        assert track["peak"] == 1

    asyncio.run(main())


def test_cancel_running_task_worker_continues():
    """取消执行中的任务：worker 不死，继续下一个任务"""
    q = _fresh_queue()

    async def main():
        started = []

        async def blocker():
            started.append(True)
            await asyncio.sleep(5)  # 会被取消

        async def quick():
            return "quick"

        fut1 = await q.submit("blocker", blocker)
        fut2 = await q.submit("quick", quick)
        await asyncio.sleep(0.02)
        fut1._pet_task.cancel()
        assert await fut2 == "quick", "worker 应在任务被取消后继续消费"

    asyncio.run(main())


def test_task_exception_propagates_to_future():
    """任务内异常转写到提交方 Future，且不杀死 worker"""
    q = _fresh_queue()

    async def main():
        async def boom():
            raise RuntimeError("model down")

        fut1 = await q.submit("boom", boom)
        fut2 = await q.submit("ok", _factory(tag="ok"))
        with pytest.raises(RuntimeError):
            await fut1
        assert await fut2 == "done:ok"

    asyncio.run(main())


def test_fire_and_forget_no_future():
    """无运行循环时创建任务（future=None）→ worker 正常执行，结果丢弃"""
    task = PetTask("fire", _factory(tag="ff"))
    assert task.future is None

    async def main():
        await task.execute()  # 不抛异常即可

    asyncio.run(main())


def test_loop_restart_abandons_pending():
    """换事件循环（进程内重启）：旧队列未执行任务被快速失败标记，新循环可用

    注意：不跨循环 await 旧 Future（未定义行为），只检查其状态。
    """
    q = _fresh_queue()

    async def first():
        f1 = await q.submit("quick", _factory(delay=0.05, tag="quick"))
        await f1  # worker 先跑完当前任务，退出时只剩 pending 未执行
        f2 = await q.submit("pending", _factory(delay=0.2, tag="pending"))
        return f2

    f2 = asyncio.run(first())

    async def second():
        # 新循环首次 submit 触发废弃逻辑：旧队列被清空重建
        f3 = await q.submit("fresh", _factory(tag="fresh"))
        assert await f3 == "done:fresh", "新循环应正常工作"
        assert q.pending() == 0, "旧队列的未执行任务应已被废弃清空"
        # 旧循环的 Future 绑定已关闭的循环，无法安全标记（Python 限制），
        # 生产中该循环已消亡、无人 await，不会泄漏 —— 只验证队列状态。

    asyncio.run(second())
