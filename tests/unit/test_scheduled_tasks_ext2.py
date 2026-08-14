"""scheduled_tasks 补测：后台循环异常 / 调度判定边界 / scan 未到期任务 / chat 无 agent_type"""
import asyncio
import sys
import threading
import time
import types
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

import modules.thinking.scheduled_tasks as st_mod
from modules.thinking.scheduled_tasks import ScheduledTaskManager


def _mgr():
    m = ScheduledTaskManager.__new__(ScheduledTaskManager)
    m._last_fired = {}
    m._handlers = {}
    m._lock = threading.RLock()
    m._stop = threading.Event()
    m._thread = None
    return m


# ── 后台循环：_scan 异常被捕获 ────────────────────────────────────────

def test_loop_scan_error(monkeypatch):
    m = _mgr()
    monkeypatch.setattr(st_mod, "SCAN_INTERVAL", 0.001)
    m._scan = AsyncMock(side_effect=RuntimeError("scan boom"))

    def stop_later():
        time.sleep(0.05)
        m._stop.set()

    t = threading.Thread(target=stop_later)
    t.start()
    m._loop()  # 扫描失败不中断循环
    t.join()
    assert m._stop.is_set()


# ── 调度判定边界 ──────────────────────────────────────────────────────

def test_due_daily_not_due():
    m = _mgr()
    now = datetime(2026, 8, 9, 10, 0)
    assert m._due_daily("s", {"id": "d"}, "10:31", now) is False  # 超出 jitter


def test_due_once_bad_time():
    m = _mgr()
    now = datetime(2026, 8, 9, 10, 30)
    assert m._due_once("s", {"id": "x"}, "ab:cd", now) is False


def test_due_once_not_due():
    m = _mgr()
    now = datetime(2026, 8, 9, 10, 0)
    assert m._due_once("s", {"id": "x"}, "10:31", now) is False  # 未到时间


def test_due_once_future_iso():
    m = _mgr()
    now = datetime(2026, 8, 9, 10, 30)
    assert m._due_once("s", {"id": "x"}, "2030-01-01T10:00:00", now) is False


def test_due_cron_wrong_parts():
    m = _mgr()
    now = datetime(2026, 8, 9, 10, 30)
    assert m._due_cron("s", {"id": "c"}, "* * *", now) is False  # 3 段 → 忽略


def test_due_cron_exception():
    m = _mgr()
    now = MagicMock()
    now.strftime.return_value = "x"
    type(now).minute = PropertyMock(side_effect=RuntimeError("boom"))
    type(now).hour = 10
    assert m._due_cron("s", {"id": "c"}, "*/5 10", now) is False


# ── _scan：启用但未到期 ───────────────────────────────────────────────

async def test_scan_enabled_not_due(monkeypatch):
    m = _mgr()
    repo = MagicMock()
    repo.get_all_sessions = MagicMock(return_value=[{"session_id": "s1"}])
    repo.get_scheduled_tasks = MagicMock(return_value={"tasks": [
        {"id": "t1", "enabled": True, "schedule": "10:31"},
        {"id": "t2", "enabled": True, "schedule": "10:31"},
    ]})
    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: repo)
    m._due = MagicMock(side_effect=lambda sid, task, now: task["id"] == "t2")
    m._fire = AsyncMock()
    await m._scan()  # t1 未到期 → 跳过
    m._fire.assert_awaited_once_with("s1", {"id": "t2", "enabled": True, "schedule": "10:31"})


# ── chat handler：无 agent_type ───────────────────────────────────────

async def test_handle_chat_no_agent_type(monkeypatch):
    m = _mgr()
    cfg = sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(PROACTIVE_OUTREACH_ENABLED=True))
    generate_and_push = AsyncMock()
    monkeypatch.setattr("modules.thinking.frontend_channel.generate_and_push", generate_and_push)
    await m._handle_chat("s", {"prompt": "问候"})  # 跳过角色加载分支
    generate_and_push.assert_awaited_once()
