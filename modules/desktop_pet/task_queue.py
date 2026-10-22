"""桌宠串行任务队列 — 所有对话触发源统一入队，单一 worker 串行消费

背景：桌宠有多条对话触发路径（页面聊天 SSE、语音事件、定时/主动思考、
桌宠窗口互动按键），它们都汇聚到 PetEngine.chat / stream_chat。此前各路径
直接并发调用模型循环，会出现同一桌宠同时跑多个思考、历史乱序、last_reply
互相覆盖等问题。

本队列做成类消息队列式消费：
- 所有触发源 submit 一个任务工厂（coroutine factory）后拿 Future 等结果；
- 单一 worker 按 FIFO 逐个执行，同一时间只跑一个模型任务（严格串行）；
- 任务可取消（如 SSE 客户端断开），取消后 worker 继续消费下一个；
- worker 绑定创建时的事件循环；检测到调用方换循环（如测试里多次 asyncio.run、
  进程内重启）时废弃旧队列/旧 worker，在当前循环重建（未执行任务快速失败）。
"""
import asyncio
import threading
from typing import Any, Callable, Coroutine, List, Optional

from utils.logger import setup_logger

logger = setup_logger("pet_task_queue")


class PetTask:
    """队列任务：工厂函数产出实际协程，future 绑定提交方的事件循环"""

    def __init__(self, kind: str, factory: Callable[[], Coroutine[Any, Any, Any]]):
        self.kind = kind
        self.factory = factory
        try:
            loop = asyncio.get_running_loop()
            self.future: Optional[asyncio.Future] = loop.create_future()
        except RuntimeError:
            self.future = None
        self._task: Optional[asyncio.Task] = None  # worker 启动后指向执行任务

    def cancel(self) -> None:
        """取消任务（排队中/执行中均可）"""
        if self.future is not None and not self.future.done():
            self.future.cancel()
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def _set_result(self, result: Any) -> None:
        try:
            if self.future is not None and not self.future.done():
                self.future.set_result(result)
        except Exception as _swallowed_err:
            logger.debug(f"[_set_result] ignored Exception: {_swallowed_err}") # 提交方循环已关闭等场景：结果无人接收，静默丢弃

    def _set_exception(self, exc: BaseException) -> None:
        try:
            if self.future is not None and not self.future.done():
                self.future.set_exception(exc)
        except Exception as _swallowed_err:
            logger.debug(f"[_set_exception] ignored Exception: {_swallowed_err}")

    async def execute(self) -> None:
        """worker 调用：运行工厂协程并把结果/异常转写到提交方 future"""
        self._task = asyncio.current_task()
        try:
            result = await self.factory()
            self._set_result(result)
        except asyncio.CancelledError:
            if self.future is not None and not self.future.done():
                self.future.cancel()
            raise
        except Exception as e:
            self._set_exception(e)
        finally:
            self._task = None


class PetTaskQueue:
    """桌宠串行任务队列（单例）——类消息队列式消费，不并发"""

    _instance: Optional["PetTaskQueue"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._queue: Optional[asyncio.Queue] = None
        self._worker: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running_kind = ""  # 当前正在执行的任务类型（诊断用）

    @classmethod
    def get_instance(cls) -> "PetTaskQueue":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 对外接口 ──

    async def submit(self, kind: str, factory: Callable[[], Coroutine[Any, Any, Any]]) -> Optional[asyncio.Future]:
        """入队一个任务，返回绑定调用方循环的 Future（await 它拿结果）。

        kind 仅用于日志（chat / stream / auto_think ...）。
        """
        self._ensure_worker()
        task = PetTask(kind, factory)
        assert self._queue is not None
        depth = self._queue.qsize()
        if depth:
            logger.info(f"[Pet] 任务入队 kind={kind} 排队中={depth}（串行等待前序任务）")
        self._queue.put_nowait(task)
        if task.future is not None:
            # 反向引用：提交方（如 SSE 断开）可通过 future 找到任务并取消
            task.future._pet_task = task  # type: ignore[attr-defined]
        return task.future

    @property
    def running_kind(self) -> str:
        return self._running_kind

    def pending(self) -> int:
        try:
            return self._queue.qsize() if self._queue else 0
        except Exception:
            return 0

    # ── worker ──

    def _ensure_worker(self) -> None:
        """确保 worker 在当前事件循环上运行；换循环时废弃旧 worker 重建"""
        loop = asyncio.get_running_loop()
        if self._worker is not None and self._worker.done():
            self._worker = None
        if self._queue is not None and self._loop is loop and self._worker is not None:
            return
        # 换循环（或首次调用）：旧队列里未执行的任务快速失败（其 future 绑定旧循环，
        # 生产环境正常关闭时队列应为空；测试多次 asyncio.run 也走这里）
        if self._queue is not None:
            abandoned: List[PetTask] = []
            while True:
                try:
                    abandoned.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            for t in abandoned:
                logger.warning(f"[Pet] 循环切换，丢弃未执行任务 kind={t.kind}")
                t._set_exception(RuntimeError("pet task queue restarted"))
        self._queue = asyncio.Queue()
        self._loop = loop
        self._worker = loop.create_task(self._run_worker())

    async def _run_worker(self) -> None:
        """单一消费者：FIFO 逐个执行，同一时间只跑一个任务"""
        assert self._queue is not None
        while True:
            task = await self._queue.get()
            # 排队中被取消的任务直接跳过（future 已取消 = 提交方不再需要结果）
            if task.future is not None and task.future.cancelled():
                self._queue.task_done()
                continue
            self._running_kind = task.kind
            # 每个任务独立 task 运行：单个任务被取消（SSE 断开）不影响 worker
            job = asyncio.create_task(task.execute())
            try:
                await asyncio.wait({job})
            except asyncio.CancelledError:
                # worker 自身被取消（事件循环关停）→ 连同正在执行的任务一起退出，
                # 绝不能吞掉取消继续循环（否则 asyncio.run 关停时 _cancel_all_tasks 永远等不完）
                job.cancel()
                self._running_kind = ""
                raise
            except Exception as e:
                logger.error(f"[Pet] 任务执行失败 kind={task.kind}: {e}")
            finally:
                self._running_kind = ""
                self._queue.task_done()
