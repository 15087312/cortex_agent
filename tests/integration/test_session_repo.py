"""真实 SessionRepository 测试（create_session/save_message/get_messages/clear_messages）

覆盖之前被 mock 掩盖的真实实现路径（如 _utcnow 之类运行时问题）。
"""
import os
import threading

import pytest

import modules.database.connection as conn


@pytest.fixture
def repo(tmp_path, monkeypatch):
    from modules.database.session_repo import SessionRepository
    import modules.database.session_repo as sr
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "test_memory.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    dm = conn.get_db_manager()
    dm.initialize()
    monkeypatch.setattr(sr, "get_db_manager", lambda: dm)
    return SessionRepository()


def test_create_session_then_list(repo):
    repo.create_session("s_test_1")
    sessions = repo.get_all_sessions()
    assert any(s["session_id"] == "s_test_1" for s in sessions)


def test_save_and_get_messages(repo):
    repo.create_session("s_test_1")
    mid = repo.save_message("s_test_1", "user", "你好")
    assert mid
    repo.save_message("s_test_1", "assistant", "收到")
    msgs = repo.get_messages("s_test_1")
    assert len(msgs) == 2
    assert any(m["content"] == "你好" for m in msgs)


def test_clear_messages(repo):
    repo.create_session("s_test_1")
    repo.save_message("s_test_1", "user", "a")
    repo.save_message("s_test_1", "assistant", "b")
    deleted = repo.clear_messages("s_test_1")
    assert deleted == 2
    assert repo.get_messages("s_test_1") == []


def test_create_session_idempotent(repo):
    # 已有会话再 create 不应抛错（走 _utcnow 更新路径）
    repo.create_session("s_test_1")
    repo.create_session("s_test_1")
    sessions = repo.get_all_sessions()
    matches = [s for s in sessions if s["session_id"] == "s_test_1"]
    assert len(matches) == 1


def test_delete_session(repo):
    repo.create_session("s_test_del")
    repo.delete_session("s_test_del")
    sessions = repo.get_all_sessions()
    assert not any(s["session_id"] == "s_test_del" for s in sessions)
