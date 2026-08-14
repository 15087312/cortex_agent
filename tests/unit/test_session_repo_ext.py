"""SessionRepository 补充测试 — 真实 SQLite，覆盖元数据/任务/清理/复制等分支"""
import threading
from datetime import datetime, timedelta

import pytest

import modules.database.connection as conn
import modules.database.session_repo as sr


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "sess.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    dm = conn.get_db_manager()
    dm.initialize()
    monkeypatch.setattr(sr, "get_db_manager", lambda: dm)
    return sr.SessionRepository()


@pytest.fixture
def now(monkeypatch):
    """冻结 _utcnow，便于时间相关断言"""
    fake = datetime(2026, 8, 14, 10, 0, 0)

    def _fake_utcnow():
        return fake

    monkeypatch.setattr(sr, "_utcnow", _fake_utcnow)
    return fake


# ── 进程级标志 ────────────────────────────────────────────────────────────

def test_boot_has_spoken_default(repo, monkeypatch):
    monkeypatch.setattr(sr, "_boot_has_spoken", False)
    assert sr.get_boot_has_spoken() is False


def test_boot_has_spoken_set_by_user_message(repo, monkeypatch):
    monkeypatch.setattr(sr, "_boot_has_spoken", False)
    repo.create_session("s1")
    repo.save_message("s1", "user", "你好")
    assert sr.get_boot_has_spoken() is True


def test_boot_has_spoken_not_set_by_assistant(repo, monkeypatch):
    monkeypatch.setattr(sr, "_boot_has_spoken", False)
    repo.create_session("s1")
    repo.save_message("s1", "assistant", "hi")
    assert sr.get_boot_has_spoken() is False


# ── 会话生命周期 ──────────────────────────────────────────────────────────

def test_create_session_idempotent_updates_existing(repo, now):
    repo.create_session("s1")
    repo.create_session("s1")  # 已有 → 更新 last_active + is_active
    sessions = repo.get_all_sessions()
    assert len(sessions) == 1
    assert sessions[0]["last_active"] == now.isoformat()


def test_touch_session(repo, now):
    repo.create_session("s1")
    repo.touch_session("s1")
    repo.touch_session("missing")  # 无会话 → no-op
    sess = repo.get_session_metadata("s1")  # 仅确认不抛
    assert sess == {}


def test_close_session(repo):
    repo.create_session("s1")
    repo.close_session("s1")
    assert repo.get_active_sessions() == []
    repo.close_session("missing")  # no-op


def test_set_session_title(repo):
    repo.create_session("s1")
    repo.set_session_title("s1", "x" * 300)  # 截断到 200
    row = repo.get_all_sessions()[0]
    assert len(row["title"]) == 200
    repo.set_session_title("missing", "t")  # no-op


def test_get_session_metadata_missing(repo):
    assert repo.get_session_metadata("nope") == {}


def test_get_session_metadata_invalid_json(repo):
    repo.create_session("s1")
    with repo._session() as s:
        from modules.database.chat_models import ChatSession
        row = s.query(ChatSession).filter_by(session_id="s1").first()
        row.metadata_json = "{invalid"
    assert repo.get_session_metadata("s1") == {}


def test_set_session_metadata_merge(repo):
    repo.create_session("s1")
    assert repo.set_session_metadata("s1", {"a": 1}) is True
    assert repo.set_session_metadata("s1", {"b": 2}) is True
    meta = repo.get_session_metadata("s1")
    assert meta == {"a": 1, "b": 2}
    assert repo.set_session_metadata("missing", {}) is False


def test_set_session_metadata_bad_existing(repo):
    repo.create_session("s1")
    with repo._session() as s:
        from modules.database.chat_models import ChatSession
        row = s.query(ChatSession).filter_by(session_id="s1").first()
        row.metadata_json = "oops"
    assert repo.set_session_metadata("s1", {"a": 1}) is True
    assert repo.get_session_metadata("s1") == {"a": 1}


# ── outreach / scheduled_tasks ────────────────────────────────────────────

def test_outreach_config(repo):
    repo.create_session("s1")
    assert repo.get_outreach_config("s1") == {}
    repo.set_outreach_config("s1", {"enabled": True, "cooldown_range": [5, 10]})
    assert repo.get_outreach_config("s1")["enabled"] is True


def test_outreach_config_non_dict(repo):
    repo.create_session("s1")
    repo.set_session_metadata("s1", {"outreach": "not-a-dict"})
    assert repo.get_outreach_config("s1") == {}


def test_scheduled_tasks(repo):
    repo.create_session("s1")
    # 未配置时返回 {}（实现实际行为，docstring 中的 {"tasks": []} 仅在 meta 值非 dict 时出现）
    assert repo.get_scheduled_tasks("s1") == {}
    repo.set_scheduled_tasks("s1", {"tasks": [{"id": "t1"}]})
    assert repo.get_scheduled_tasks("s1")["tasks"][0]["id"] == "t1"
    assert repo.set_scheduled_tasks("s1", "not-a-dict") is True
    assert repo.get_scheduled_tasks("s1") == {"tasks": []}


def test_scheduled_tasks_non_dict_in_meta(repo):
    repo.create_session("s1")
    repo.set_session_metadata("s1", {"scheduled_tasks": "bad"})
    assert repo.get_scheduled_tasks("s1") == {"tasks": []}


# ── 列表查询 ──────────────────────────────────────────────────────────────

def test_get_all_sessions_order_and_fields(repo):
    repo.create_session("older")
    repo.create_session("newer")
    with repo._session() as s:
        from modules.database.chat_models import ChatSession
        for sid in ("older", "newer"):
            row = s.query(ChatSession).filter_by(session_id=sid).first()
            row.last_active = datetime(2026, 8, 1) if sid == "older" else datetime(2026, 8, 2)
            row.title = f"标题{sid}"
            row.metadata_json = '{"k": "v"}'
    sessions = repo.get_all_sessions(limit=1)
    assert sessions[0]["session_id"] == "newer"
    assert sessions[0]["metadata"]["k"] == "v"
    assert sessions[0]["created_at"].startswith("2026")


def test_get_all_sessions_null_dates(repo):
    repo.create_session("s1")
    with repo._session() as s:
        from modules.database.chat_models import ChatSession
        row = s.query(ChatSession).filter_by(session_id="s1").first()
        row.created_at = None
        row.last_active = None
        row.metadata_json = None
    sessions = repo.get_all_sessions()
    assert sessions[0]["created_at"] == ""
    assert sessions[0]["last_active"] == ""
    assert sessions[0]["metadata"] == {}


def test_get_active_sessions(repo):
    repo.create_session("a")
    repo.create_session("b")
    repo.close_session("b")
    active = repo.get_active_sessions()
    assert [x["session_id"] for x in active] == ["a"]


# ── 消息 ──────────────────────────────────────────────────────────────────

def test_save_message_skips_empty(repo):
    repo.create_session("s1")
    assert repo.save_message("s1", "user", "   ") == ""
    assert repo.save_message("s1", "user", None) == ""


def test_save_message_truncates_and_titles(repo):
    repo.create_session("s1")
    long = "长" * 60000
    mid = repo.save_message("s1", "user", long)
    assert mid
    msgs = repo.get_messages("s1")
    assert len(msgs[0]["content"]) == 50000
    assert len(repo.get_all_sessions()[0]["title"]) == 200


def test_save_message_updates_counts(repo):
    repo.create_session("s1")
    repo.save_message("s1", "user", "a")
    repo.save_message("s1", "assistant", "b")
    assert repo.get_all_sessions()[0]["message_count"] == 2
    # 无会话时也能保存消息（不更新计数）
    assert repo.save_message("ghost", "user", "x")


def test_delete_message(repo):
    repo.create_session("s1")
    mid = repo.save_message("s1", "user", "a")
    repo.save_message("s1", "assistant", "b")
    assert repo.delete_message("s1", mid) is True
    assert repo.get_all_sessions()[0]["message_count"] == 1
    assert repo.delete_message("s1", "nope") is False


def test_delete_message_without_session_row(repo):
    repo.create_session("s1")
    mid = repo.save_message("s1", "user", "a")
    with repo._session() as s:
        from modules.database.chat_models import ChatSession
        row = s.query(ChatSession).filter_by(session_id="s1").first()
        s.delete(row)
    assert repo.delete_message("s1", mid) is True  # 无 session_row → 跳过计数更新


def test_clear_messages(repo, now):
    repo.create_session("s1")
    repo.save_message("s1", "user", "a")
    repo.save_message("s1", "assistant", "b")
    deleted = repo.clear_messages("s1")
    assert deleted == 2
    assert repo.get_messages("s1") == []
    assert repo.get_all_sessions()[0]["message_count"] == 0
    assert repo.get_all_sessions()[0]["last_active"] == now.isoformat()


def test_clear_messages_unknown_session(repo):
    assert repo.clear_messages("nope") == 0


def test_update_message(repo):
    repo.create_session("s1")
    assert repo.update_message("s1", "x", "") is False  # 空内容
    mid = repo.save_message("s1", "user", "旧")
    assert repo.update_message("s1", mid, "新内容" * 20000) is True
    msgs = repo.get_messages("s1")
    assert len(msgs[0]["content"]) == 50000
    assert repo.update_message("s1", "missing", "x") is False


def test_update_message_without_session_row(repo):
    repo.create_session("s1")
    mid = repo.save_message("s1", "user", "旧")
    with repo._session() as s:
        from modules.database.chat_models import ChatSession
        row = s.query(ChatSession).filter_by(session_id="s1").first()
        s.delete(row)
    assert repo.update_message("s1", mid, "新") is True  # 无 session_row → 跳过 touch


def test_get_recent_messages_order(repo):
    repo.create_session("s1")
    for i in range(5):
        repo.save_message("s1", "user", f"m{i}")
    recent = repo.get_recent_messages("s1", limit=3)
    assert [m["content"] for m in recent] == ["m2", "m3", "m4"]
    assert repo.get_recent_messages("nope") == []


# ── 摘要 / 删除 ───────────────────────────────────────────────────────────

def test_get_session_summary(repo):
    assert repo.get_session_summary("nope") is None
    repo.create_session("s1", execution_mode="view")
    repo.save_message("s1", "user", "hi")
    summary = repo.get_session_summary("s1")
    assert summary["session_id"] == "s1"
    assert summary["execution_mode"] == "view"
    assert summary["message_count"] == 1


def test_delete_session(repo):
    repo.create_session("s1")
    repo.save_message("s1", "user", "a")
    assert repo.delete_session("s1") is True
    assert repo.get_session_summary("s1") is None
    assert repo.delete_session("ghost") is False


def test_delete_empty_sessions(repo, now):
    repo.create_session("old_empty")
    repo.create_session("voice_legacy")
    repo.create_session("recent_empty")
    repo.create_session("has_messages")
    repo.save_message("has_messages", "user", "x")
    with repo._session() as s:
        from modules.database.chat_models import ChatSession
        row = s.query(ChatSession).filter_by(session_id="old_empty").first()
        row.last_active = now - timedelta(minutes=60)
        row2 = s.query(ChatSession).filter_by(session_id="voice_legacy").first()
        row2.last_active = now - timedelta(minutes=5)  # voice 新也清
    deleted = repo.delete_empty_sessions(exclude_ids=["recent_empty"])
    remaining = {x["session_id"] for x in repo.get_all_sessions()}
    assert deleted == 2  # old_empty + voice_legacy
    assert remaining == {"recent_empty", "has_messages"}


def test_delete_empty_sessions_noop(repo, now):
    # 默认 10 分钟闲置阈值：last_active 晚于 cutoff → 保留
    repo.create_session("only")
    with repo._session() as s:
        from modules.database.chat_models import ChatSession
        row = s.query(ChatSession).filter_by(session_id="only").first()
        row.last_active = now  # 与冻结 _utcnow 同源，> cutoff
    assert repo.delete_empty_sessions() == 0


# ── 复制消息 ──────────────────────────────────────────────────────────────

def test_copy_messages_to_session(repo):
    repo.create_session("src")
    repo.create_session("dst")
    repo.save_message("src", "user", "a", round_num=1, tier="large")
    repo.save_message("src", "assistant", "b")
    n = repo.copy_messages_to_session("src", "dst")
    assert n == 2
    dst_msgs = repo.get_messages("dst")
    assert [m["content"] for m in dst_msgs] == ["a", "b"]
    assert dst_msgs[0]["round_num"] == 1 and dst_msgs[0]["tier"] == "large"
    assert repo.get_all_sessions()[0]["message_count"] == 2


def test_copy_messages_no_source(repo):
    repo.create_session("dst")
    assert repo.copy_messages_to_session("src", "dst") == 0


def test_copy_messages_missing_target(repo):
    repo.create_session("src")
    repo.save_message("src", "user", "a")
    assert repo.copy_messages_to_session("src", "dst") == 1  # 无目标会话也能复制


# ── _parse_metadata / 单例 ────────────────────────────────────────────────

def test_parse_metadata(repo):
    assert sr.SessionRepository._parse_metadata('{"a": 1}') == {"a": 1}
    assert sr.SessionRepository._parse_metadata(None) == {}
    assert sr.SessionRepository._parse_metadata("not-json") == {}


def test_get_session_repo_singleton(monkeypatch):
    monkeypatch.setattr(sr, "_session_repo", None)
    r1 = sr.get_session_repo()
    r2 = sr.get_session_repo()
    assert r1 is r2
    monkeypatch.setattr(sr, "_session_repo", None)
