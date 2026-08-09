"""会话定时任务调度判定测试（每天/间隔/单次/cron）"""
import asyncio
import threading
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.thinking.scheduled_tasks import ScheduledTaskManager


def _mgr():
    m = ScheduledTaskManager.__new__(ScheduledTaskManager)  # 不启动线程
    m._last_fired = {}
    m._lock = threading.RLock()
    return m


def test_daily_due_and_no_repeat():
    m = _mgr()
    now = datetime(2026, 8, 9, 10, 30)
    assert m._due_daily("s", {"id": "t1"}, "10:31", now)  # jitter ±5min
    assert not m._due_daily("s", {"id": "t1"}, "10:31", now)  # 同一天不重复


def test_interval_due():
    m = _mgr()
    now = datetime(2026, 8, 9, 10, 0)
    assert m._due_interval("s", {"id": "i1"}, 1, now)
    assert not m._due_interval("s", {"id": "i1"}, 1, now)  # 间隔内不重复
    m._last_fired[("s", "i1")] = now.timestamp() - 120  # 已过 2 分钟
    assert m._due_interval("s", {"id": "i1"}, 1, now)


def test_cron_due():
    m = _mgr()
    now = datetime(2026, 8, 9, 10, 30)
    assert m._due_cron("s", {"id": "c1"}, "30 10", now)
    assert not m._due_cron("s", {"id": "c1"}, "30 10", now)  # 同分钟不重复


def test_once_due_only_once():
    m = _mgr()
    now = datetime(2026, 8, 9, 10, 30)
    assert m._due_once("s", {"id": "o1"}, "10:31", now)
    assert not m._due_once("s", {"id": "o1"}, "10:31", now)  # 单次后不再触发


def test_due_dispatch():
    m = _mgr()
    now = datetime(2026, 8, 9, 10, 30)
    assert m._due("s", {"id": "t1", "schedule": "10:31"}, now)
    assert m._due("s", {"id": "t2", "schedule": {"kind": "interval", "every_minutes": 5}}, now)
    assert m._due("s", {"id": "t3", "schedule": {"kind": "once", "at": "10:31"}}, now)
    assert not m._due("s", {"id": "t4", "schedule": "bad"}, now)


def test_due_daily_invalid_time():
    m = ScheduledTaskManager()
    assert m._due_daily("s", {"id": "t1"}, "abc", datetime.now()) is False


def test_due_interval_cooldown():
    m = ScheduledTaskManager()
    task = {"id": "i1", "schedule": {"kind": "interval", "every_minutes": 30}}
    assert m._due("s", task, datetime.now()) is True
    assert m._due("s", task, datetime.now()) is False  # 冷却中


def test_due_once_iso_datetime():
    m = ScheduledTaskManager()
    task = {"id": "o1"}
    past = (datetime.now() - timedelta(minutes=5)).isoformat()
    assert m._due_once("s", task, past, datetime.now()) is True
    assert m._due_once("s", task, past, datetime.now()) is False  # 单次不重复


def test_due_unknown_schedule_false():
    m = ScheduledTaskManager()
    assert m._due("s", {"schedule": {"kind": "weird"}}, datetime.now()) is False


def test_fire_unknown_action():
    m = ScheduledTaskManager()
    result = asyncio.run(m._fire("s", {"id": "t", "action": "nope"}))
    # 未知 action 标记 error 后返回
    assert m._last_fired is not None


def test_fire_success(monkeypatch):
    m = ScheduledTaskManager()
    calls = []
    async def handler(sid, task):
        calls.append((sid, task))
    m.register_handler("my_action", handler)
    asyncio.run(m._fire("s", {"id": "t", "action": "my_action"}))
    assert calls == [("s", {"id": "t", "action": "my_action"})]


def test_fire_handler_error(monkeypatch):
    m = ScheduledTaskManager()
    async def bad(sid, task):
        raise RuntimeError("boom")
    m.register_handler("bad", bad)
    mm = MagicMock()
    monkeypatch.setattr(m, "_mark_run", mm)
    asyncio.run(m._fire("s", {"id": "t", "action": "bad"}))
    mm.assert_called_once_with("s", {"id": "t", "action": "bad"}, "error")


@pytest.fixture
def session_repo(tmp_path, monkeypatch):
    """真实临时 SQLite + 真实 SessionRepository"""
    import modules.database.connection as conn
    from modules.database.session_repo import SessionRepository
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "test_sched.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    conn.get_db_manager().initialize()
    return SessionRepository()


def test_mark_run_real_db(session_repo):
    """真实 DB：_mark_run 更新 last_run/last_status 并落库"""
    session_repo.create_session("s1")
    session_repo.set_scheduled_tasks("s1", {"tasks": [{"id": "t1", "last_run": None, "last_status": None}]})
    import modules.database.session_repo as sr
    with patch.object(sr, "get_session_repo", lambda: session_repo):
        m = ScheduledTaskManager()
        m._mark_run("s1", {"id": "t1"}, "success")
    cfg = session_repo.get_scheduled_tasks("s1")
    assert cfg["tasks"][0]["last_status"] == "success"
    assert cfg["tasks"][0]["last_run"]


def test_mark_run_no_session_real_db(session_repo):
    """真实 DB：无该会话时不抛（_mark_run 容错）"""
    import modules.database.session_repo as sr
    with patch.object(sr, "get_session_repo", lambda: session_repo):
        ScheduledTaskManager()._mark_run("不存在会话", {"id": "t1"}, "error")  # 不应抛


def test_scan_real_db(session_repo):
    """真实 DB + 真实 handler：_scan 触发 enabled 任务并真实 _fire"""
    session_repo.create_session("s1")
    session_repo.set_scheduled_tasks("s1", {"tasks": [{"id": "t1", "enabled": True, "action": "noop", "schedule": {"kind": "interval", "every_minutes": 1}}]})
    import modules.database.session_repo as sr
    fired = []

    async def noop(sid, task):
        fired.append((sid, task["id"]))

    with patch.object(sr, "get_session_repo", lambda: session_repo):
        m = ScheduledTaskManager()
        m.register_handler("noop", noop)
        asyncio.run(m._scan())
    assert ("s1", "t1") in fired


def test_scan_skips_disabled_real_db(session_repo):
    """真实 DB：disabled 任务不触发"""
    session_repo.create_session("s1")
    session_repo.set_scheduled_tasks("s1", {"tasks": [{"id": "t1", "enabled": False, "action": "noop", "schedule": {"kind": "interval", "every_minutes": 1}}]})
    import modules.database.session_repo as sr
    fired = []

    async def noop(sid, task):
        fired.append(sid)

    with patch.object(sr, "get_session_repo", lambda: session_repo):
        m = ScheduledTaskManager()
        m.register_handler("noop", noop)
        asyncio.run(m._scan())
    assert fired == []
