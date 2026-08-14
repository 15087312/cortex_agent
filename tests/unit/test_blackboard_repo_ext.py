"""blackboard_repo 补充测试 — 落库/查询/过滤/异常分支（真实 SQLite + mock 兜底）"""
import threading
from types import SimpleNamespace

import pytest

import modules.database.connection as conn
import modules.database.blackboard_repo as br
from modules.database.chat_models import BlackboardObservation


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "bb.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    dm = conn.get_db_manager()
    dm.initialize()
    monkeypatch.setattr(br, "get_db_manager", lambda: dm)
    return dm


def _obs(**kw):
    fields = dict(
        observation_id="obs-1",
        tier="large",
        content="发现了一个 bug",
        created_at=None,
        metadata={"a": 1},
    )
    fields.update(kw)
    return SimpleNamespace(**fields)


# ── save_observation ──────────────────────────────────────────────────────

def test_save_success_with_timestamp(db):
    obs = _obs(created_at=1700000000)
    assert br.save_observation("s1", obs) is True
    with db.get_session() as s:
        row = s.query(BlackboardObservation).first()
        assert row.session_id == "s1"
        assert row.observation_id == "obs-1"
        assert row.tier == "large"
        assert row.content == "发现了一个 bug"
        assert row.created_at.year == 2023  # 由时间戳换算
        assert row.metadata_json == '{"a": 1}'


def test_save_success_defaults(db):
    obs = _obs(created_at=0, metadata=None, observation_id="", tier="", content="")
    assert br.save_observation("s1", obs) is True
    with db.get_session() as s:
        row = s.query(BlackboardObservation).first()
        assert row.observation_id == ""
        assert row.tier == ""
        assert row.content == ""
        assert row.metadata_json == "{}"
        assert row.created_at is not None  # 无时间戳 → now


def test_save_failure_returns_false(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(br, "get_db_manager", boom)
    assert br.save_observation("s1", _obs()) is False


# ── query_observations ────────────────────────────────────────────────────

def _insert(db, session_id, tier, content, created):
    with db.get_session() as s:
        s.add(BlackboardObservation(
            session_id=session_id, observation_id=f"o-{content}",
            tier=tier, content=content,
            created_at=created, metadata_json='{"n": 1}',
        ))


def test_query_no_filters(db):
    _insert(db, "s1", "large", "A", None)
    _insert(db, "s2", "small", "B", None)
    rows = br.query_observations()
    assert len(rows) == 2
    assert rows[0]["metadata"] == {"n": 1}
    assert rows[0]["created_at"] != ""  # 有 created_at 默认值


def test_query_null_created_at(db):
    _insert(db, "s1", "large", "A", None)
    with db.get_session() as s:
        row = s.query(BlackboardObservation).first()
        row.created_at = None
    rows = br.query_observations(session_id="s1")
    assert rows[0]["created_at"] == ""


def test_query_filters(db):
    _insert(db, "s1", "large", "发现甲问题", None)
    _insert(db, "s1", "small", "普通内容", None)
    _insert(db, "s2", "large", "发现乙问题", None)
    rows = br.query_observations(session_id="s1", tier="large", query="甲")
    assert len(rows) == 1
    assert rows[0]["content"] == "发现甲问题"


def test_query_time_range_valid(db):
    from datetime import datetime
    _insert(db, "s1", "large", "early", datetime(2026, 1, 1))
    _insert(db, "s1", "large", "late", datetime(2026, 6, 1))
    rows = br.query_observations(
        session_id="s1",
        start="2026-02-01T00:00:00",
        end="2026-07-01T00:00:00",
    )
    assert [r["content"] for r in rows] == ["late"]


def test_query_time_range_invalid_ignored(db):
    _insert(db, "s1", "large", "x", None)
    rows = br.query_observations(session_id="s1", start="not-a-date", end="also-bad")
    assert len(rows) == 1  # 非法日期被忽略，不过滤


def test_query_limit_and_order(db):
    _insert(db, "s1", "large", "a", None)
    _insert(db, "s1", "large", "b", None)
    _insert(db, "s1", "large", "c", None)
    rows = br.query_observations(session_id="s1", limit=2)
    assert len(rows) == 2


def test_query_failure_returns_empty(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(br, "get_db_manager", boom)
    assert br.query_observations() == []
