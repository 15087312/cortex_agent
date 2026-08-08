"""proactive_repo 测试（此前 0% 覆盖）：主动搭话日志落库/查询"""
import threading

import pytest

import modules.database.connection as conn


@pytest.fixture
def tmp_repo(tmp_path, monkeypatch):
    import modules.database.proactive_repo as pr
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "test_memory.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    conn.get_db_manager().initialize()
    # 替换 proactive_repo 用的 get_db_manager
    monkeypatch.setattr(pr, "get_db_manager", lambda: conn.get_db_manager())
    return pr


def test_save_and_query(tmp_repo):
    assert tmp_repo.save_proactive_log("s1", "idle", "你好，有什么需要帮忙的吗")
    logs = tmp_repo.query_proactive_logs(limit=10)
    assert len(logs) >= 1
    assert logs[0]["content"] == "你好，有什么需要帮忙的吗"
    assert logs[0]["reason"] == "idle"


def test_query_by_session(tmp_repo):
    tmp_repo.save_proactive_log("s1", "idle", "消息1")
    tmp_repo.save_proactive_log("s2", "screen", "消息2")
    logs = tmp_repo.query_proactive_logs(session_id="s2")
    assert len(logs) == 1
    assert logs[0]["content"] == "消息2"


def test_count(tmp_repo):
    tmp_repo.save_proactive_log("s1", "idle", "消息1")
    tmp_repo.save_proactive_log("s1", "screen", "消息2")
    assert tmp_repo.count_proactive_logs() >= 2
