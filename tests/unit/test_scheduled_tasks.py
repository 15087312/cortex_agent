"""会话定时任务调度判定测试（每天/间隔/单次/cron）"""
import asyncio
import threading
from datetime import datetime, timedelta
from unittest.mock import MagicMock

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


def test_handle_chat(monkeypatch):
    import modules.perception.trigger as trg
    import modules.thinking.frontend_channel as fc
    m = ScheduledTaskManager()
    async def fake_llm(prompt, session_id, role=None, tier="large"):
        return "定时问候"
    pushed = []
    async def fake_push(sid, *, msg_type, event, content, role="assistant", data=None, persist=True):
        pushed.append(content)
        return True
    monkeypatch.setattr(trg, "call_outreach_llm", fake_llm)
    monkeypatch.setattr(fc, "confirm_frontend_connection", lambda session_id=None: True)
    monkeypatch.setattr(fc, "push_content", fake_push)
    asyncio.run(m._handle_chat("s1", {"prompt": "问候"}))
    assert pushed == ["定时问候"]


def test_handle_chat_empty(monkeypatch):
    import modules.perception.trigger as trg
    import modules.thinking.frontend_channel as fc
    m = ScheduledTaskManager()
    async def fake_llm(prompt, session_id, role=None, tier="large"):
        return ""
    monkeypatch.setattr(trg, "call_outreach_llm", fake_llm)
    pushed = []
    async def fake_push(sid, *, msg_type, event, content, role="assistant", data=None, persist=True):
        pushed.append(content)
        return True
    monkeypatch.setattr(fc, "confirm_frontend_connection", lambda session_id=None: True)
    monkeypatch.setattr(fc, "push_content", fake_push)
    asyncio.run(m._handle_chat("s1", {}))
    assert pushed == []


def test_mark_run(monkeypatch):
    import modules.database.session_repo as sr
    repo = MagicMock()
    repo.get_scheduled_tasks.return_value = {"tasks": [{"id": "t1", "last_run": None, "last_status": None}]}
    monkeypatch.setattr(sr, "get_session_repo", lambda: repo)
    m = ScheduledTaskManager()
    m._mark_run("s1", {"id": "t1"}, "success")
    assert repo.set_scheduled_tasks.called
    cfg = repo.set_scheduled_tasks.call_args[0][1]
    assert cfg["tasks"][0]["last_status"] == "success"


def test_mark_run_failure(monkeypatch):
    import modules.database.session_repo as sr
    repo = MagicMock()
    repo.get_scheduled_tasks.side_effect = RuntimeError
    monkeypatch.setattr(sr, "get_session_repo", lambda: repo)
    ScheduledTaskManager()._mark_run("s1", {"id": "t1"}, "error")  # 不应抛


def test_handle_chat_handshake_fail_skips_llm(monkeypatch):
    """前端不可达（握手失败）时不调用 LLM、不推送"""
    import modules.perception.trigger as trg
    import modules.thinking.frontend_channel as fc
    m = ScheduledTaskManager()
    called = {"llm": 0}
    async def fake_llm(prompt, session_id, role=None, tier="large"):
        called["llm"] += 1
        return "定时问候"
    monkeypatch.setattr(trg, "call_outreach_llm", fake_llm)
    pushed = []
    async def fake_push(sid, *, msg_type, event, content, role="assistant", data=None, persist=True):
        pushed.append(content)
        return True
    monkeypatch.setattr(fc, "confirm_frontend_connection", lambda session_id=None: False)
    monkeypatch.setattr(fc, "push_content", fake_push)
    asyncio.run(m._handle_chat("s1", {"prompt": "问候"}))
    assert called["llm"] == 0
    assert pushed == []


def test_scan(monkeypatch):
    import modules.thinking.scheduled_tasks as mod
    m = ScheduledTaskManager()
    repo = MagicMock()
    repo.get_all_sessions.return_value = [{"session_id": "s1"}]
    repo.get_scheduled_tasks.return_value = {"tasks": [{"id": "t1", "enabled": True, "schedule": {"kind": "interval", "every_minutes": 1}}]}
    import modules.database.session_repo as sr
    monkeypatch.setattr(sr, "get_session_repo", lambda: repo)
    fired = []
    async def fake_fire(sid, task):
        fired.append((sid, task))
    monkeypatch.setattr(m, "_fire", fake_fire)
    asyncio.run(m._scan())
    assert len(fired) == 1


def test_scan_skips_disabled(monkeypatch):
    m = ScheduledTaskManager()
    import modules.thinking.scheduled_tasks as mod
    repo = MagicMock()
    repo.get_all_sessions.return_value = [{"session_id": "s1"}]
    repo.get_scheduled_tasks.return_value = {"tasks": [{"id": "t1", "enabled": False}]}
    import modules.database.session_repo as sr
    monkeypatch.setattr(sr, "get_session_repo", lambda: repo)
    fired = []
    m._fire = lambda sid, task: fired.append((sid, task))
    asyncio.run(m._scan())
    assert fired == []
