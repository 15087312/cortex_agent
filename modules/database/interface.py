"""Database module interface facade."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DatabasePort(Protocol):
    @property
    def short_term_repo(self) -> Any: ...

    def create_memory_query(self, **kwargs: Any) -> Any:
        """Create a repository memory query object."""

    def deactivate_short_term(
        self,
        owner: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Mark short-term memory rows inactive with optional scope filter."""

    def deactivate_all_short_term(self) -> None:
        """Mark all short-term memory rows inactive."""

    def count_active_short_term(self) -> int:
        """Count active short-term memory rows."""


@dataclass
class RepositoryDatabaseAdapter:
    """Adapter around modules.database repository/connection concrete APIs."""

    @property
    def short_term_repo(self) -> Any:
        from modules.database.repository import short_term_repo

        return short_term_repo

    def create_memory_query(self, **kwargs: Any) -> Any:
        from modules.database.repository import MemoryQuery

        return MemoryQuery(**kwargs)

    def deactivate_short_term(
        self,
        owner: str | None = None,
        session_id: str | None = None,
    ) -> None:
        from modules.database.connection import db_manager
        from modules.database.models import ShortTermMemory as STMModel

        with db_manager.get_session() as session:
            q = session.query(STMModel).filter(STMModel.is_active == True)
            if owner is not None:
                q = q.filter(STMModel.owner == owner)
            if session_id is not None:
                q = q.filter(STMModel.session_id == session_id)
            q.update({"is_active": False})

    def deactivate_all_short_term(self) -> None:
        self.deactivate_short_term(owner=None, session_id=None)

    def count_active_short_term(self) -> int:
        from modules.database.connection import db_manager
        from modules.database.models import ShortTermMemory as STMModel

        with db_manager.get_session() as session:
            return session.query(STMModel).filter(STMModel.is_active == True).count()


_database_port: DatabasePort | None = None


def get_database_port() -> DatabasePort:
    """Return the default database port."""
    global _database_port
    if _database_port is None:
        _database_port = RepositoryDatabaseAdapter()
    return _database_port


def set_database_port(port: DatabasePort | None) -> None:
    """Override the database port, primarily for integration/tests."""
    global _database_port
    _database_port = port or RepositoryDatabaseAdapter()


__all__ = ["DatabasePort", "get_database_port", "set_database_port"]
