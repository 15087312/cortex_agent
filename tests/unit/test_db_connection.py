"""database/connection 扩展测试：unit 级（真实 tmp SQLite），补齐 78%→90%+ 的缺口

覆盖：DatabaseConfig 默认/环境变量/无目录、initialize 幂等、迁移成功路径、
get_session 回滚、get_session_without_commit、close、get_db_manager 竞态。
"""
import sqlite3
import threading

import pytest

import modules.database.connection as conn
from modules.database.connection import DatabaseConfig, DatabaseManager, get_db_manager


@pytest.fixture
def tmp_dbm(tmp_path, monkeypatch):
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "unit_memory.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    return get_db_manager()


# ── DatabaseConfig ───────────────────────────────────────────────────────────

def test_config_env_override(monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", "/tmp/custom_env.db")
    cfg = DatabaseConfig()
    assert cfg.sqlite_path == "/tmp/custom_env.db"


def test_config_default_path(monkeypatch):
    monkeypatch.delenv("SQLITE_PATH", raising=False)
    cfg = DatabaseConfig()
    assert cfg.sqlite_path


def test_get_url_without_dir(monkeypatch):
    monkeypatch.setattr(conn.config, "sqlite_path", "plain_unit.db")
    url = conn.config.get_url()
    assert url == "sqlite:///plain_unit.db"


# ── initialize 幂等 ──────────────────────────────────────────────────────────

def test_initialize_idempotent(tmp_dbm):
    tmp_dbm.initialize()
    engine = tmp_dbm._engine
    tmp_dbm.initialize()  # 二次调用直接 return（52）
    assert tmp_dbm._engine is engine


def test_create_tables_skips_when_created(tmp_dbm):
    tmp_dbm.initialize()
    tmp_dbm.create_tables()  # _tables_created=True → 直接 return（84）
    assert tmp_dbm._tables_created is True


# ── 迁移 ─────────────────────────────────────────────────────────────────────

def _precreate_stm(db_path, with_session_id):
    raw = sqlite3.connect(db_path)
    if with_session_id:
        raw.execute(
            """CREATE TABLE short_term_memories (
                id INTEGER PRIMARY KEY, memory_type TEXT, owner TEXT,
                session_id TEXT, created_at TEXT, is_active INTEGER)"""
        )
    else:
        raw.execute(
            """CREATE TABLE short_term_memories (
                id INTEGER PRIMARY KEY, memory_type TEXT, owner TEXT,
                created_at TEXT, is_active INTEGER)"""
        )
    raw.commit()
    raw.close()


def test_migrate_adds_column_and_indexes(tmp_path, monkeypatch):
    """迁移成功路径：ALTER 成功 + 索引创建 commit（103-104, 118-120）"""
    db = str(tmp_path / "migrate.db")
    _precreate_stm(db, with_session_id=False)
    monkeypatch.setattr(conn.config, "sqlite_path", db)
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    dm = get_db_manager()
    dm.initialize()
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(short_term_memories)")}
    assert "session_id" in cols
    idx = {r[1] for r in sqlite3.connect(db).execute("PRAGMA index_list(short_term_memories)")}
    assert any("idx_stm_" in i for i in idx)


def test_migrate_column_exists_skipped(tmp_path, monkeypatch):
    """迁移失败路径：列已存在 → ALTER 报错被捕获（105-106）"""
    db = str(tmp_path / "migrate_exist.db")
    _precreate_stm(db, with_session_id=True)
    monkeypatch.setattr(conn.config, "sqlite_path", db)
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    dm = get_db_manager()
    dm.initialize()  # ALTER ADD COLUMN session_id 已存在 → 跳过，不抛异常
    assert dm._tables_created is True


# ── get_session ──────────────────────────────────────────────────────────────

def test_get_session_rollback_and_raise(tmp_dbm):
    from modules.database.chat_models import ChatSession
    tmp_dbm.initialize()
    with pytest.raises(RuntimeError):
        with tmp_dbm.get_session() as s:
            s.add(ChatSession(session_id="rb_unit"))
            s.flush()
            raise RuntimeError("boom")
    with tmp_dbm.get_session() as s:
        row = s.query(ChatSession).filter_by(session_id="rb_unit").first()
        assert row is None


def test_get_session_without_commit(tmp_dbm):
    """get_session_without_commit：无 factory 时自动初始化并返回会话（142->143）"""
    tmp_dbm._session_factory = None
    tmp_dbm._engine = None
    s = tmp_dbm.get_session_without_commit()
    assert s is not None
    s.close()


def test_get_session_without_commit_already_initialized(tmp_dbm):
    """factory 已存在 → 跳过初始化直接返回（142->144）"""
    tmp_dbm.initialize()
    s = tmp_dbm.get_session_without_commit()
    assert s is not None
    s.close()


def test_get_session_auto_initialize(tmp_path, monkeypatch):
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "auto.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    dm = get_db_manager()
    with dm.get_session() as s:
        assert s is not None
    assert dm._tables_created is True


# ── close ────────────────────────────────────────────────────────────────────

def test_close_disposes(tmp_dbm):
    tmp_dbm.initialize()
    tmp_dbm.close()
    assert tmp_dbm._engine is None
    assert tmp_dbm._session_factory is None


def test_close_without_engine(tmp_dbm):
    tmp_dbm.close()  # _engine None → 不报错（148）

# ── get_db_manager 竞态 ─────────────────────────────────────────────────────

def test_get_db_manager_inner_race(monkeypatch):
    """内层检查发现 _db_manager 已被并发设置 → 直接返回（166->168）"""
    existing = DatabaseManager()
    monkeypatch.setattr(conn, "_db_manager", None)

    class FakeLock:
        def __enter__(self):
            conn._db_manager = existing
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(conn, "_db_manager_lock", FakeLock())
    assert get_db_manager() is existing


def test_get_db_manager_already_set(monkeypatch):
    """_db_manager 已存在 → 外层检查直接返回（164->168）"""
    existing = DatabaseManager()
    monkeypatch.setattr(conn, "_db_manager", existing)
    assert get_db_manager() is existing
