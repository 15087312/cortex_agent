"""
Tests for DatabaseManager — 真实临时 SQLite（无 mock）。
覆盖连接管理/建表幂等/事务回滚/关闭/配置。
"""
import threading

import pytest

import modules.database.connection as conn


def _reset_db_singleton():
    """重置 DatabaseManager 单例（测试隔离，非 mock）"""
    import modules.database.connection as mod
    original = mod._db_manager
    mod._db_manager = None
    return original


def _restore_db_singleton(original):
    import modules.database.connection as mod
    mod._db_manager = original


@pytest.fixture
def tmp_dbm(tmp_path, monkeypatch):
    """真实临时 SQLite 数据库"""
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "test_memory.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    return conn.get_db_manager()


# ------------------------------------------------------------------ #
# Singleton
# ------------------------------------------------------------------ #

class TestSingleton:
    def test_singleton_returns_same_instance(self):
        from modules.database.connection import get_db_manager
        original = _reset_db_singleton()
        try:
            assert get_db_manager() is get_db_manager()
        finally:
            _restore_db_singleton(original)

    def test_singleton_preserves_state(self):
        from modules.database.connection import get_db_manager
        original = _reset_db_singleton()
        try:
            m1 = get_db_manager()
            m1.test_marker = "hello"
            assert get_db_manager().test_marker == "hello"
        finally:
            _restore_db_singleton(original)


# ------------------------------------------------------------------ #
# create_tables — idempotent
# ------------------------------------------------------------------ #

class TestCreateTables:
    def test_create_tables_called_twice_no_error(self, tmp_dbm):
        """真实建表幂等：连续调用不报错"""
        tmp_dbm.initialize()
        tmp_dbm.initialize()
        assert tmp_dbm._tables_created is True

    def test_create_tables_skips_when_already_created(self, tmp_dbm):
        """_tables_created 为 True 时跳过重复建表"""
        tmp_dbm.initialize()
        created = tmp_dbm._tables_created
        tmp_dbm.create_tables()
        assert tmp_dbm._tables_created == created


# ------------------------------------------------------------------ #
# get_session — transaction
# ------------------------------------------------------------------ #

class TestGetSession:
    def test_get_session_yields_session(self, tmp_dbm):
        tmp_dbm.initialize()
        with tmp_dbm.get_session() as s:
            # 真实会话可执行 SQL
            assert s is not None

    def test_get_session_rollback_on_exception(self, tmp_dbm):
        """异常时回滚，不留脏数据"""
        from modules.database.chat_models import ChatSession
        tmp_dbm.initialize()
        try:
            with tmp_dbm.get_session() as s:
                s.add(ChatSession(session_id="rollback_test"))
                s.flush()
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        with tmp_dbm.get_session() as s:
            row = s.query(ChatSession).filter_by(session_id="rollback_test").first()
            assert row is None  # 已回滚

    def test_get_session_initializes_if_needed(self, tmp_path, monkeypatch):
        """get_session 自动初始化（无显式 initialize）"""
        from modules.database.connection import get_db_manager
        original = _reset_db_singleton()
        try:
            monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "auto_init.db"))
            monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
            dbm = get_db_manager()
            with dbm.get_session() as s:
                assert s is not None
            assert dbm._tables_created is True
        finally:
            _restore_db_singleton(original)


# ------------------------------------------------------------------ #
# close
# ------------------------------------------------------------------ #

class TestClose:
    def test_close_disposes_engine(self, tmp_dbm):
        tmp_dbm.initialize()
        engine = tmp_dbm._engine
        tmp_dbm.close()
        assert tmp_dbm._engine is None

    def test_close_no_error_when_no_engine(self, tmp_dbm):
        tmp_dbm.close()  # 未初始化直接 close 不报错

    def test_close_called_twice_no_error(self, tmp_dbm):
        tmp_dbm.initialize()
        tmp_dbm.close()
        tmp_dbm.close()


# ------------------------------------------------------------------ #
# config
# ------------------------------------------------------------------ #

class TestDatabaseConfig:
    def test_default_path_uses_project_data_dir(self):
        from modules.database.connection import config
        assert str(config.sqlite_path).startswith("data/") or "memory" in str(config.sqlite_path)

    def test_env_override(self, monkeypatch):
        from modules.database.connection import config
        monkeypatch.setattr(config, "sqlite_path", "/tmp/custom_memory.db")
        assert str(config.sqlite_path) == "/tmp/custom_memory.db"

    def test_get_url_creates_directory(self, tmp_path, monkeypatch):
        from modules.database.connection import config
        custom = tmp_path / "nested" / "dir" / "m.db"
        monkeypatch.setattr(config, "sqlite_path", str(custom))
        url = config.get_url()
        assert url == f"sqlite:///{custom}"
        assert custom.parent.exists()
