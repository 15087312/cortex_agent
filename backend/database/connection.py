"""
Database connection manager — SQLite with WAL mode.
"""
import os
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from sqlalchemy.pool import NullPool

from backend.utils.logger import setup_logger

logger = setup_logger("database")

Base = declarative_base()

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class DatabaseManager:
    """SQLite database manager with WAL mode."""

    def __init__(self):
        self._engine = None
        self._session_factory = None
        self._tables_created = False

    def initialize(self, db_path: str = None):
        if self._engine is not None:
            return

        if not db_path:
            db_path = os.environ.get("MEMORY_DB_PATH", str(PROJECT_ROOT / "data" / "memory.db"))

        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        url = f"sqlite:///{db_path}"
        self._engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=NullPool,
            echo=False,
        )

        @event.listens_for(self._engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        logger.info(f"SQLite initialized (WAL mode): {db_path}")

        self._session_factory = sessionmaker(
            bind=self._engine,
            autocommit=False,
            autoflush=False,
        )

        self.create_tables()

    def create_tables(self):
        if self._tables_created:
            return
        from . import chat_models
        Base.metadata.create_all(self._engine)
        self._tables_created = True
        logger.info("Database tables created")

    def get_session(self) -> Session:
        if self._session_factory is None:
            self.initialize()
        return self._session_factory()

    def close(self):
        if self._engine:
            self._engine.dispose()
            self._engine = None
            self._session_factory = None


# Global singleton
_db_manager: DatabaseManager = None


def get_db_manager() -> DatabaseManager:
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager
