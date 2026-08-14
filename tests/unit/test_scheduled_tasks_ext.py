"""scheduled_tasks 扩展测试：start/stop / scan / fire / chat handler"""
import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import modules.thinking.scheduled_tasks as st_mod
from modules.thinking.scheduled_tasks import ScheduledTaskManager, get_task_manager


def _mgr():
    m = ScheduledTaskManager.__new__(ScheduledTaskManager)
    m._last_fired = {}
    m._handlers = {}
    m._lock = threading.RLock()
    m._stop = threading.Event()
    m._thread = None
    return m


# ── start / stop / loop ────────────────────────────────────────────────

def test_start_and_stop(monkeypatch):
    m = _mgr()
    class FakeThread:
        def __init__(self, *a, **k):
            self.started = False
        def start(self):
            self.started = True
        def is_alive(self):
            return True
    fake = FakeThread()
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: fake)
    m.start()
    assert fake.started is True
    m.start()  # 已启动不再重复
    m.stop()
    assert m._stop.is_set()


# ── _due 分发 ──────────────────────────────────────────────────────────

def test_due_dispatch():
    from datetime import datetime
    m = _mgr()
    now = datetime(2026, 8, 9, 10, 30)
    assert m._due("s", {"id": "d1", "schedule": "10:31"}, now)
    assert m._due("s", {"id": "i1", "schedule": {"kind": "interval", "every_minutes": 1}}, now)
    assert m._due("s", {"id": "o1", "schedule": {"kind": "once", "at": "10:31"}}, now)
    assert m._due("s", {"id": "c1", "schedule": {"kind": "cron", "expr": "30 10"}}, now)
    assert m._due("s", {"id": "u1", "schedule": {"kind": "bogus"}}, now) is False
    assert m._due("s", {"id": "u2"}, now) is False


def test_due_daily_invalid():
    from datetime import datetime
    m = _mgr()
    now = datetime(2026, 8, 9, 10, 30)
    assert m._due_daily("s", {"id": "x"}, "bad", now) is False


def test_due_once_iso_and_no_at():
    from datetime import datetime, timedelta
    m = _mgr()
    now = datetime(2026, 8, 9, 10, 30)
    assert m._due_once("s", {"id": "x"}, "", now) is False
    past = (now - timedelta(minutes=1)).isoformat()
    assert m._due_once("s", {"id": "iso"}, past, now) is True
    assert m._due_once("s", {"id": "iso"}, past, now) is False  # 已触发
    assert m._due_once("s", {"id": "bad"}, "not-a-date", now) is False


def test_due_cron_bad_expr():
    from datetime import datetime
    m = _mgr()
    now = datetime(2026, 8, 9, 10, 30)
    assert m._due_cron("s", {"id": "c"}, "bad expr!!", now) is False


# ── _scan ──────────────────────────────────────────────────────────────

async def test_scan_runs_tasks(monkeypatch):
    m = _mgr()
    repo = MagicMock()
    repo.get_all_sessions = MagicMock(return_value=[{"session_id": "s1"}, "s2"])
    repo.get_scheduled_tasks = MagicMock(return_value={
        "tasks": [
            {"id": "t1", "enabled": True, "schedule": "10:31", "action": "chat"},
            {"id": "t2", "enabled": False, "schedule": "10:31"},
        ],
    })
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: repo)
    m._due = MagicMock(side_effect=lambda sid, task, now: task["id"] == "t1")
    m._fire = AsyncMock()
    await m._scan()
    m._fire.assert_awaited_once()


async def test_scan_session_error(monkeypatch):
    m = _mgr()
    repo = MagicMock()
    repo.get_all_sessions = MagicMock(return_value=[{"session_id": "s1"}])
    repo.get_scheduled_tasks = MagicMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: repo)
    await m._scan()  # 不抛异常


async def test_scan_list_error(monkeypatch):
    m = _mgr()
    repo = MagicMock()
    repo.get_all_sessions = MagicMock(side_effect=RuntimeError("list fail"))
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: repo)
    await m._scan()  # 返回 None


# ── _fire ──────────────────────────────────────────────────────────────

async def test_fire_unknown_action(monkeypatch):
    m = _mgr()
    m._mark_run = MagicMock()
    await m._fire("s", {"id": "x", "action": "nope"})
    m._mark_run.assert_called_once_with("s", {"id": "x", "action": "nope"}, "error")


async def test_fire_success_and_skipped(monkeypatch):
    m = _mgr()
    m._handlers = {"chat": AsyncMock(return_value="ok")}
    m._mark_run = MagicMock()
    await m._fire("s", {"id": "a", "action": "chat"})
    m._mark_run.assert_called_once_with("s", {"id": "a", "action": "chat"}, "success")
    m._handlers = {"chat": AsyncMock(return_value="skipped")}
    await m._fire("s", {"id": "b", "action": "chat"})
    m._mark_run.assert_called_with("s", {"id": "b", "action": "chat"}, "skipped")


async def test_fire_handler_error(monkeypatch):
    m = _mgr()
    m._handlers = {"chat": AsyncMock(side_effect=RuntimeError("boom"))}
    m._mark_run = MagicMock()
    await m._fire("s", {"id": "c", "action": "chat"})
    m._mark_run.assert_called_with("s", {"id": "c", "action": "chat"}, "error")


def test_mark_run(monkeypatch):
    m = _mgr()
    repo = MagicMock()
    cfg = {"tasks": [{"id": "t1"}, {"id": "t2"}]}
    repo.get_scheduled_tasks = MagicMock(return_value=cfg)
    repo.set_scheduled_tasks = MagicMock()
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: repo)
    m._mark_run("s", {"id": "t2"}, "success")
    assert cfg["tasks"][1]["last_status"] == "success"
    repo.set_scheduled_tasks.assert_called_once()


def test_mark_run_error(monkeypatch):
    m = _mgr()
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: (_ for _ in ()).throw(RuntimeError("no")))
    m._mark_run("s", {"id": "t"}, "success")  # 不抛异常


# ── chat handler ───────────────────────────────────────────────────────

async def test_handle_chat_disabled(monkeypatch):
    m = _mgr()
    import sys, types
    cfg = sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(PROACTIVE_OUTREACH_ENABLED=False))
    assert await m._handle_chat("s", {}) == "skipped"


async def test_handle_chat_sends(monkeypatch):
    m = _mgr()
    import sys, types
    cfg = sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(PROACTIVE_OUTREACH_ENABLED=True))
    load_result = {"roles": {"my_agent": {"tier": "expert"}}}
    loader = MagicMock()
    loader.load = MagicMock(return_value=load_result)
    monkeypatch.setattr("config.prompts.loader.get_loader", lambda: loader)
    generate_and_push = AsyncMock()
    monkeypatch.setattr("modules.thinking.frontend_channel.generate_and_push", generate_and_push)
    out = await m._handle_chat("s", {"prompt": "问候", "agent_type": "my_agent"})
    assert out is None
    generate_and_push.assert_awaited_once()


async def test_handle_chat_agent_roles_error(monkeypatch):
    m = _mgr()
    import sys, types
    cfg = sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(PROACTIVE_OUTREACH_ENABLED=True))
    def boom(*a, **k):
        raise RuntimeError("loader")
    monkeypatch.setattr("config.prompts.loader.get_loader", boom)
    generate_and_push = AsyncMock()
    monkeypatch.setattr("modules.thinking.frontend_channel.generate_and_push", generate_and_push)
    await m._handle_chat("s", {"prompt": "问候", "agent_type": "x"})
    generate_and_push.assert_awaited_once()


# ── 单例 ───────────────────────────────────────────────────────────────

def test_get_task_manager_singleton(monkeypatch):
    monkeypatch.setattr(st_mod, "_manager", None)
    a = get_task_manager()
    b = get_task_manager()
    assert a is b
