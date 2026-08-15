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
from typing import Any, Dict

from utils.logger import setup_logger

logger = setup_logger("perception_proactive_trigger")


# 统一前端推送出口的握手函数（迁移自本模块，详见 modules/thinking/frontend_channel.py）
from modules.thinking.frontend_channel import confirm_frontend_connection  # noqa: E402,F401



def outreach_trigger_allowed() -> bool:
    """主动搭话三层闸门（第 1、2 层）——所有主动消息触发源（主路径/感知触发/定时任务）统一检查

    1. 全局总开关 PROACTIVE_OUTREACH_ENABLED 必须开启；
    2. 至少一个会话在设置里【单独开启】主动搭话（metadata.outreach.enabled=true）。

    第 3 层（满足具体规则标准）由各触发源的规则判定完成。
    """
    # 复用主路径同一套判定（全局开关 + 会话 enabled），保证旁路与主路径永不漂移
    try:
        return bool(ProactiveTrigger()._get_enabled_outreach_sessions())
    except Exception:
        return False


def _build_outreach_system_prompt(role: str = "orchestrator", tier: str = "large") -> str:
    """构建主动搭话的 system prompt — 默认复用总指挥人格，可指定角色（roles.yaml），跳过工具规则"""
    from config.prompts.composer import PromptComposer, PromptRequest
    from config.settings import settings

    composer = PromptRequest(
        tier=tier,
        role=role,
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

    def __init__(self):
        self._idle_timer = IdleTimer()
        self._session_last_trigger: Dict[str, float] = {}       # 综合冷却（会话级，距上次任意主动搭话）
        self._screen_last_trigger: Dict[str, float] = {}        # screen 规则触发后冷却
        self._last_rule_check: Dict[str, Dict[str, float]] = {}  # 各规则 check_interval 判定跟踪
        self._trigger_count = 0
        self._lock = threading.Lock()
        self._sub_id: str = ""
        self._event_bus = None
        self._timer_task = None

    # ── 生命周期 ──

    def start(self, event_bus) -> None:
        from modules.perception.events.types import PerceptionEventType
        self._event_bus = event_bus
        self._sub_id = event_bus.subscribe(
            PerceptionEventType.SCREEN_DIFF,
            handler=self._on_screen_diff,
        )
        # 定时判定（schedule / idle / time_windows 规则），每 5s 检查
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            self._timer_task = loop.create_task(self._check_loop())
        except Exception as e:
            logger.warning(f"主动搭话定时判定未启动: {e}")
        logger.info("主动触发已启动（会话级规则：schedule/screen/idle/time_windows，最多 5 会话）")

    def stop(self) -> None:
        if self._event_bus and self._sub_id:
            self._event_bus.unsubscribe(self._sub_id)
            self._sub_id = ""
        if self._timer_task:
            try:
                self._timer_task.cancel()
            except Exception:
                pass
            self._timer_task = None
        logger.info("主动触发已停止")

    def notify_activity(self) -> None:
        """用户有活动时调用，重置空闲计时"""
        self._idle_timer.notify_activity()

    # ── 事件处理 ──

    def _on_screen_diff(self, event) -> None:
        """SCREEN_DIFF 事件：按各 enabled 会话的 screen 规则判定"""
        change_ratio = event.payload.get("change_ratio", 0)
        changed_regions = event.payload.get("changed_regions", [])
        if not self._qt_active():
            return
        for session_id, cfg in self._get_enabled_outreach_sessions().items():
            try:
                screen = cfg.get("screen") or {}
                if not screen.get("enabled", True):
                    continue
                if change_ratio < screen.get("change_ratio", 1.0):
                    continue
                if random.random() > screen.get("probability", 1.0):
                    continue
                if not self._cooldown_ok(session_id, cfg):
                    continue
                if not self._screen_cooldown_ok(session_id, screen):
                    continue
                if not self._rule_ready(session_id, "screen", screen.get("check_interval_seconds", 30)):
                    continue
                self._run_in_main_loop(self._try_outreach(session_id, "screen", change_ratio, changed_regions))
            except Exception as e:
                logger.debug(f"screen 规则判定失败: {e}")

    # ── 触发执行 ──

    # ── 定时判定（schedule / idle / time_windows 规则）──

    async def _check_loop(self) -> None:
        """后台定时循环：每 5s 检查所有 enabled 会话的规则"""
        while True:
            try:
                await asyncio.sleep(5)
                await self._run_periodic_check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"主动搭话定时判定失败: {e}")

    async def _run_periodic_check(self) -> None:
        """按各会话规则（schedule/idle/time_windows）做一次判定"""
        if not self._qt_active():
            return
        for session_id, cfg in self._get_enabled_outreach_sessions().items():
            try:
                if not self._cooldown_ok(session_id, cfg):
                    continue
                # schedule：定点（±jitter 窗口内），每次判定
                if self._check_schedule(cfg):
                    await self._try_outreach(session_id, "schedule")
                    continue
                # idle：空闲 + 概率，按该规则 check_interval
                idle_cfg = cfg.get("idle") or {}
                if self._rule_ready(session_id, "idle", idle_cfg.get("check_interval_seconds", 60)) \
                   and self._check_idle_rule(idle_cfg):
                    await self._try_outreach(session_id, "idle")
                    continue
                # time_windows：时段 + 概率，按判定间隔
                if self._rule_ready(session_id, "time_windows", 30) and self._check_time_windows(cfg):
                    await self._try_outreach(session_id, "time_window")
            except Exception as e:
                logger.debug(f"会话 {session_id[:8]} 规则判定失败: {e}")

    async def _try_outreach(self, session_id: str, reason: str,
                            change_ratio: float = 0, changed_regions: list = None) -> None:
        """触发一次主动搭话：走统一出口（握手 → LLM → 持久化 → 推送）"""
        try:
            cfg = self._get_session_outreach_config(session_id)
            if not self._cooldown_ok(session_id, cfg):
                return

            # 该会话的会话记忆（历史对话）作为搭话上下文
            conversation = self._get_session_conversation(session_id)
            current_app, current_window = self._get_current_window()
            prompt = self._build_prompt(
                idle_minutes=self._idle_timer.idle_minutes,
                change_ratio=change_ratio,
                changed_regions=changed_regions or [],
                current_app=current_app,
                current_window=current_window,
                conversation=conversation,
            )
            # 统一出口：握手确认（前端不可达则跳过 LLM）→ 生成 → 持久化 → 推送
            from modules.thinking.frontend_channel import generate_and_push
            response = await generate_and_push(
                session_id,
                lambda: self._call_llm(prompt, session_id),
                msg_type="proactive",
                event="proactive_outreach",
                role="assistant",
            )
            if not response:
                return

            self._trigger_count += 1
            count = self._trigger_count

            # 记录主动搭话历史（追溯/统计）
            try:
                from modules.database.proactive_repo import save_proactive_log
                save_proactive_log(session_id, reason, response[:500])
            except Exception:
                pass
            # 任意主动搭话触发 → 更新综合冷却；screen 额外更新该规则冷却
            with self._lock:
                self._session_last_trigger[session_id] = time.time()
                if reason == "screen":
                    self._screen_last_trigger[session_id] = time.time()
            logger.info(f"[主动触发 #{count}] ({reason}) 推送完成: {response[:60]}")
        except Exception as e:
            logger.error(f"[主动触发] 失败: {e}")
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

    def _get_session_outreach_config(self, session_id: str) -> dict:
        """读取会话级主动搭话配置（存 chat_sessions.metadata_json.outreach）"""
        try:
            from modules.database.session_repo import get_session_repo
            return get_session_repo().get_outreach_config(session_id)
        except Exception:
            return {}

    def _qt_active(self) -> bool:
        """Qt 端开着（有活跃 WS 连接）是主动搭话的前提"""
        try:
            from modules.thinking.api_stream import connection_manager
            return bool(connection_manager.active_connections)
        except Exception:
            return False

    def _get_enabled_outreach_sessions(self) -> Dict[str, dict]:
        """获取本次应触发的会话及配置

        强制总闸：全局总开关（PROACTIVE_OUTREACH_ENABLED）关则全部不触发。
        触发前提：该会话必须在设置里【单独开启】（metadata.outreach.enabled=true）。
        全局默认规则只作为「已单独开启、但未细配具体规则」会话的默认模板，
        不会自动应用到未开启的会话。
        """
        from config.settings import settings
        if not getattr(settings, "PROACTIVE_OUTREACH_ENABLED", True):
            return {}
        default = self._get_global_default_rules()
        try:
            from modules.database.session_repo import get_session_repo
            result: Dict[str, dict] = {}
            for s in get_session_repo().get_all_sessions(limit=100):
                cfg = (s.get("metadata") or {}).get("outreach") or {}
                if not cfg.get("enabled"):
                    # 未在设置里单独开启 → 即使全局默认开启也不触发
                    continue
                has_rules = bool(
                    cfg.get("schedule") or cfg.get("screen")
                    or cfg.get("idle") or cfg.get("time_windows_enabled")
                )
                if has_rules:
                    result[s["session_id"]] = cfg
                elif default.get("enabled"):
                    # 会话单独开启但未配具体规则 → 用全局默认规则作为模板
                    result[s["session_id"]] = default
                else:
                    result[s["session_id"]] = cfg
            return result
        except Exception:
            return {}

    @staticmethod
    def _get_global_default_rules() -> dict:
        """读取全局默认主动搭话规则（PROACTIVE_OUTREACH_DEFAULT，JSON）"""
        try:
            import json
            from config.settings import settings
            raw = getattr(settings, "PROACTIVE_OUTREACH_DEFAULT", "{}") or "{}"
            d = json.loads(raw) if isinstance(raw, str) else (raw or {})
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _get_session_conversation(self, session_id: str) -> str:
        """该会话的会话记忆（历史对话）作为搭话上下文"""
        try:
            from modules.database.session_repo import get_session_repo
            msgs = get_session_repo().get_recent_messages(session_id, limit=6)
            real = [m for m in msgs if m.get("role") in ("user", "assistant") and m.get("content")]
            return "\n".join(
                f"[{m.get('role')}]: {str(m.get('content', ''))[:200]}" for m in real
            )
        except Exception:
            return ""

    def _cooldown_ok(self, session_id: str, cfg: dict) -> bool:
        """综合冷却：距上次该会话任意主动搭话 >= cooldown_minutes 才允许触发"""
        cooldown = cfg.get("cooldown_minutes")
        if cooldown is None:
            from config.settings import settings as _s
            cooldown = getattr(_s, "PROACTIVE_OUTREACH_COOLDOWN_MINUTES", 15)
        cooldown = 15 if cooldown is None else int(cooldown)
        with self._lock:
            last = self._session_last_trigger.get(session_id, 0.0)
            return time.time() - last >= cooldown * 60

    def _screen_cooldown_ok(self, session_id: str, screen: dict) -> bool:
        """screen 规则触发后冷却（与综合冷却同时满足才触发）"""
        cd = screen.get("cooldown_minutes") or 30
        with self._lock:
            last = self._screen_last_trigger.get(session_id, 0.0)
            return time.time() - last >= cd * 60

    def _rule_ready(self, session_id: str, rule_key: str, interval_seconds: int) -> bool:
        """按规则判定间隔（check_interval_seconds）控制判定频率"""
        with self._lock:
            now = time.time()
            checks = self._last_rule_check.setdefault(session_id, {})
            last = checks.get(rule_key, 0.0)
            if now - last < interval_seconds:
                return False
            checks[rule_key] = now
            return True

    def _check_schedule(self, cfg: dict) -> bool:
        """定点发送：schedule.enabled 且当前在 schedule.time ± jitter 内则触发"""
        sched = cfg.get("schedule")
        if not sched or not sched.get("enabled", True) or not sched.get("time"):
            return False
        from datetime import datetime, timedelta
        target = str(sched["time"]).strip()
        try:
            hh, mm = map(int, target.split(":"))
        except Exception:
            return False
        now = datetime.now()
        target_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        jitter = int(sched.get("jitter_minutes") or 0)
        window = timedelta(minutes=abs(jitter))
        return target_dt - window <= now <= target_dt + window

    def _check_idle_rule(self, idle_cfg: dict) -> bool:
        """空闲触发：idle.enabled 且空闲 >= idle_minutes，按 probability 概率触发"""
        if not idle_cfg or not idle_cfg.get("enabled", True):
            return False
        if self._idle_timer.idle_minutes < (idle_cfg.get("idle_minutes") or 30):
            return False
        prob = idle_cfg.get("probability")
        return random.random() < (1.0 if prob is None else float(prob))

    def _check_time_windows(self, cfg: dict) -> bool:
        """时段触发：time_windows_enabled 且当前在某 time_window 内，按该窗口概率触发

        用分钟数精确比较（字符串比较在跨午夜/整点边界有误）：end < start 视为跨午夜窗口。
        """
        if not cfg.get("time_windows_enabled", True):
            return False
        windows = cfg.get("time_windows")
        if not isinstance(windows, list) or not windows:
            return False
        from datetime import datetime
        now = datetime.now()
        cur = now.hour * 60 + now.minute
        for w in windows:
            start, end = w.get("start", ""), w.get("end", "")
            try:
                sh, sm = map(int, str(start).split(":"))
                eh, em = map(int, str(end).split(":"))
            except (ValueError, TypeError):
                continue
            s = sh * 60 + sm
            e = eh * 60 + em
            if s <= e:
                inside = s <= cur <= e
            else:
                inside = cur >= s or cur <= e  # 跨午夜（如 22:00-02:00）
            if inside:
                prob = w.get("probability")
                return random.random() < (1.0 if prob is None else float(prob))
        return False

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
        """调用大模型（统一走 call_outreach_llm —— 与定时任务等共用同一段 LLM 调用代码）"""
        return await call_outreach_llm(prompt, session_id)

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
                else:
                    # chatonly 等非 agent 内存会话：直接落 DB（会话记忆由 DB 恢复，前端可追溯）
                    try:
                        from modules.database.session_repo import get_session_repo
                        msg_id = get_session_repo().save_message(session_id, "assistant", text)
                    except Exception:
                        pass
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
                "session_last_trigger_count": len(self._session_last_trigger),
                "active_sessions": len(self._get_enabled_outreach_sessions()),
            }


def _run_async(coro):
    """同步执行异步协程（在独立线程中，不阻塞主事件循环）"""
    async def _run_task_wrapped():
        # 必须用 Task 包装：asyncio.run 直接 run_until_complete 协程时不在 Task 上下文内，
        # 内部 aiohttp 的 asyncio.timeout 会报 'Timeout context manager should be used inside a task'
        return await asyncio.create_task(coro)
    return asyncio.run(_run_task_wrapped())


def run_in_main_loop(coro, timeout: float = 120.0):
    """提交协程到主事件循环执行（跨线程安全）。

    模型 client 的 aiohttp session 池化绑定主 loop；在 daemon 线程里 asyncio.run
    新建 loop 调用会报 'Event loop is closed'/'Timeout context manager should be
    used inside a task'。统一提交到主 loop 执行可复用 session。
    主 loop 不可用时回退独立线程执行。
    """
    try:
        from modules.thinking.api_stream import connection_manager, _main_event_loop
        loop = connection_manager._loop or _main_event_loop
        if loop and not loop.is_closed():
            return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=timeout)
    except Exception:
        pass
    return _run_async(coro)


async def call_outreach_llm(prompt: str, session_id: str = "", role: str = None, tier: str = "large") -> str:
    """调用大模型（与主动搭话同一逻辑：人格 + 时间/感知/记忆/内心独白上下文）

    - role=None → 默认总指挥人格（orchestrator）
    - role 指定 → 使用 roles.yaml 对应角色人格（如 code_writer/tester/ui_designer...）
    供主动搭话、定时任务等复用——保证"调用同一个大模型 API 代码部分"。
    """
    try:
        from datetime import datetime
        from modules.thinking.model_factory import get_model_factory
        from infra.model.base_model import ChatMessage
        from config.settings import settings as _cfg
        factory = get_model_factory()
        factory.ensure_ready()
        client = factory.get_client("large")
        system_prompt = _build_outreach_system_prompt(role=role or "orchestrator", tier=tier)

        extras = []
        try:
            extras.append(f"【当前时间】{datetime.now().strftime('%Y-%m-%d %H:%M')}")
            extras.append(f"【对话对象】{getattr(_cfg, 'USER_NAME', '用户') or '用户'}")
        except Exception:  # pragma: no cover — datetime.now()/USER_NAME 读取为 stdlib 属性访问，实际不可失败
            pass
        try:
            from modules.thinking.probes.probe_tools import _session_guidance
            g = _session_guidance.get(("large_primary", session_id), {})
            inner = g.get("inner_thoughts", "")
            if inner:
                extras.append(f"【你回忆起的过往经验】\n{inner}")
        except Exception:
            pass
        try:
            from modules.thinking.context.sources.perception_source import PerceptionSource
            frag = await PerceptionSource().collect()
            if frag and getattr(frag, "content", ""):  # type: ignore[arg-type]  # 动态协议访问
                extras.append(frag.content)
        except Exception:
            pass
        try:
            from modules.memory.event_retrieval import get_event_retrieval
            events = await get_event_retrieval().retrieve(query=prompt, max_results=3, threshold=0.10)
            if events:
                lines = ["【曾经发生的事】", "（以下为过去的事件记忆，仅供参考，不要把过去任务当作当前任务执行）"]
                for i, ev in enumerate(events, 1):
                    date = str(getattr(ev, "time", "") or "")[:10] or "未知日期"
                    lines.append(f"  [{i}] (日期={date}) {str(getattr(ev, 'fact', ''))[:150]}")
                extras.append("\n".join(lines))
        except Exception:
            pass
        if extras:  # pragma: no cover — extras 首元素（当前时间）恒被先追加，此处不可能为空
            system_prompt = f"{system_prompt}\n\n" + "\n\n".join(extras)

        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=prompt),
        ]
        response = await client.chat(messages=messages)
        return (response.message.content or "").strip() if response and response.message else ""
    except Exception as e:
        logger.error(f"大模型调用失败: {e}")
        return ""
