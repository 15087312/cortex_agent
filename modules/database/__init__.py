"""
数据库模块

提供:
- SQLite 持久化
- diskcache 缓存层
- 记忆仓储
- 统一记忆系统
"""
from .connection import DatabaseManager, db_manager, Base, DatabaseConfig
from .disk_cache import DiskCache, disk_cache
from .models import (
    ShortTermMemory,
    LongTermMemory,
    ExperienceMemory,
    MemoryRegion,
    MemoryShortcut,
    MemoryPermission,
    MemoryRelation
)
from .repository import (
    ShortTermMemoryRepository,
    ExperienceRepository,
    MemoryQuery,
    short_term_repo,
    experience_repo
)
__all__ = [
    "DatabaseManager",
    "db_manager",
    "Base",
    "DatabaseConfig",
    "DiskCache",
    "disk_cache",

    "ShortTermMemory",
    "LongTermMemory",
    "ExperienceMemory",
    "MemoryRegion",
    "MemoryShortcut",
    "MemoryPermission",
    "MemoryRelation",
    "ShortTermMemoryRepository",
    "ExperienceRepository",
    "MemoryQuery",
    "short_term_repo",
    "experience_repo",
]
