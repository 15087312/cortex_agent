"""blackboard_repo 真实 DB 测试（无 mock）：黑板观察落库与查询"""
import json
import threading

import pytest

import modules.database.connection as conn
from modules.database import blackboard_repo
from modules.database.chat_models import BlackboardObservation


class FakeObs:
    """与 BlackboardObservation 真实模型字段一致的观察对象"""

    def __init__(self, **kw):
        self.observation_id = kw.get("observation_id", "o1")
        self.tier = kw.get("tier", "expert")
        self.content = kw.get("content", "观察内容")
        self.created_at = kw.get("created_at", 1785000000)
        self.metadata = kw.get("metadata", {"k": "v"})


@pytest.fixture
def dbm(tmp_path, monkeypatch):
    """真实临时 SQLite 数据库（同 test_session_repo 模式）"""
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "test_blackboard.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    dm = conn.get_db_manager()
    dm.initialize()
    return dm


def test_save_observation_real_db(dbm):
    """真实落库：save 后能从 DB 查回，metadata 序列化正确"""
    assert blackboard_repo.save_observation("s1", FakeObs()) is True
    with dbm.get_session() as s:
        row = s.query(BlackboardObservation).filter_by(session_id="s1").first()
        assert row is not None
        assert row.content == "观察内容"
        assert json.loads(row.metadata_json) == {"k": "v"}


def test_save_observation_without_created_at(dbm):
    """无 created_at 时自动补时间戳，仍落库"""
    obs = FakeObs()
    obs.created_at = None
    assert blackboard_repo.save_observation("s1", obs) is True
    with dbm.get_session() as s:
        row = s.query(BlackboardObservation).filter_by(session_id="s1").first()
        assert row is not None
        assert row.created_at is not None


def test_save_observation_unserializable_metadata_fails(dbm):
    """metadata 含不可序列化对象 → json.dumps 抛异常 → save 返回 False（真实失败路径）"""
    obs = FakeObs()
    obs.metadata = {"bad": object()}  # object() 不可 json 序列化
    assert blackboard_repo.save_observation("s1", obs) is False


def test_query_observations_filtered(dbm):
    """真实查询：按 session/tier 过滤"""
    blackboard_repo.save_observation("s1", FakeObs(tier="expert", content="A"))
    blackboard_repo.save_observation("s1", FakeObs(tier="large", content="B"))
    blackboard_repo.save_observation("s2", FakeObs(tier="expert", content="C"))

    result = blackboard_repo.query_observations(session_id="s1")
    assert len(result) == 2
    result = blackboard_repo.query_observations(session_id="s1", tier="expert")
    assert len(result) == 1
    assert result[0]["content"] == "A"
    result = blackboard_repo.query_observations(session_id="nope")
    assert result == []


def test_query_observations_all(dbm):
    """不传条件返回全部"""
    blackboard_repo.save_observation("s1", FakeObs())
    blackboard_repo.save_observation("s2", FakeObs())
    assert len(blackboard_repo.query_observations()) == 2
