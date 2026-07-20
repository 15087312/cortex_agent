"""
数据库连接管理
只使用 SQLite（零配置、可打包）
"""
import os
from pathlib import Path
from contextlib import contextmanager
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from sqlalchemy.pool import NullPool
from utils.logger import setup_logger

logger = setup_logger("database")

Base = declarative_base()

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class DatabaseConfig:
    """数据库配置"""
    
    def __init__(self):
        self.sqlite_path = os.environ.get("SQLITE_PATH")
        if not self.sqlite_path:
            self.sqlite_path = str(PROJECT_ROOT / "data" / "memory.db")
    
    def get_url(self) -> str:
        """获取数据库 URL"""
        db_dir = os.path.dirname(self.sqlite_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        else:
            os.makedirs(PROJECT_ROOT / "data", exist_ok=True)
        return f"sqlite:///{self.sqlite_path}"


config = DatabaseConfig()


class DatabaseManager:
    """数据库管理器"""

    def __init__(self):
        self._engine = None
        self._session_factory = None
        self._tables_created = False

    def initialize(self):
        """初始化数据库"""
        if self._engine is not None:
            return
        
        url = config.get_url()
        
        self._engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=NullPool,
            echo=False
        )
        
        # 启用 WAL 模式：支持并发读写，不锁库
        @event.listens_for(self._engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()
        
        logger.debug(f"SQLite 数据库初始化 (WAL模式): {config.sqlite_path}")
        
        self._session_factory = sessionmaker(
            bind=self._engine,
            autocommit=False,
            autoflush=False
        )
        
        self.create_tables()
    
    def create_tables(self):
        """创建表（幂等：重复调用自动跳过）"""
        if self._tables_created:
            return
        from . import models
        from . import chat_models  # 会话持久化表
        Base.metadata.create_all(self._engine)
        self._migrate()
        self._tables_created = True
        logger.info("数据库表创建完成")

    def _migrate(self):
        """增量迁移：为已有数据库添加新列和索引"""
        migrations = [
            ("short_term_memories", "session_id", "VARCHAR(100) DEFAULT ''"),
        ]
        for table, column, col_def in migrations:
            try:
                with self._engine.connect() as conn:
                    conn.execute(
                        text(
                            f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"
                        )
                    )
                    conn.commit()
                    logger.info(f"迁移: {table}.{column} 已添加")
            except Exception as e:
                logger.debug(f"迁移 {table}.{column} 跳过 (可能已存在): {e}")

        # 添加查询索引
        index_migrations = [
            "CREATE INDEX IF NOT EXISTS idx_stm_memory_type ON short_term_memories(memory_type)",
            "CREATE INDEX IF NOT EXISTS idx_stm_owner ON short_term_memories(owner)",
            "CREATE INDEX IF NOT EXISTS idx_stm_session_id ON short_term_memories(session_id)",
            "CREATE INDEX IF NOT EXISTS idx_stm_created_at ON short_term_memories(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_stm_is_active ON short_term_memories(is_active)",
        ]
        for sql in index_migrations:
            try:
                with self._engine.connect() as conn:
                    conn.execute(text(sql))
                    conn.commit()
            except Exception as e:
                logger.debug(f"索引创建跳过 (可能已存在): {e}")
    
    @contextmanager
    def get_session(self) -> Session:
        """获取数据库会话"""
        if self._session_factory is None:
            self.initialize()
        
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()
    
    def get_session_without_commit(self) -> Session:
        """获取数据库会话（不自动提交）"""
        if self._session_factory is None:
            self.initialize()
        return self._session_factory()
    
    def close(self):
        """关闭数据库"""
        if self._engine:
            self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("数据库连接已关闭")


import threading as _threading

_db_manager = None
_db_manager_lock = _threading.Lock()


def get_db_manager() -> DatabaseManager:
    """获取数据库管理器单例"""
    global _db_manager
    if _db_manager is None:
        with _db_manager_lock:
            if _db_manager is None:
                _db_manager = DatabaseManager()
    return _db_manager


# 向后兼容
db_manager = get_db_manager()
