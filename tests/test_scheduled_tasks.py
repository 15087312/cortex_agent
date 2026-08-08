"""会话定时任务调度判定测试（每天/间隔/单次/cron）"""
import threading
from datetime import datetime

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
