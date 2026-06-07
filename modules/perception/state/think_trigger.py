"""差异→思考触发器 — 差异强度达标时触发单次 LLM 思考

设计原则:
- 不直接 import thinking 模块，通过 ThinkTriggerPort 接口注入
- 冷却机制防止频繁触发
- 只在差异强度达到阈值时触发
- 使用持久化异步循环，不每次创建新 loop
"""
import asyncio
import threading
import time
from typing import Any, Dict, List, Optional, Protocol

from utils.logger import setup_logger

logger = setup_logger("perception_think_trigger")


class ThinkTriggerPort(Protocol):
    """思考触发接口 — 由编排层注入具体实现"""

    async def trigger_think(self, context: str, differences: List[Any]) -> Dict[str, Any]:
        """触发一次单次思考"""


class PerceptionThinkTrigger:
    """差异→思考触发器"""

    def __init__(
        self,
        trigger_port: Optional[ThinkTriggerPort] = None,
        min_intensity: float = 50.0,
        cooldown_seconds: int = 60,
    ):
        self._trigger_port = trigger_port
        self._min_intensity = min_intensity
        self._cooldown_seconds = cooldown_seconds
        self._last_trigger_time: float = 0.0
        self._trigger_count = 0
        self._lock = threading.Lock()
        self._sub_id: str = ""
        self._async_loop: Optional[asyncio.AbstractEventLoop] = None
        self._async_thread: Optional[threading.Thread] = None

    def set_trigger_port(self, port: ThinkTriggerPort) -> None:
        self._trigger_port = port

    def _ensure_async_loop(self):
        if self._async_loop and self._async_loop.is_running():
            return
        self._async_loop = asyncio.new_event_loop()
        self._async_thread = threading.Thread(
            target=self._run_loop, daemon=True, name="think-trigger-async"
        )
        self._async_thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._async_loop)
        self._async_loop.run_forever()

    def start(self, event_bus) -> None:
        from modules.perception.events.types import PerceptionEventType
        self._sub_id = event_bus.subscribe(
            PerceptionEventType.DIFFERENCE_DETECTED,
            handler=self._on_difference,
        )
        logger.info(f"ThinkTrigger 启动: min_intensity={self._min_intensity} cooldown={self._cooldown_seconds}s")

    def stop(self, event_bus=None) -> None:
        if event_bus and self._sub_id:
            event_bus.unsubscribe(self._sub_id)
            self._sub_id = ""
        if self._async_loop and self._async_loop.is_running():
            self._async_loop.call_soon_threadsafe(self._async_loop.stop)
        if self._async_thread and self._async_thread.is_alive():
            self._async_thread.join(timeout=5)
        self._async_loop = None
        self._async_thread = None

    def _on_difference(self, event) -> None:
        """差异事件回调"""
        intensity = event.payload.get("intensity", 0)
        if intensity < self._min_intensity:
            return

        # 冷却检查 + 计数器都在锁内
        with self._lock:
            now = time.time()
            if now - self._last_trigger_time < self._cooldown_seconds:
                return
            self._last_trigger_time = now
            self._trigger_count += 1
            count = self._trigger_count

        if self._trigger_port is None:
            logger.debug(f"ThinkTrigger #{count}: 无 trigger_port，跳过执行")
            return

        context = self._build_context(event)
        logger.info(f"ThinkTrigger #{count}: intensity={intensity}")

        self._ensure_async_loop()
        asyncio.run_coroutine_threadsafe(
            self._async_trigger(context, [event.payload]),
            self._async_loop,
        )

    async def _async_trigger(self, context: str, differences: list):
        try:
            result = await self._trigger_port.trigger_think(context, differences)
            logger.info(f"ThinkTrigger 完成: {result.get('duration_ms', 0):.0f}ms")
        except Exception as e:
            logger.error(f"ThinkTrigger 执行失败: {e}")

    def _build_context(self, event) -> str:
        payload = event.payload
        parts = [
            "## 感知系统检测到环境变化",
            f"- 来源: {payload.get('source_type', 'unknown')}",
            f"- 类别: {payload.get('category', 'unknown')}",
            f"- 强度: {payload.get('intensity', 0):.0f}/100",
        ]
        desc = payload.get("description", "")
        if desc:
            parts.append(f"- 描述: {desc}")
        parts.append("\n请分析此变化是否需要关注。")
        return "\n".join(parts)

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "trigger_count": self._trigger_count,
                "min_intensity": self._min_intensity,
                "cooldown_seconds": self._cooldown_seconds,
                "has_trigger_port": self._trigger_port is not None,
            }
