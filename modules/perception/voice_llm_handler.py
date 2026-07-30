"""语音指令处理器 — 订阅 SPEECH_DETECTED 事件，接入完整思考流程

当 VoiceDetector 检测到语音后发布 SPEECH_DETECTED 事件，
此处理器接收事件，用可配置的前后缀（如"科特…完毕"）包装语音文本，
然后通过 StreamThinkingSystem.think() 走完整的总指挥流程
（安全审查 → 记忆加载 → 专家引导 → 多模型思考 → 输出审查），
而不是直接调用大模型 API。

设计要点：
- 语音走与 CLI TUI 完全相同的思考路径
- 专用 voice session（不干扰键盘输入）
- 跨事件循环调度（Event Bus 的后台循环 → 应用主循环）
- 响应自动推送到活跃的 CLI TUI WebSocket + 控制台输出兜底
"""

import asyncio
import time
import uuid
from typing import Optional

from utils.logger import setup_logger

logger = setup_logger("voice_llm_handler")


class VoiceLLMHandler:
    """语音指令处理器

    订阅感知事件总线上的 SPEECH_DETECTED 事件。
    收到语音识别结果时，通过 StreamThinkingSystem.think() 接入完整思考流程。
    """

    def __init__(self, event_bus=None):
        self._event_bus = event_bus
        self._sub_id: Optional[str] = None
        self._running = False
        self._voice_session_id: Optional[str] = None
        self._thinking_system = None

    @property
    def is_active(self) -> bool:
        return self._running

    def start(self) -> None:
        """启动语音指令处理器：创建语音会话 + 订阅事件"""
        if self._running:
            return
        self._running = True

        try:
            from modules.thinking.api_stream import get_thinking_system
            from modules.perception.events.bus import get_event_bus
            from modules.perception.events.types import PerceptionEventType

            # ── 1. 获取思考系统 ──
            self._thinking_system = get_thinking_system()

            # ── 2. 创建专用语音会话（独立于键盘输入，避免冲突） ──
            self._voice_session_id = f"voice_{uuid.uuid4().hex[:8]}"
            asyncio.create_task(self._thinking_system.start(self._voice_session_id))
            logger.info(f"语音会话已创建: {self._voice_session_id[:12]}")

            # ── 3. 订阅语音事件 ──
            bus = self._event_bus or get_event_bus()
            self._sub_id = bus.subscribe(
                PerceptionEventType.SPEECH_DETECTED,
                async_handler=self._on_speech_detected,
            )
            logger.info("语音指令处理器已启动 → 总指挥路径")
        except Exception as e:
            logger.error(f"语音指令处理器启动失败: {e}")
            self._running = False

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False

        # 取消订阅
        if self._sub_id:
            try:
                from modules.perception.events.bus import get_event_bus
                bus = self._event_bus or get_event_bus()
                bus.unsubscribe(self._sub_id)
            except Exception as e:
                logger.debug(f"取消订阅失败 (非致命): {e}")
            self._sub_id = None

        # 关闭语音会话
        if self._voice_session_id and self._thinking_system:
            try:
                asyncio.create_task(
                    self._thinking_system.stop(self._voice_session_id)
                )
            except Exception:
                pass
            self._voice_session_id = None

        logger.info("语音指令处理器已停止")

    async def _on_speech_detected(self, event) -> None:
        """处理 SPEECH_DETECTED 事件 — 调度到主循环执行完整思考流程"""
        if not self._running or not self._thinking_system or not self._voice_session_id:
            return

        try:
            # ── 1. 提取语音文本 ──
            payload = event.payload if hasattr(event, "payload") else {}
            text = payload.get("text", "")
            if not text or not text.strip():
                return

            # ── 2. 动态读取配置（支持热更新） ──
            from config.settings import settings as current_settings
            s = current_settings
            if not s.PERCEPTION_VOICE_LLM_TRIGGER_ENABLED:
                return

            # 唤醒词已由 VoiceDetector 剥离，此处直接传递干净文本
            logger.info(f"语音→总指挥: text={text[:60]}")

            # ── 3. 跨循环调度 ──
            # 注意：此 handler 由 Event Bus 在后台循环 (event-bus-async) 调用。
            # StreamThinkingSystem.think() 需要在主应用循环上运行。
            # 通过 connection_manager._loop 获取主循环，使用 run_coroutine_threadsafe 调度。
            from modules.thinking.api_stream import connection_manager

            main_loop = getattr(connection_manager, "_loop", None)
            if main_loop and not main_loop.is_closed():
                # 跨循环调度：在后台发起任务，在后台等待结果
                future = asyncio.run_coroutine_threadsafe(
                    self._thinking_system.think(
                        self._voice_session_id,
                        text.strip(),
                    ),
                    main_loop,
                )
                # wrap_future 允许在后天循环中等待主循环的 Future
                response = await asyncio.wrap_future(future)
            else:
                # 无主循环时，在当当前循环上兜底执行
                logger.warning("无主事件循环可用，在当前循环执行思考流程")
                response = await self._thinking_system.think(
                    self._voice_session_id,
                    text.strip(),
                )

            # ── 4. 路由响应 ──
            if response and response.strip():
                await self._route_to_cli_tui(response, text)
                self._output_response(response, text)
            else:
                logger.info("语音思考无输出响应")

        except asyncio.CancelledError:
            logger.info("语音思考任务被取消")
        except Exception as e:
            logger.error(f"语音指令处理异常: {e}", exc_info=True)

    async def _route_to_cli_tui(self, response: str, original_text: str) -> None:
        """将语音思考结果推送到活跃的 CLI TUI WebSocket 连接"""
        try:
            from modules.thinking.api_stream import connection_manager

            # 查找非语音的活跃会话（CLI TUI 连接）
            async with connection_manager._lock:
                active_sessions = list(connection_manager.active_connections.keys())

            target_session = None
            for sid in active_sessions:
                if sid != self._voice_session_id:
                    target_session = sid
                    break

            if target_session:
                await connection_manager.send_json(
                    target_session,
                    {
                        "type": "message",
                        "event": "assistant_message",
                        "content": f"[来自语音] {response}",
                        "role": "main",
                        "data": {"source": "voice"},
                        "timestamp": time.time(),
                    },
                )
                logger.info(
                    f"语音响应已推送到 CLI TUI (session={target_session[:12]})"
                )
        except Exception as e:
            logger.debug(f"推送到 CLI TUI 失败 (非致命): {e}")

    def _output_response(self, response: str, original_text: str) -> None:
        """控制台输出兜底 — 当 CLI TUI 不可用时，语音响应仍可见"""
        try:
            from modules.output_system.distributor import OutputDistributor

            distributor = OutputDistributor()
            header = f"[语音] 🎤 识别: {original_text[:60]}"
            body = f"[语音] 🤖 回复: {response[:200]}"
            distributor.distribute(f"\n{header}\n{body}\n", channel="console")
        except Exception as e:
            logger.debug(f"输出分发失败 (非致命): {e}")
