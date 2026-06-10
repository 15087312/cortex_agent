"""感知事件总线 — 模块间唯一通信通道

设计原则:
- 发布-订阅模式，模块间不直接调用
- 同步分发（保证顺序），异步 handler 在独立线程执行
- 线程安全，支持运行时动态订阅/取消
"""
import asyncio
import threading
from typing import Any, Awaitable, Callable, Dict, List, Optional

from utils.logger import setup_logger
from modules.perception.events.types import PerceptionEvent, PerceptionEventType

logger = setup_logger("perception_event_bus")

SyncHandler = Callable[[PerceptionEvent], None]
AsyncHandler = Callable[[PerceptionEvent], Awaitable[None]]


class _Subscription:
    __slots__ = ("sub_id", "event_type", "sync_handler", "async_handler")

    def __init__(
        self,
        sub_id: str,
        event_type: str,
        sync_handler: Optional[SyncHandler] = None,
        async_handler: Optional[AsyncHandler] = None,
    ):
        self.sub_id = sub_id
        self.event_type = event_type
        self.sync_handler = sync_handler
        self.async_handler = async_handler


class PerceptionEventBus:
    """感知事件总线

    用法:
        bus = get_event_bus()
        bus.subscribe(PerceptionEventType.SCREEN_OCR, my_handler)
        bus.publish(event)
    """

    def __init__(self):
        self._subscriptions: Dict[str, List[_Subscription]] = {}
        self._sub_lock = threading.Lock()
        self._async_lock = threading.Lock()
        self._event_count = 0
        self._async_loop: Optional[asyncio.AbstractEventLoop] = None
        self._async_thread: Optional[threading.Thread] = None
        self._shutdown = False
        logger.info("PerceptionEventBus 初始化完成")

    def _ensure_async_loop(self):
        """确保有后台事件循环（线程安全）"""
        if self._shutdown:
            return
        if self._async_loop is not None and self._async_loop.is_running():
            return
        with self._async_lock:
            if self._shutdown:
                return
            if self._async_loop is not None and self._async_loop.is_running():
                return
            self._async_loop = asyncio.new_event_loop()
            self._async_thread = threading.Thread(
                target=self._run_async_loop, daemon=True, name="event-bus-async"
            )
            self._async_thread.start()

    def _run_async_loop(self):
        asyncio.set_event_loop(self._async_loop)
        self._async_loop.run_forever()

    def subscribe(
        self,
        event_type: str,
        handler: Optional[SyncHandler] = None,
        *,
        async_handler: Optional[AsyncHandler] = None,
    ) -> str:
        """订阅事件类型，返回 subscription_id"""
        if handler is None and async_handler is None:
            raise ValueError("必须提供 handler 或 async_handler")

        import uuid
        sub_id = uuid.uuid4().hex[:8]
        sub = _Subscription(sub_id, event_type, handler, async_handler)

        with self._sub_lock:
            if event_type not in self._subscriptions:
                self._subscriptions[event_type] = []
            self._subscriptions[event_type].append(sub)

        logger.debug(f"订阅: {event_type} -> {sub_id}")
        return sub_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """取消订阅"""
        with self._sub_lock:
            for event_type, subs in self._subscriptions.items():
                original_len = len(subs)
                self._subscriptions[event_type] = [s for s in subs if s.sub_id != subscription_id]
                if len(self._subscriptions[event_type]) < original_len:
                    logger.debug(f"取消订阅: {subscription_id}")
                    return True
        return False

    def publish(self, event: PerceptionEvent) -> None:
        """发布事件到总线"""
        with self._sub_lock:
            self._event_count += 1
            exact_subs = list(self._subscriptions.get(event.event_type, []))
            wildcard_subs = list(self._subscriptions.get(PerceptionEventType.ALL, []))

        all_subs = exact_subs + wildcard_subs
        if not all_subs:
            return

        for sub in all_subs:
            try:
                if sub.sync_handler:
                    sub.sync_handler(event)
                if sub.async_handler:
                    self._ensure_async_loop()
                    asyncio.run_coroutine_threadsafe(
                        sub.async_handler(event), self._async_loop
                    )
            except Exception as e:
                logger.error(
                    f"事件处理异常: {event.event_type} sub={sub.sub_id} err={e}"
                )

    def get_stats(self) -> Dict[str, Any]:
        """获取总线统计"""
        with self._sub_lock:
            sub_counts = {
                et: len(subs) for et, subs in self._subscriptions.items()
            }
            count = self._event_count
        return {
            "total_events": count,
            "subscriptions": sub_counts,
            "total_subscribers": sum(sub_counts.values()),
        }

    def shutdown(self) -> None:
        """优雅关闭（停止异步循环）"""
        self._shutdown = True
        if self._async_loop and self._async_loop.is_running():
            self._async_loop.call_soon_threadsafe(self._async_loop.stop)
        if self._async_thread and self._async_thread.is_alive():
            self._async_thread.join(timeout=5)
        self._async_loop = None
        self._async_thread = None

    def clear(self) -> None:
        """清空所有订阅（测试用）"""
        self.shutdown()
        with self._sub_lock:
            self._subscriptions.clear()
            self._event_count = 0


# 全局单例
_event_bus: Optional[PerceptionEventBus] = None
_init_lock = threading.Lock()


def get_event_bus() -> PerceptionEventBus:
    """获取事件总线单例（线程安全）"""
    global _event_bus
    if _event_bus is None:
        with _init_lock:
            if _event_bus is None:
                _event_bus = PerceptionEventBus()
    return _event_bus
