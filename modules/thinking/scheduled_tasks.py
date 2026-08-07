"""会话定时任务 — 每会话独立，到点调用逻辑（action 可注册扩展）

借鉴 DeterminFlow cron：
- 调度类型：HH:MM(每天) / interval(每N分钟) / once(单次) / cron(简化"分 时")
- 配置存会话 metadata_json.scheduled_tasks: {"tasks": [{"id","schedule","enabled","action","prompt"}]}
- 触发时调用 action 处理器（默认 chat：复用主动搭话 call_outreach_llm 生成消息 → 注入会话 → 推送）
- 执行状态记录到任务配置（last_run / last_status）
"""
import asyncio
import threading
import time
from datetime import datetime
from typing import Callable, Optional

from utils.logger import setup_logger

logger = setup_logger("scheduled_tasks")

JITTER_MINUTES = 5
SCAN_INTERVAL = 30


class ScheduledTaskManager:
    def __init__(self):
        self._handlers: dict = {}
        self._last_fired: dict = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.register_handler("chat", self._handle_chat)

    def register_handler(self, action: str, handler: Callable) -> None:
        """注册定时任务逻辑：action -> async handler(session_id, task)"""
        self._handlers[action] = handler

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="scheduled-tasks")
        self._thread.start()
        logger.info("会话定时任务已启动")

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(SCAN_INTERVAL)
            try:
                asyncio.run(self._scan())
            except Exception as e:
                logger.debug(f"定时任务扫描失败: {e}")

    async def _scan(self) -> None:
        from modules.database.session_repo import get_session_repo
        repo = get_session_repo()
        try:
            sessions = repo.get_all_sessions(limit=200)
        except Exception as e:
            logger.debug(f"会话列表获取失败: {e}")
            return
        now = datetime.now()
        for s in sessions:
            sid = s.get("session_id") if isinstance(s, dict) else getattr(s, "session_id", "")
            if not sid:
                continue
            try:
                cfg = repo.get_scheduled_tasks(sid)
                for task in (cfg.get("tasks") or []):
                    if not task.get("enabled"):
                        continue
                    if self._due(sid, task, now):
                        await self._fire(sid, task)
            except Exception as e:
                logger.debug(f"会话 {sid[:8]} 定时任务失败: {e}")

    # ── 调度判定（DeterminFlow 式）──

    def _due(self, session_id: str, task: dict, now: datetime) -> bool:
        sched = task.get("schedule", task.get("time"))
        if isinstance(sched, str) and ":" in str(sched):
            return self._due_daily(session_id, task, sched, now)
        if isinstance(sched, dict):
            kind = sched.get("kind")
            if kind == "interval":
                return self._due_interval(session_id, task, sched.get("every_minutes", 30), now)
            if kind == "once":
                return self._due_once(session_id, task, sched.get("at", ""), now)
            if kind == "cron":
                return self._due_cron(session_id, task, sched.get("expr", ""), now)
        return False

    def _due_daily(self, session_id: str, task: dict, hhmm: str, now: datetime) -> bool:
        try:
            h, m = map(int, str(hhmm).split(":"))
        except (ValueError, TypeError):
            return False
        now_min = now.hour * 60 + now.minute
        target_min = h * 60 + m
        key = (session_id, task.get("id") or str(hhmm))
        today = now.date().isoformat()
        with self._lock:
            if self._last_fired.get(key) == today:
                return False
            if abs(now_min - target_min) <= JITTER_MINUTES:
                self._last_fired[key] = today
                return True
        return False

    def _due_interval(self, session_id: str, task: dict, every_minutes: int, now: datetime) -> bool:
        key = (session_id, task.get("id") or "interval")
        interval_sec = max(1, int(every_minutes or 30)) * 60
        with self._lock:
            last = self._last_fired.get(key)
            if last is None or (now.timestamp() - last) >= interval_sec:
                self._last_fired[key] = now.timestamp()
                return True
        return False

    def _due_once(self, session_id: str, task: dict, at: str, now: datetime) -> bool:
        key = (session_id, task.get("id") or "once")
        with self._lock:
            if self._last_fired.get(key):
                return False
            if not at:
                return False
            # "HH:MM"（今天）± jitter
            if ":" in at and "-" not in at:
                try:
                    h, m = map(int, at.split(":"))
                except (ValueError, TypeError):
                    return False
                now_min = now.hour * 60 + now.minute
                target_min = h * 60 + m
                if abs(now_min - target_min) <= JITTER_MINUTES:
                    self._last_fired[key] = now.timestamp()
                    return True
                return False
            # ISO 日期时间：单次执行
            try:
                target = datetime.fromisoformat(at)
                if now >= target:
                    self._last_fired[key] = now.timestamp()
                    return True
            except (ValueError, TypeError):
                return False
        return False

    def _due_cron(self, session_id: str, task: dict, expr: str, now: datetime) -> bool:
        key = (session_id, task.get("id") or "cron")
        minute_mark = now.strftime("%Y%m%d%H%M")
        try:
            parts = str(expr or "").strip().split()
            if len(parts) == 2:
                min_pat, hour_pat = parts[0], parts[1]
                min_ok = min_pat == "*" or str(now.minute) == min_pat
                hour_ok = hour_pat == "*" or str(now.hour) == hour_pat
                with self._lock:
                    if min_ok and hour_ok and self._last_fired.get(key) != minute_mark:
                        self._last_fired[key] = minute_mark
                        return True
        except Exception:
            pass
        return False

    # ── 执行 ──

    async def _fire(self, session_id: str, task: dict) -> None:
        action = task.get("action", "chat")
        handler = self._handlers.get(action)
        if not handler:
            logger.warning(f"定时任务未知 action: {action} (session={session_id[:8]})")
            self._mark_run(session_id, task, "error")
            return
        try:
            await handler(session_id, task)
            self._mark_run(session_id, task, "success")
            logger.info(f"[定时任务] session={session_id[:8]} action={action} 已执行")
        except Exception as e:
            self._mark_run(session_id, task, "error")
            logger.error(f"[定时任务] action={action} 失败: {e}")

    def _mark_run(self, session_id: str, task: dict, status: str) -> None:
        try:
            from modules.database.session_repo import get_session_repo
            repo = get_session_repo()
            cfg = repo.get_scheduled_tasks(session_id)
            for t in (cfg.get("tasks") or []):
                if t.get("id") == task.get("id"):
                    t["last_run"] = datetime.now().strftime("%m-%d %H:%M")
                    t["last_status"] = status
                    break
            repo.set_scheduled_tasks(session_id, cfg)
        except Exception:
            pass

    # ── 默认 action: chat —— 复用主动搭话 LLM 逻辑 → 注入会话 → 推送 ──

    async def _handle_chat(self, session_id: str, task: dict) -> None:
        prompt = task.get("prompt") or "现在是定时任务时间，请自然地向用户说一句话（简短自然，1-2 句）。"
        from modules.perception.trigger import call_outreach_llm
        text = await call_outreach_llm(prompt, session_id)
        if not text:
            return
        await self._push(session_id, text)

    async def _push(self, session_id: str, text: str) -> None:
        msg_id = ""
        try:
            from modules.database.session_repo import get_session_repo
            msg_id = get_session_repo().save_message(session_id, "assistant", text)
        except Exception:
            pass
        try:
            from modules.thinking.api_stream import connection_manager, _build_event
            event = _build_event(
                session_id=session_id,
                msg_type="proactive",
                event="scheduled_task",
                content=text,
                role="assistant",
                data={"message_id": msg_id},
            )
            for sid in list(connection_manager.active_connections.keys()):
                connection_manager.send_json_from_thread(sid, event)
        except Exception as e:
            logger.debug(f"[定时任务] 推送失败: {e}")


_manager: Optional[ScheduledTaskManager] = None


def get_task_manager() -> ScheduledTaskManager:
    global _manager
    if _manager is None:
        _manager = ScheduledTaskManager()
    return _manager
