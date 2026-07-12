"""
统一主动触发 — 替代 ThinkTrigger + DetectorBridge + PerceptionDifferenceSource + ProactiveOutreachHandler

条件:
  屏幕变化幅度 > threshold (change_ratio >= 15%)
  AND 空闲时间 > idle_threshold (默认 5 分钟)
  AND 不在冷却期
  → 触发一次主动询问

空闲时间由自身维护的 Timer 跟踪，用户活动 (notify_activity) 重置计时。
"""
import asyncio
import random
import threading
import time
from typing import Any, Dict, Optional

from utils.logger import setup_logger

logger = setup_logger("perception_proactive_trigger")

# 主动搭话专用 system prompt — 保留人格，去除工具规则
_OUTREACH_SYSTEM_PROMPT = """你是一个有人格的 AI 助手，正在观察用户的工作环境。

【人格】可爱、温暖、有人情味的助手，像一个贴心的伙伴
【风格】简洁自然，像真人朋友一样说话，不要过于正式或机械
【任务】主动关心用户，询问是否需要帮助，或提醒用户注意屏幕上的变化

【约束】
- 不要暴露内部结构（如 MessageBus、delegate_task 等）
- 不要编造不存在的信息
- 回复简短，1-2 句话即可
- 如果没有明确需要帮助的场景，可以简单问候或提醒休息

【核心价值观】
- 安全第一：不伤害用户或系统安全
- 诚实守信：不欺骗用户
- 保护隐私：不泄露敏感信息"""


class IdleTimer:
    """空闲计时器 — 记录用户最后一次活动时间"""

    def __init__(self):
        self._last_activity = time.time()
        self._lock = threading.Lock()

    def notify_activity(self) -> None:
        with self._lock:
            self._last_activity = time.time()

    @property
    def idle_seconds(self) -> float:
        with self._lock:
            return time.time() - self._last_activity

    @property
    def idle_minutes(self) -> float:
        return self.idle_seconds / 60.0


class ProactiveTrigger:
    """统一主动触发

    订阅 SCREEN_DIFF 事件，条件满足时触发 LLM 调用并推送到 WebSocket。
    """

    def __init__(
        self,
        change_ratio_threshold: float = 0.15,
        idle_threshold_seconds: int = 300,
        cooldown_seconds: int = 900,
    ):
        self._idle_timer = IdleTimer()
        self._change_ratio_threshold = change_ratio_threshold
        self._idle_threshold_seconds = idle_threshold_seconds
        self._cooldown_seconds = cooldown_seconds
        self._last_trigger_time: float = 0.0
        self._trigger_count = 0
        self._lock = threading.Lock()
        self._sub_id: str = ""
        self._event_bus = None

    # ── 生命周期 ──

    def start(self, event_bus) -> None:
        from modules.perception.events.types import PerceptionEventType
        self._event_bus = event_bus
        self._sub_id = event_bus.subscribe(
            PerceptionEventType.SCREEN_DIFF,
            handler=self._on_screen_diff,
        )
        logger.info(
            f"主动触发启动: change_ratio>={self._change_ratio_threshold:.0%} "
            f"idle>{self._idle_threshold_seconds}s "
            f"cooldown={self._cooldown_seconds}s"
        )

    def stop(self) -> None:
        if self._event_bus and self._sub_id:
            self._event_bus.unsubscribe(self._sub_id)
            self._sub_id = ""
        logger.info("主动触发已停止")

    def notify_activity(self) -> None:
        """用户有活动时调用，重置空闲计时"""
        self._idle_timer.notify_activity()

    def reset_cooldown(self) -> None:
        """用户主动交互时调用，重置冷却期（避免在用户说话时主动搭话）"""
        with self._lock:
            self._last_trigger_time = time.time()
            self._idle_timer.notify_activity()

    # ── 事件处理 ──

    def _on_screen_diff(self, event) -> None:
        """SCREEN_DIFF 事件回调"""
        change_ratio = event.payload.get("change_ratio", 0)
        if change_ratio < self._change_ratio_threshold:
            return

        if not self._check_idle():
            return

        if not self._check_cooldown():
            return

        self._do_trigger(event)

    # ── 条件检查 ──

    def _check_idle(self) -> bool:
        """检查是否空闲足够久"""
        idle = self._idle_timer.idle_seconds
        if idle < self._idle_threshold_seconds:
            return False
        logger.info(f"空闲条件满足: {idle:.0f}s")
        return True

    def _check_cooldown(self) -> bool:
        with self._lock:
            elapsed = time.time() - self._last_trigger_time
            if elapsed < self._cooldown_seconds:
                return False
            return True

    # ── 触发执行 ──

    def _do_trigger(self, event) -> None:
        change_ratio = event.payload.get("change_ratio", 0)
        idle_minutes = self._idle_timer.idle_minutes
        logger.info(f"触发主动询问: change_ratio={change_ratio:.0%} idle={idle_minutes:.0f}min")

        with self._lock:
            self._last_trigger_time = time.time()
            self._trigger_count += 1
            count = self._trigger_count

        # 后台执行，不阻塞事件总线
        threading.Thread(
            target=self._execute_outreach,
            args=(idle_minutes, event, count),
            daemon=True,
        ).start()

    def _execute_outreach(self, idle_minutes: float, event, trigger_count: int):
        """执行主动询问（在 daemon 线程中）"""
        try:
            session_id, conversation = self._get_session_info()
            if not session_id:
                logger.warning(f"[主动触发 #{trigger_count}] 无活跃 session，跳过")
                return

            # 获取屏幕变化详情
            change_ratio = event.payload.get("change_ratio", 0)
            changed_regions = event.payload.get("changed_regions", [])

            # 获取当前窗口信息
            current_app, current_window = self._get_current_window()

            prompt = self._build_prompt(
                idle_minutes=idle_minutes,
                change_ratio=change_ratio,
                changed_regions=changed_regions,
                current_app=current_app,
                current_window=current_window,
                conversation=conversation,
            )
            response = _run_async(self._call_llm(prompt))
            if not response:
                return

            self._push(session_id, response)
            logger.info(f"[主动触发 #{trigger_count}] 推送完成: {response[:60]}")
        except Exception as e:
            logger.error(f"[主动触发 #{trigger_count}] 失败: {e}")

    def _get_session_info(self):
        """获取最近活跃的 session，附带对话历史"""
        try:
            from modules.thinking.api_stream import get_thinking_system, connection_manager
            system = get_thinking_system()
            if not system.sessions:
                return "", ""

            active_ws = set(connection_manager.active_connections.keys())
            best_sid = ""
            best_ts = 0
            for sid, data in system.sessions.items():
                started = data.get("started_at", 0)
                has_ws = sid in active_ws
                if (has_ws, started) > (best_sid in active_ws, best_ts):
                    best_ts = started
                    best_sid = sid

            # 获取最近对话文本
            conversation = ""
            if best_sid:
                session_data = system.sessions.get(best_sid, {})
                messages = session_data.get("messages", [])
                if messages:
                    recent = messages[-6:]  # 最近 6 条（3 轮）
                    conversation = "\n".join(
                        f"{'用户' if m.get('role') == 'user' else '助手'}: {str(m.get('content', ''))[:200]}"
                        for m in recent
                    )
            return best_sid, conversation
        except Exception:
            return "", ""

    def _get_current_window(self) -> tuple:
        """获取当前活动窗口信息"""
        try:
            from modules.perception.state.world_state import get_world_state
            state = get_world_state()
            return state.active_app or "", state.active_window or ""
        except Exception:
            return "", ""

    def _build_prompt(
        self,
        idle_minutes: float,
        change_ratio: float,
        changed_regions: list,
        current_app: str,
        current_window: str,
        conversation: str,
    ) -> str:
        """构建主动询问 prompt"""
        from config.settings import settings

        # 用户自定义 prompt
        if settings.PROACTIVE_OUTREACH_WORK_PROMPT:
            return settings.PROACTIVE_OUTREACH_WORK_PROMPT.format(
                idle_minutes=idle_minutes,
                change_ratio=change_ratio,
                current_app=current_app or "(未知)",
                current_window=current_window or "(未知)",
                conversation=conversation or "(无历史对话)",
            )

        # 构建屏幕变化描述
        screen_info = f"屏幕发生了 {change_ratio:.0%} 的变化"
        if current_app:
            screen_info += f"，当前应用: {current_app}"
        if current_window:
            screen_info += f"，窗口: {current_window[:50]}"
        if changed_regions:
            region_count = len(changed_regions)
            screen_info += f"，共 {region_count} 个区域变化"

        # 构建完整 prompt
        parts = [
            f"【环境变化】{screen_info}",
            f"【用户状态】已空闲 {idle_minutes:.0f} 分钟",
        ]

        if conversation:
            parts.append(f"【最近对话】\n{conversation}")

        parts.append("\n请根据以上信息，主动询问用户是否需要帮助或提醒用户注意变化。回复简短自然。")

        return "\n".join(parts)

    async def _call_llm(self, prompt: str) -> str:
        """调用大模型（带人格 system prompt）"""
        try:
            from modules.thinking.model_factory import get_model_factory
            from infra.model.base_model import ChatMessage
            factory = get_model_factory()
            factory.ensure_ready()
            client = factory.get_client("large")
            messages = [
                ChatMessage(role="system", content=_OUTREACH_SYSTEM_PROMPT),
                ChatMessage(role="user", content=prompt),
            ]
            response = await client.chat(messages=messages)
            return response.message.content if response and response.message else ""
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            return ""

    def _push(self, session_id: str, text: str) -> None:
        """推送到 WebSocket"""
        try:
            from modules.thinking.api_stream import connection_manager
            from modules.thinking.api_stream import _build_event
            event = _build_event("proactive_outreach", {"content": text})
            connection_manager.send_json(session_id, event)
        except Exception as e:
            logger.error(f"WebSocket 推送失败: {e}")

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "trigger_count": self._trigger_count,
                "idle_seconds": round(self._idle_timer.idle_seconds, 1),
                "change_ratio_threshold": self._change_ratio_threshold,
                "idle_threshold_seconds": self._idle_threshold_seconds,
                "cooldown_seconds": self._cooldown_seconds,
            }


def _run_async(coro):
    """同步执行异步协程（在独立线程中，不阻塞主事件循环）"""
    # 主动触发在 daemon 线程中运行，没有运行中的 event loop，asyncio.run 安全
    return asyncio.run(coro)
