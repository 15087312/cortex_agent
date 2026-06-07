"""
数据库连接基础设施
提供 SQLite + diskcache 支持（零配置、可打包）
"""

# diskcache 客户端
from modules.database.disk_cache import disk_cache, DiskCache

__all__ = [
    "disk_cache",
    "DiskCache"
]
