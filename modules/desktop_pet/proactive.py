"""感知事件 → 视觉分析 → 注入感知池

接收感知系统的事件（窗口切换、屏幕 OCR、屏幕变化），经统一冷却过滤后
截屏调用视觉模型，将描述文本作为 screen.vision 事件推进 PerceptionPool，
供 ReAct 循环的 build_context 自然拾取为环境感知上下文。

不主动触发对话，仅作为循环的环境感知输入。
"""
import asyncio
import time

from utils.logger import setup_logger

logger = setup_logger("pet_proactive")


class ProactiveTrigger:
    """感知事件 → 视觉分析 → 感知池注入

    - 任意感知事件均可触发（不区分事件类型）
    - 单一冷却 15s，从上次调用完成后开始计时
    - asyncio.Lock 保证视觉模型请求排队，不会并发
    """

    _COOLDOWN = 15.0
    _IMPORTANCE_THRESHOLD = 0.5

    def __init__(self, engine):
        self._engine = engine
        self._lock = asyncio.Lock()
        self._last_done: float = 0

    async def on_event(self, event) -> None:
        """感知事件回调入口（由 PetEngine 的事件订阅调用）"""
        event_type = getattr(event, "event_type", "") or ""
        logger.debug(f"[Pet] 收到感知事件: {event_type}")

        if not self._engine.enabled or not self._engine.is_active():
            return

        importance = getattr(event, "importance", 0.5) or 0.5
        if importance < self._IMPORTANCE_THRESHOLD:
            return

        if time.time() - self._last_done < self._COOLDOWN:
            return

        async with self._lock:
            if time.time() - self._last_done < self._COOLDOWN:
                return
            vision_desc = await self._analyze_screen_vision()
            self._last_done = time.time()

        if not vision_desc:
            return

        try:
            from modules.perception.integration import get_perception_integrator
            integrator = get_perception_integrator()
            integrator.pool.add("screen.vision", "proactive_trigger", vision_desc)
            logger.info(f"[Pet] 视觉上下文已注入感知池: {vision_desc[:80]}")
        except Exception as e:
            logger.warning(f"[Pet] 注入感知池失败: {e}")

    async def _analyze_screen_vision(self) -> str:
        """截屏 + 视觉模型分析，返回屏幕内容描述（空串表示失败）"""
        try:
            from utils.screen_capture import capture_screen_bytes
            from infra.data_process.core.image_analyzer import ImageAnalyzer

            screen_bytes = capture_screen_bytes(max_width=768)
            if not screen_bytes:
                return ""

            analyzer = ImageAnalyzer()
            await analyzer.initialize()
            result = await analyzer.analyze(
                screen_bytes,
                prompt="用一两句话客观概括当前屏幕内容：什么应用、什么界面、正在做什么。",
            )
            desc = (result.get("description") or "").strip()
            if desc:
                logger.info(f"[Pet] 视觉分析完成: {desc[:80]}")
            return desc[:300]
        except Exception as e:
            logger.warning(f"[Pet] 视觉分析失败 (非致命): {e}")
            return ""
