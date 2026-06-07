"""
Tests for DatabaseManager — database connection and session management.
"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


# ------------------------------------------------------------------ #
# Singleton pattern
# ------------------------------------------------------------------ #

class TestSingleton:
    def test_singleton_returns_same_instance(self):
        """DatabaseManager() always returns the same instance."""
        from modules.database.connection import DatabaseManager
        # Reset singleton for clean test
        original = DatabaseManager._instance
        DatabaseManager._instance = None
        try:
            m1 = DatabaseManager()
            m2 = DatabaseManager()
            assert m1 is m2
        finally:
            DatabaseManager._instance = original

    def test_singleton_preserves_state(self):
        """Setting an attribute on one instance is visible on another."""
        from modules.database.connection import DatabaseManager
        original = DatabaseManager._instance
        DatabaseManager._instance = None
        try:
            m1 = DatabaseManager()
            m1.test_marker = "hello"
            m2 = DatabaseManager()
            assert m2.test_marker == "hello"
        finally:
            DatabaseManager._instance = original


# ------------------------------------------------------------------ #
# create_tables — idempotent
# ------------------------------------------------------------------ #

class TestCreateTables:
    def test_create_tables_called_twice_no_error(self):
        """create_tables is idempotent — calling it twice does not fail."""
        from modules.database.connection import DatabaseManager
        original = DatabaseManager._instance
        original_flag = DatabaseManager._tables_created
        DatabaseManager._instance = None
        DatabaseManager._tables_created = False
        try:
            mgr = DatabaseManager()
            # Patch initialize so we don't hit real DB
            mgr._engine = MagicMock()
            mgr._session_factory = MagicMock()
            with patch("modules.database.connection.Base"):
                mgr.create_tables()
                # Second call should be a no-op (flag is True)
                mgr.create_tables()
        finally:
            DatabaseManager._instance = original
            DatabaseManager._tables_created = original_flag

    def test_create_tables_skips_when_already_created(self):
        """create_tables returns early when _tables_created flag is True."""
        from modules.database.connection import DatabaseManager
        original = DatabaseManager._instance
        original_flag = DatabaseManager._tables_created
        DatabaseManager._instance = None
        DatabaseManager._tables_created = False
        try:
            mgr = DatabaseManager()
            mgr._engine = MagicMock()
            with patch("modules.database.connection.Base") as mock_base:
                DatabaseManager._tables_created = True
                mgr.create_tables()
                # create_all should not be called because flag was already True
                mock_base.metadata.create_all.assert_not_called()
        finally:
            DatabaseManager._instance = original
            DatabaseManager._tables_created = original_flag


# ------------------------------------------------------------------ #
# get_session — returns a valid session
# ------------------------------------------------------------------ #

class TestGetSession:
    def test_get_session_yields_session(self):
        """get_session context manager yields a session object."""
        from modules.database.connection import DatabaseManager
        original = DatabaseManager._instance
        DatabaseManager._instance = None
        try:
            mgr = DatabaseManager()
            mock_factory = MagicMock()
            mock_session = MagicMock()
            mock_factory.return_value = mock_session
            mgr._session_factory = mock_factory
            mgr._engine = MagicMock()

            with mgr.get_session() as session:
                assert session is mock_session

            mock_session.commit.assert_called_once()
            mock_session.close.assert_called_once()
        finally:
            DatabaseManager._instance = original

    def test_get_session_rollback_on_exception(self):
        """get_session rolls back and re-raises on exception."""
        from modules.database.connection import DatabaseManager
        original = DatabaseManager._instance
        DatabaseManager._instance = None
        try:
            mgr = DatabaseManager()
            mock_factory = MagicMock()
            mock_session = MagicMock()
            mock_factory.return_value = mock_session
            mgr._session_factory = mock_factory
            mgr._engine = MagicMock()

            with pytest.raises(ValueError, match="test error"):
                with mgr.get_session() as session:
                    raise ValueError("test error")

            mock_session.rollback.assert_called_once()
            mock_session.close.assert_called_once()
            mock_session.commit.assert_not_called()
        finally:
            DatabaseManager._instance = original

    def test_get_session_initializes_if_needed(self):
        """get_session calls initialize() when _session_factory is None."""
        from modules.database.connection import DatabaseManager
        original = DatabaseManager._instance
        DatabaseManager._instance = None
        try:
            mgr = DatabaseManager()
            mgr._engine = MagicMock()
            # Simulate _session_factory being None (first call)
            mock_factory = MagicMock()
            mock_session = MagicMock()
            mock_factory.return_value = mock_session

            with patch.object(mgr, "initialize") as mock_init:
                # After initialize is called, set the factory
                def set_factory():
                    mgr._session_factory = mock_factory
                mock_init.side_effect = set_factory

                with mgr.get_session() as session:
                    assert session is mock_session

                mock_init.assert_called_once()
        finally:
            DatabaseManager._instance = original


# ------------------------------------------------------------------ #
# close — works without error
# ------------------------------------------------------------------ #

class TestClose:
    def test_close_disposes_engine(self):
        """close() disposes the engine and resets factory."""
        from modules.database.connection import DatabaseManager
        original = DatabaseManager._instance
        DatabaseManager._instance = None
        try:
            mgr = DatabaseManager()
            mock_engine = MagicMock()
            mgr._engine = mock_engine
            mgr._session_factory = MagicMock()

            mgr.close()

            mock_engine.dispose.assert_called_once()
            assert mgr._engine is None
            assert mgr._session_factory is None
        finally:
            DatabaseManager._instance = original

    def test_close_no_error_when_no_engine(self):
        """close() does not raise when engine is already None."""
        from modules.database.connection import DatabaseManager
        original = DatabaseManager._instance
        DatabaseManager._instance = None
        try:
            mgr = DatabaseManager()
            mgr._engine = None
            mgr._session_factory = None
            # Should not raise
            mgr.close()
        finally:
            DatabaseManager._instance = original

    def test_close_called_twice_no_error(self):
        """Calling close() twice does not raise."""
        from modules.database.connection import DatabaseManager
        original = DatabaseManager._instance
        DatabaseManager._instance = None
        try:
            mgr = DatabaseManager()
            mgr._engine = MagicMock()
            mgr._session_factory = MagicMock()
            mgr.close()
            # Second call: engine is None, should be safe
            mgr.close()
        finally:
            DatabaseManager._instance = original


# ------------------------------------------------------------------ #
# DatabaseConfig
# ------------------------------------------------------------------ #

class TestDatabaseConfig:
    def test_default_path_uses_project_data_dir(self):
        """Default sqlite_path falls back to project data directory."""
        from modules.database.connection import DatabaseConfig
        with patch.dict("os.environ", {}, clear=True):
            cfg = DatabaseConfig()
            assert "memory.db" in cfg.sqlite_path
            assert "data" in cfg.sqlite_path

    def test_env_override(self):
        """SQLITE_PATH env var overrides the default."""
        from modules.database.connection import DatabaseConfig
        with patch.dict("os.environ", {"SQLITE_PATH": "/custom/path/test.db"}):
            cfg = DatabaseConfig()
            assert cfg.sqlite_path == "/custom/path/test.db"

    def test_get_url_creates_directory(self):
        """get_url creates the parent directory if it doesn't exist."""
        from modules.database.connection import DatabaseConfig
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "subdir", "test.db")
            cfg = DatabaseConfig()
            cfg.sqlite_path = db_path
            url = cfg.get_url()
            assert url.startswith("sqlite:///")
            assert os.path.isdir(os.path.join(td, "subdir"))
