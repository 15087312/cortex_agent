"""proactive_repo 单元测试 — mock db 边界，覆盖成功/异常/过滤/截断分支"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

import modules.database.proactive_repo as pr


def _session():
    sess = MagicMock()
    sess.add.return_value = None
    q = MagicMock()
    q.filter.return_value = q
    q.order_by.return_value = q
    q.limit.return_value = q
    q.all.return_value = []
    sess.query.return_value = q
    return sess


def _db(sess):
    db = MagicMock()
    cm = db.get_session.return_value
    cm.__enter__.return_value = sess
    cm.__exit__.return_value = False
    return db


def _row(session_id="s1", reason="idle", content="你好", ts=datetime(2026, 8, 1, 10, 0, 0)):
    r = MagicMock()
    r.session_id = session_id
    r.reason = reason
    r.content = content
    r.created_at = ts
    return r


# ── save_proactive_log ──

def test_save_success(monkeypatch):
    sess = _session()
    monkeypatch.setattr(pr, "get_db_manager", lambda: _db(sess))
    assert pr.save_proactive_log("s1", "idle", "内容") is True
    sess.add.assert_called_once()
    added = sess.add.call_args[0][0]
    assert added.session_id == "s1"
    assert added.reason == "idle"


def test_save_defaults_none_values(monkeypatch):
    sess = _session()
    monkeypatch.setattr(pr, "get_db_manager", lambda: _db(sess))
    assert pr.save_proactive_log("s1", None, None) is True
    added = sess.add.call_args[0][0]
    assert added.reason == ""
    assert added.content == ""


def test_save_truncates_content(monkeypatch):
    sess = _session()
    monkeypatch.setattr(pr, "get_db_manager", lambda: _db(sess))
    pr.save_proactive_log("s1", "idle", "x" * 5000)
    added = sess.add.call_args[0][0]
    assert len(added.content) == 2000


def test_save_failure_returns_false(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(pr, "get_db_manager", boom)
    assert pr.save_proactive_log("s1", "idle", "x") is False


def test_save_created_at_utc_naive(monkeypatch):
    sess = _session()
    monkeypatch.setattr(pr, "get_db_manager", lambda: _db(sess))
    pr.save_proactive_log("s1", "idle", "x")
    added = sess.add.call_args[0][0]
    assert added.created_at.tzinfo is None


# ── query_proactive_logs ──

def test_query_returns_rows(monkeypatch):
    sess = _session()
    sess.query.return_value.all.return_value = [_row()]
    monkeypatch.setattr(pr, "get_db_manager", lambda: _db(sess))
    logs = pr.query_proactive_logs()
    assert len(logs) == 1
    assert logs[0]["session_id"] == "s1"
    assert logs[0]["reason"] == "idle"
    assert logs[0]["created_at"] == "2026-08-01T10:00:00"


def test_query_filters_by_session(monkeypatch):
    sess = _session()
    monkeypatch.setattr(pr, "get_db_manager", lambda: _db(sess))
    pr.query_proactive_logs(session_id="s2")
    sess.query.return_value.filter.assert_called_once()


def test_query_no_filter_when_session_empty(monkeypatch):
    sess = _session()
    monkeypatch.setattr(pr, "get_db_manager", lambda: _db(sess))
    pr.query_proactive_logs(session_id="")
    q = sess.query.return_value
    assert not q.filter.called


def test_query_caps_limit_at_200(monkeypatch):
    sess = _session()
    monkeypatch.setattr(pr, "get_db_manager", lambda: _db(sess))
    pr.query_proactive_logs(limit=999)
    args = sess.query.return_value.limit.call_args[0][0]
    assert args == 200


def test_query_null_created_at(monkeypatch):
    sess = _session()
    sess.query.return_value.all.return_value = [_row(ts=None)]
    monkeypatch.setattr(pr, "get_db_manager", lambda: _db(sess))
    logs = pr.query_proactive_logs()
    assert logs[0]["created_at"] == ""


def test_query_failure_returns_empty(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(pr, "get_db_manager", boom)
    assert pr.query_proactive_logs() == []


# ── count_proactive_logs ──

def test_count_success(monkeypatch):
    sess = _session()
    sess.query.return_value.count.return_value = 7
    monkeypatch.setattr(pr, "get_db_manager", lambda: _db(sess))
    assert pr.count_proactive_logs() == 7


def test_count_failure_returns_zero(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(pr, "get_db_manager", boom)
    assert pr.count_proactive_logs() == 0
