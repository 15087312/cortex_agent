"""blackboard_repo 测试（此前 38% 覆盖）：黑板观察落库与查询"""
import json
from unittest.mock import MagicMock, patch

from modules.database import blackboard_repo


class FakeObs:
    def __init__(self):
        self.observation_id = "o1"
        self.tier = "expert"
        self.content = "观察内容"
        self.created_at = 1785000000
        self.metadata = {"k": "v"}


def test_save_observation_success():
    db = MagicMock()
    with patch.object(blackboard_repo, "get_db_manager", return_value=db):
        assert blackboard_repo.save_observation("s1", FakeObs()) is True
    # 验证落库了 metadata 序列化
    added = db.get_session.return_value.__enter__.return_value.add.call_args[0][0]
    assert json.loads(added.metadata_json) == {"k": "v"}


def test_save_observation_failure():
    db = MagicMock()
    db.get_session.side_effect = RuntimeError("db down")
    with patch.object(blackboard_repo, "get_db_manager", return_value=db):
        assert blackboard_repo.save_observation("s1", FakeObs()) is False


def test_save_observation_no_created_at():
    obs = FakeObs()
    obs.created_at = None
    db = MagicMock()
    with patch.object(blackboard_repo, "get_db_manager", return_value=db):
        assert blackboard_repo.save_observation("s1", obs) is True


def _fake_query():
    q = MagicMock()
    q.filter.return_value = q
    q.limit.return_value = q
    q.all.return_value = []
    return q


def test_query_observations_all_filters():
    db = MagicMock()
    db.get_session.return_value.__enter__.return_value.query.return_value = _fake_query()
    with patch.object(blackboard_repo, "get_db_manager", return_value=db):
        result = blackboard_repo.query_observations(
            session_id="s1", tier="expert", start="2025-01-01", end="2025-02-01", query="关键词"
        )
    assert result == []


def test_query_observations_no_filters():
    db = MagicMock()
    db.get_session.return_value.__enter__.return_value.query.return_value = _fake_query()
    with patch.object(blackboard_repo, "get_db_manager", return_value=db):
        assert blackboard_repo.query_observations() == []
