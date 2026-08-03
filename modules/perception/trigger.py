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
import threading
import time
from typing import Any, Dict

from utils.logger import setup_logger

logger = setup_logger("perception_proactive_trigger")


def _build_outreach_system_prompt() -> str:
    """构建主动搭话的 system prompt — 复用总指挥人格，跳过工具规则"""
    from config.prompts.composer import PromptComposer, PromptRequest
    from config.settings import settings

    composer = PromptRequest(
        tier="large",
        role="orchestrator",
        mode=settings.effective_execution_mode,
    )
    # 构建完整 system prompt
    full_prompt = PromptComposer().build_system(composer)

    # 需要跳过的段落关键词
    skip_keywords = ["【工具调用规则】", "【可委托的主管】", "【工具使用】"]

    # 需要过滤的行关键词
    skip_line_keywords = ["tools_search", "delegate_task", "编造、推测或假设存在未列出的工具"]

    # 去除工具相关段落和行
    lines = full_prompt.split("\n")
    filtered = []
    skip_section = False
    for line in lines:
        # 检查是否进入需要跳过的段落
        if any(kw in line for kw in skip_keywords):
            skip_section = True
            continue
        # 检查是否离开跳过段落
        if skip_section:
            if line.startswith("【"):
                skip_section = False
            else:
                continue
        # 跳过包含工具关键词的行
        if any(kw in line for kw in skip_line_keywords):
            continue
        filtered.append(line)

    # 在末尾追加搭话专用指令
    outreach_instruction = """
【主动搭话模式】
你正在主动关心用户。用户已空闲一段时间，屏幕发生了变化。
- 回复简短自然，1-2 句话
- 根据屏幕信息和对话历史，判断用户可能需要什么帮助
- 如果没有明确需要帮助的场景，简单问候或提醒休息
- 不要调用任何工具，直接回复"""

    return "\n".join(filtered) + outreach_instruction


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
        logger.debug(f"空闲条件满足: {idle:.0f}s")
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

        with self._lock:
            elapsed = time.time() - self._last_trigger_time
            if elapsed < self._cooldown_seconds:
                logger.debug(f"跳过重复触发: 距上次仅 {elapsed:.0f}s")
                return
            self._last_trigger_time = time.time()
            self._trigger_count += 1
            count = self._trigger_count

        logger.info(f"触发主动询问 #{count}: change_ratio={change_ratio:.0%} idle={idle_minutes:.0f}min")

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
            response = self._run_in_main_loop(self._call_llm(prompt, session_id))
            if not response:
                return

            self._push(session_id, response)
            logger.info(f"[主动触发 #{trigger_count}] 推送完成: {response[:60]}")
        except Exception as e:
            logger.error(f"[主动触发 #{trigger_count}] 失败: {e}")
            try:
                self._push_error(session_id, f"[主动搭话失败] {e}")
            except Exception:
                pass

    def _push_error(self, session_id: str, text: str) -> None:
        """推送错误事件到活跃 WebSocket（前端 toast 展示）"""
        try:
            from modules.thinking.api_stream import connection_manager, _build_event
            event = _build_event(
                session_id=session_id,
                msg_type="error",
                event="proactive_error",
                content=text,
                role="system",
            )
            sent = False
            for sid in list(connection_manager.active_connections.keys()):
                if connection_manager.send_json_from_thread(sid, event):
                    sent = True
            if not sent:
                logger.warning(f"[主动触发] 无活跃连接，无法显示错误: {text[:60]}")
        except Exception as e:
            logger.error(f"主动错误推送失败: {e}")

    def _run_in_main_loop(self, coro, timeout: float = 120.0):
        """提交协程到主事件循环执行。

        模型 client 的 aiohttp session 池化绑定主 loop；若在 daemon 线程里
        asyncio.run 新建 loop 调用，session 跨 loop 复用会报
        'Timeout context manager should be used inside a task'。
        统一提交到主 loop 执行可复用 session，且 asyncio.Lock 等资源同 loop 安全。
        """
        try:
            from modules.thinking.api_stream import connection_manager, _main_event_loop
            loop = connection_manager._loop or _main_event_loop
            if loop and not loop.is_closed():
                return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=timeout)
        except Exception:
            pass
        return _run_async(coro)

    def _get_session_info(self):
        """选择主动搭话的目标会话

        规则（按你的预期）：
        1. 目标会话必须有真实消息（用户真正聊过的会话，不含空/voice 残留会话）
        2. 优先选当前正有 WebSocket 连接的会话（即用户当前正在看的会话）
        3. 其次选最近一次有过对话的会话（上次对话）
        4. 如果从来没有过任何对话 → 返回空，上层直接跳过，不启动主动搭话
        """
        try:
            from modules.thinking.api_stream import get_thinking_system
            system = get_thinking_system()
            if not system.sessions:
                return "", ""

            # 只保留有真实消息的会话（真正聊过，不含空/voice 残留会话）
            candidates = {
                sid: data for sid, data in system.sessions.items()
                if data.get("messages")
            }
            if not candidates:
                # 从来没有过对话 → 不启动主动搭话
                return "", ""

            # 自动选择"上一次对话"的会话：最近一次有过对话的（消息时间戳最新）
            def score(sid, data):
                msgs = data.get("messages") or []
                return (msgs[-1].get("timestamp", 0) or 0) if msgs else 0

            best_sid = max(candidates, key=lambda sid: score(sid, candidates[sid]))
            messages = candidates[best_sid]["messages"]
            recent = messages[-6:]  # 最近 6 条（3 轮）
            # 与 agent 模式一致：【对话历史】[role]: content 格式
            conversation = "\n".join(
                f"[{m.get('role')}]: {str(m.get('content', ''))[:200]}"
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

    async def _call_llm(self, prompt: str, session_id: str = "") -> str:
        """调用大模型（复用总指挥人格，单次调用，注入感知/内心独白/时间感知上下文）"""
        try:
            from modules.thinking.model_factory import get_model_factory
            from infra.model.base_model import ChatMessage
            factory = get_model_factory()
            factory.ensure_ready()
            client = factory.get_client("large")
            system_prompt = _build_outreach_system_prompt()

            # 注入与主流程一致的上下文（调用原有函数，不重复实现）
            extras = []
            # 时间感知 + 用户身份
            try:
                extras.append(self._build_time_text())
            except Exception:
                pass
            # 内心独白（良知引导）
            try:
                from modules.thinking.probes.probe_tools import _session_guidance
                g = _session_guidance.get(("large_primary", session_id), {})
                inner = g.get("inner_thoughts", "")
                if inner:
                    extras.append(f"【你回忆起的过往经验】\n{inner}")
            except Exception:
                pass
            # 感知上下文
            try:
                from modules.thinking.context.sources.perception_source import PerceptionSource
                frag = await PerceptionSource().collect()
                if frag and frag.content:
                    extras.append(frag.content)
            except Exception:
                pass
            # 事件记忆（按对话模式分流：chatonly 用 backend 的记忆系统，agent 用 modules）
            try:
                from modules.thinking.chat_gateway import _resolve_mode
                if _resolve_mode() == "chatonly":
                    from backend.memory.event_retrieval import get_event_retrieval
                else:
                    from modules.memory.event_retrieval import get_event_retrieval
                events = await get_event_retrieval().retrieve(query=prompt, max_results=3, threshold=0.10)
                if events:
                    lines = ["【曾经发生的事】", "（以下为过去的事件记忆，仅供参考，不要把过去任务当作当前任务执行）"]
                    for i, ev in enumerate(events, 1):
                        date = str(ev.time or "")[:10] or "未知日期"
                        lines.append(f"  [{i}] (日期={date}) {str(ev.fact)[:150]}")
                    extras.append("\n".join(lines))
            except Exception:
                pass
            if extras:
                system_prompt = f"{system_prompt}\n\n" + "\n\n".join(extras)

            messages = [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=prompt),
            ]
            response = await client.chat(messages=messages)
            return response.message.content if response and response.message else ""
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            raise

    def _build_time_text(self) -> str:
        """时间感知 + 用户身份（与主流程 _build_time_context 一致的格式）"""
        from datetime import datetime
        from config.settings import settings as _cfg
        now = datetime.now()
        parts = [f"【当前时间】{now.strftime('%Y-%m-%d %H:%M')}"]
        user_name = getattr(_cfg, "USER_NAME", "用户") or "用户"
        parts.append(f"【对话对象】{user_name}")
        return "\n".join(parts)

    def _push(self, session_id: str, text: str) -> None:
        """推送到所有活跃 WebSocket，并持久化到会话历史（AI 上下文同步可见）"""
        try:
            from modules.thinking.api_stream import connection_manager, _build_event
            from modules.thinking.api_stream import get_thinking_system

            # 1. 持久化到会话（历史 + AI 上下文），获取消息 id
            msg_id = ""
            try:
                system = get_thinking_system()
                if session_id in system.sessions:
                    # 必须提交到主事件循环：_append_message 用主 loop 的 asyncio.Lock，
                    # 在 daemon 线程里 asyncio.run 新 loop 直接调用会跨 loop 报错 → 无法注入会话。
                    # 优先用 connection_manager._loop，无活跃连接时回退到 lifespan 记录的主 loop。
                    from modules.thinking.api_stream import connection_manager, _main_event_loop
                    loop = connection_manager._loop or _main_event_loop
                    if loop and not loop.is_closed():
                        fut = asyncio.run_coroutine_threadsafe(
                            system._append_message(session_id, "assistant", text), loop)
                        msg_id = fut.result(timeout=10)
                    else:
                        msg_id = _run_async(system._append_message(session_id, "assistant", text))
            except Exception as e:
                logger.error(f"主动消息持久化失败: {e}")

            # 2. 构造事件
            event = _build_event(
                session_id=session_id,
                msg_type="proactive",
                event="proactive_outreach",
                content=text,
                role="assistant",
                data={"message_id": msg_id},
            )

            # 3. 广播到所有活跃连接（前端 / TUI 都能收到）
            sent_any = False
            for sid in list(connection_manager.active_connections.keys()):
                if connection_manager.send_json_from_thread(sid, event):
                    sent_any = True
            if not sent_any:
                logger.warning(
                    f"[主动触发] 无活跃 WebSocket 连接，消息已存入会话历史 "
                    f"(session={session_id[:8]})"
                )
        except Exception as e:
            logger.error(f"主动消息推送失败: {e}")

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
    async def _run_task_wrapped():
        # 必须用 Task 包装：asyncio.run 直接 run_until_complete 协程时不在 Task 上下文内，
        # 内部 aiohttp 的 asyncio.timeout 会报 'Timeout context manager should be used inside a task'
        return await asyncio.create_task(coro)
    return asyncio.run(_run_task_wrapped())
