"""FileHistory — 文件修改历史（SQLite）单元测试

覆盖: 单例 / 建库 / 写入（initial & version 递增/碰撞）/ 读取 / 回滚 / 清理 / 关闭。
外部边界（文件系统写入）用 tmp_path + monkeypatch 隔离。
"""
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from modules.cortex.file_history import FileHistory, get_file_history


@pytest.fixture
def hist(tmp_path):
    return FileHistory(db_path=str(tmp_path / "h" / "files.db"))


@pytest.fixture
def sess_file(tmp_path):
    return str(tmp_path / "proj" / "a.py")


def _set_created_at(hist, session_id, path, version, ts):
    """直接改写 created_at，避免同秒插入导致排序不稳定"""
    conn = hist._get_conn()
    conn.execute(
        "UPDATE file_versions SET created_at=? WHERE session_id=? AND path=? AND version=?",
        (ts, session_id, path, version),
    )
    conn.commit()


# ── 构造 / 单例 ────────────────────────────────────────────────────────────

def test_init_creates_parent_dir(tmp_path):
    db = tmp_path / "nested" / "deep" / "files.db"
    assert not db.parent.exists()
    FileHistory(db_path=str(db))
    assert db.parent.is_dir()


def test_default_db_path(monkeypatch, tmp_path):
    monkeypatch.setattr("modules.cortex.file_history._DB_PATH", tmp_path / "def" / "x.db")
    fh = FileHistory()
    assert fh._db_path == str(tmp_path / "def" / "x.db")


def test_get_instance_singleton(monkeypatch, tmp_path):
    from modules.cortex import file_history as mod
    monkeypatch.setattr(mod, "_DB_PATH", tmp_path / "singleton" / "files.db")
    mod.FileHistory._instance = None
    try:
        a = FileHistory.get_instance()
        b = FileHistory.get_instance()
        assert a is b
        assert a._db_path == str(tmp_path / "singleton" / "files.db")
    finally:
        mod.FileHistory._instance = None


def test_get_instance_reuses_existing(monkeypatch, tmp_path):
    from modules.cortex import file_history as mod
    fake = object()
    mod.FileHistory._instance = fake
    try:
        assert FileHistory.get_instance() is fake
    finally:
        mod.FileHistory._instance = None


def test_get_file_history_function(monkeypatch, tmp_path):
    from modules.cortex import file_history as mod
    monkeypatch.setattr(mod, "_DB_PATH", tmp_path / "fn" / "files.db")
    mod.FileHistory._instance = None
    try:
        a = get_file_history()
        assert isinstance(a, FileHistory)
        assert get_file_history() is a
    finally:
        mod.FileHistory._instance = None


# ── 建库 ───────────────────────────────────────────────────────────────────

def test_get_conn_creates_schema(hist):
    conn = hist._get_conn()
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='file_versions'"
    ).fetchall()
    assert tables
    pragma = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert pragma == "wal"
    conn.close()


def test_init_db_idempotent(hist):
    hist._get_conn()
    hist._init_db()  # 第二次调用不应报错
    conn = hist._get_conn()
    indexes = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    names = {r[0] for r in indexes}
    assert {"idx_fv_session", "idx_fv_path", "idx_fv_session_path"} <= names


# ── 写入: record_initial ───────────────────────────────────────────────────

def test_record_initial_returns_version_id(hist, sess_file):
    vid = hist.record_initial("s1", sess_file, "original")
    assert vid
    row = hist.get_version(vid)
    assert row["version"] == "initial"
    assert row["content"] == "original"
    assert row["session_id"] == "s1"


def test_record_initial_returns_existing_on_duplicate(hist, sess_file):
    vid1 = hist.record_initial("s1", sess_file, "original")
    vid2 = hist.record_initial("s1", sess_file, "other")
    assert vid1 == vid2
    rows = hist.list_versions("s1", sess_file)
    assert len(rows) == 1


# ── 写入: record_version ───────────────────────────────────────────────────

def test_record_version_first_creates_initial_then_v1(hist, sess_file):
    vid = hist.record_version("s1", sess_file, "content1")
    assert vid
    versions = hist.list_versions("s1", sess_file)
    assert [v["version"] for v in versions] == ["initial", "v1"]
    assert versions[0]["content"] == ""
    assert versions[1]["content"] == "content1"


def test_record_version_from_initial_is_v1(hist, sess_file):
    hist.record_initial("s1", sess_file, "orig")
    _set_created_at(hist, "s1", sess_file, "initial", int(time.time()) - 1000)
    hist.record_version("s1", sess_file, "new1")
    hist.record_version("s1", sess_file, "new2")
    versions = hist.list_versions("s1", sess_file)
    assert [v["version"] for v in versions] == ["initial", "v1", "v2"]


def test_record_version_with_invalid_existing_version_falls_back_to_v1(hist, sess_file):
    conn = hist._get_conn()
    conn.execute(
        "INSERT INTO file_versions (id, session_id, path, content, version, created_at) "
        "VALUES ('id-x', 's1', ?, 'x', 'weird', ?)",
        (sess_file, int(time.time())),
    )
    conn.commit()
    vid = hist.record_version("s1", sess_file, "content")
    assert vid
    row = hist.get_version(vid)
    assert row["version"] == "v1"


def test_record_version_skips_existing_version(hist, sess_file):
    hist.record_initial("s1", sess_file, "orig")
    conn = hist._get_conn()
    conn.execute(
        "INSERT INTO file_versions (id, session_id, path, content, version, created_at) "
        "VALUES ('id-v1', 's1', ?, 'old-v1', 'v1', ?)",
        (sess_file, int(time.time()) - 1000),
    )
    conn.commit()
    vid = hist.record_version("s1", sess_file, "new")
    row = hist.get_version(vid)
    assert row["version"] == "v2"


# ── 读取 ───────────────────────────────────────────────────────────────────

def test_get_version_missing(hist):
    assert hist.get_version("nope") is None


def test_get_latest(hist, sess_file):
    hist.record_initial("s1", sess_file, "orig")
    _set_created_at(hist, "s1", sess_file, "initial", int(time.time()) - 1000)
    hist.record_version("s1", sess_file, "new1")
    latest = hist.get_latest("s1", sess_file)
    assert latest["version"] == "v1"
    assert latest["content"] == "new1"


def test_get_latest_missing(hist, sess_file):
    assert hist.get_latest("s1", sess_file) is None


def test_get_initial(hist, sess_file):
    hist.record_initial("s1", sess_file, "orig")
    initial = hist.get_initial("s1", sess_file)
    assert initial["content"] == "orig"


def test_get_initial_missing(hist, sess_file):
    assert hist.get_initial("s1", sess_file) is None


def test_list_versions_ordered_asc(hist, sess_file):
    hist.record_initial("s1", sess_file, "orig")
    hist.record_version("s1", sess_file, "new1")
    hist.record_version("s1", sess_file, "new2")
    versions = hist.list_versions("s1", sess_file)
    assert [v["version"] for v in versions] == ["initial", "v1", "v2"]


def test_list_session_files_returns_latest_per_file(hist, sess_file):
    other = str(Path(sess_file).with_name("b.py"))
    hist.record_initial("s1", sess_file, "o1")
    _set_created_at(hist, "s1", sess_file, "initial", int(time.time()) - 100)
    hist.record_version("s1", sess_file, "n1")
    _set_created_at(hist, "s1", sess_file, "v1", int(time.time()) - 50)
    hist.record_version("s1", other, "n2")
    files = hist.list_session_files("s1")
    assert {f["path"] for f in files} == {sess_file, other}
    by_path = {f["path"]: f["version"] for f in files}
    assert by_path[sess_file] == "v1"


def test_get_all_versions(hist, sess_file):
    hist.record_initial("s1", sess_file, "orig")
    hist.record_version("s1", sess_file, "new1")
    hist.record_version("s1", sess_file, "new2")
    all_v = hist.get_all_versions("s1")
    assert len(all_v) == 3


def test_get_all_versions_empty(hist):
    assert hist.get_all_versions("nobody") == []


# ── 回滚 ───────────────────────────────────────────────────────────────────

def test_rollback_file_restores_initial(hist, tmp_path):
    f = tmp_path / "proj" / "a.py"
    f.parent.mkdir(parents=True)
    f.write_text("original", encoding="utf-8")
    hist.record_initial("s1", str(f), "original")
    _set_created_at(hist, "s1", str(f), "initial", int(time.time()) - 1000)
    hist.record_version("s1", str(f), "modified")
    assert hist.get_latest("s1", str(f))["content"] == "modified"
    result = hist.rollback_file("s1", str(f))
    assert result == "original"
    assert f.read_text(encoding="utf-8") == "original"


def test_rollback_file_no_initial_returns_none(hist, tmp_path):
    f = tmp_path / "proj" / "a.py"
    assert hist.rollback_file("s1", str(f)) is None


def test_rollback_file_write_failure_returns_none(hist, tmp_path):
    f = tmp_path / "proj" / "a.py"
    f.parent.mkdir(parents=True)
    f.write_text("original", encoding="utf-8")
    hist.record_initial("s1", str(f), "original")
    with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
        assert hist.rollback_file("s1", str(f)) is None


def test_rollback_session_mixed_results(hist, tmp_path):
    ok = tmp_path / "proj" / "ok.py"
    ok.parent.mkdir(parents=True)
    ok.write_text("original", encoding="utf-8")
    hist.record_initial("s1", str(ok), "original")
    hist.record_version("s1", str(ok), "changed")

    results = hist.rollback_session("s1")
    assert results == {str(ok): "restored"}
    assert ok.read_text(encoding="utf-8") == "original"


def test_rollback_session_no_initial_status(hist, tmp_path):
    f = tmp_path / "proj" / "only_version.py"
    f.parent.mkdir(parents=True)
    f.write_text("v", encoding="utf-8")
    conn = hist._get_conn()
    conn.execute(
        "INSERT INTO file_versions (id, session_id, path, content, version, created_at) "
        "VALUES ('bare-v1', 's9', ?, 'v1-content', 'v1', ?)",
        (str(f), int(time.time())),
    )
    conn.commit()
    results = hist.rollback_session("s9")
    assert results == {str(f): "no_initial_version"}


def test_rollback_session_error_status(hist, tmp_path):
    f = tmp_path / "proj" / "err.py"
    f.parent.mkdir(parents=True)
    f.write_text("original", encoding="utf-8")
    hist.record_initial("s1", str(f), "original")
    with patch("pathlib.Path.write_text", side_effect=PermissionError("denied")):
        results = hist.rollback_session("s1")
    assert results[str(f)].startswith("error: ")


# ── 清理 / 关闭 ────────────────────────────────────────────────────────────

def test_delete_session_history(hist, sess_file):
    hist.record_initial("s1", sess_file, "orig")
    hist.record_version("s1", sess_file, "new")
    hist.record_initial("s2", sess_file, "other")
    deleted = hist.delete_session_history("s1")
    assert deleted == 2
    assert hist.get_all_versions("s1") == []
    assert len(hist.get_all_versions("s2")) == 1


def test_delete_session_history_unknown_returns_zero(hist):
    assert hist.delete_session_history("ghost") == 0


def test_close_without_conn(hist):
    hist.close()  # _conn 为 None，不应报错


def test_close_with_conn_reconnect(hist):
    conn1 = hist._get_conn()
    hist.close()
    assert hist._conn is None
    conn2 = hist._get_conn()
    assert conn2 is not conn1
