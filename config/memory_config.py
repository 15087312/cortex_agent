"""
记忆配置 - 分层记忆的 TTL、缓存目录、向量维度
"""
from pydantic import BaseModel


class MemoryConfig(BaseModel):
    """记忆配置类"""
    
    # diskcache 配置（瞬时记忆和工作记忆）
    cache_dir: str = "data/cache"
    cache_size_limit: int = 100 * 1024 * 1024  # 100MB
    
    # SQLite 配置
    sqlite_path: str = "data/memory.db"
    
    # 向量数据库配置（长期记忆语义检索，可选）
    vector_db_host: str = "localhost"
    vector_db_port: int = 6333
    vector_dimension: int = 768
    vector_index_name: str = "long_term_memory"
    
    # 时序数据库配置（可选）
    time_series_db_host: str = "localhost"
    time_series_db_port: int = 8086
    time_series_db_name: str = "humanoid_agi"
    time_series_measurement: str = "memory_timeline"
    
    # TTL 配置（秒）
    instantaneous_memory_ttl: int = 300  # 5 分钟
    working_memory_ttl: int = 3600  # 1 小时
    long_term_memory_ttl: int = 2592000  # 30 天
    
    # 记忆重要性阈值
    importance_threshold: float = 0.7  # 大于此值转为长期记忆
    urgency_threshold: float = 0.9  # 紧急记忆阈值
    
    # 批量操作配置
    batch_size: int = 100
    max_retrieval_results: int = 20
    
    # 清理配置
    cleanup_interval: int = 3600  # 1 小时清理一次
    expired_batch_size: int = 500


def get_memory_config() -> MemoryConfig:
    """获取记忆配置"""
    from config.settings import settings
    return MemoryConfig(
        cache_dir=settings.CACHE_DIR,
        cache_size_limit=settings.CACHE_SIZE_LIMIT,
        sqlite_path=settings.SQLITE_PATH,
        vector_db_host=settings.VECTOR_DB_HOST,
        vector_db_port=settings.VECTOR_DB_PORT,
        vector_dimension=settings.VECTOR_DB_DIMENSION,
        time_series_db_host=getattr(settings, "TIME_SERIES_DB_HOST", "localhost"),
        time_series_db_port=getattr(settings, "TIME_SERIES_DB_PORT", 8086),
        time_series_db_name=getattr(settings, "TIME_SERIES_DB_NAME", "humanoid_agi"),
        instantaneous_memory_ttl=settings.MEMORY_TTL_SHORT,
        working_memory_ttl=settings.MEMORY_TTL_LONG,
        long_term_memory_ttl=30 * 24 * 3600,
        importance_threshold=0.7,
        urgency_threshold=0.9
    )
