"""会话定时任务 — 每会话独立配置，到点调用逻辑（action 可注册扩展）

- 配置存会话 metadata_json.scheduled_tasks: {"tasks": [{"id","time","enabled","action","prompt"}]}
- 后台线程定时扫描（每 30s）→ 到点（HH:MM ± 5min）→ 执行 action 处理器
- 默认 action "chat"：LLM 生成消息 → 注入会话 → 推送到前端（类似主动搭话）
- 可 register_handler(action, fn) 注册自定义逻辑
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
        for s in sessions:
            sid = s.get("session_id") if isinstance(s, dict) else getattr(s, "session_id", "")
            if not sid:
                continue
            try:
                cfg = repo.get_scheduled_tasks(sid)
                for task in (cfg.get("tasks") or []):
                    if not task.get("enabled"):
                        continue
                    if self._due(sid, task):
                        await self._fire(sid, task)
            except Exception as e:
                logger.debug(f"会话 {sid[:8]} 定时任务失败: {e}")

    def _due(self, session_id: str, task: dict) -> bool:
        now = datetime.now()
        target = task.get("time", "")
        try:
            h, m = map(int, str(target).split(":"))
        except (ValueError, TypeError):
            return False
        now_min = now.hour * 60 + now.minute
        target_min = h * 60 + m
        key = (session_id, task.get("id") or task.get("time"))
        today = now.date().isoformat()
        with self._lock:
            if self._last_fired.get(key) == today:
                return False
            if abs(now_min - target_min) <= JITTER_MINUTES:
                self._last_fired[key] = today
                return True
        return False

    async def _fire(self, session_id: str, task: dict) -> None:
        action = task.get("action", "chat")
        handler = self._handlers.get(action)
        if not handler:
            logger.warning(f"定时任务未知 action: {action} (session={session_id[:8]})")
            return
        try:
            await handler(session_id, task)
            logger.info(f"[定时任务] session={session_id[:8]} action={action} 已执行")
        except Exception as e:
            logger.error(f"[定时任务] action={action} 失败: {e}")

    # ── 默认 action: chat —— LLM 生成消息 → 注入会话 → 推送 ──

    async def _handle_chat(self, session_id: str, task: dict) -> None:
        prompt = task.get("prompt") or "现在是定时任务时间，请自然地向用户说一句话（简短自然，1-2 句）。"
        from infra.model.large_model_client import LargeModelClient
        from infra.model.base_model import ChatMessage
        from modules.database.session_repo import get_session_repo

        repo = get_session_repo()
        history = []
        try:
            msgs = repo.get_recent_messages(session_id, limit=10)
            for m in msgs:
                role = m.get("role")
                content = m.get("content")
                if role in ("user", "assistant") and content:
                    history.append(ChatMessage(role=role, content=content))
        except Exception:
            pass

        system = (
            "你是这个会话的助手，定时任务触发了。"
            "请基于会话历史自然地说一句话（简短，1-2 句），不要提'定时任务'除非合适。"
            "用中文。"
        )
        messages = [ChatMessage(role="system", content=system)] + history + [
            ChatMessage(role="user", content=prompt)
        ]
        try:
            resp = await LargeModelClient().chat(messages=messages)
            text = (resp.message.content or "").strip() if resp and resp.message else ""
        except Exception as e:
            logger.error(f"[定时任务] LLM 生成失败: {e}")
            return
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
