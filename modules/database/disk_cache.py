"""
diskcache 缓存层
替换 Redis，零配置、可打包
"""
import os
import json
import time
from typing import Any, Optional, List, Dict
from utils.logger import setup_logger

logger = setup_logger("diskcache")


class DiskCache:
    """
    diskcache 缓存管理器
    
    完全替代 Redis 功能:
    - set/get/delete/exists
    - 列表操作 (lpush/lrange)
    - 哈希操作 (hset/hget/hgetall)
    - 自动过期
    - 持久化到磁盘
    
    优势:
    - 零配置
    - 单文件目录存储
    - 可直接打包进 EXE
    - 性能接近 Redis
    """
    
    def __init__(self):
        self._cache = None
        self._memory_store: Dict[str, Dict] = {}
        self._memory_max_items = 5000  # 内存后备模式条目上限

        cache_dir = os.environ.get("CACHE_DIR", "data/cache")
        os.makedirs(cache_dir, exist_ok=True)
        
        try:
            from diskcache import Cache
            self._cache = Cache(cache_dir, statistics=True)
            logger.info(f"diskcache 初始化成功: {cache_dir}")
        except Exception as e:
            logger.error(f"diskcache 初始化失败: {e}")
            self._cache = None

    def _key(self, prefix: str, key: str) -> str:
        """生成带前缀的键"""
        return f"{prefix}:{key}"
    
    def set(self, key: str, value: Any, prefix: str = "cache", ttl: int = 300) -> bool:
        """设置值"""
        full_key = self._key(prefix, key)

        try:
            if self._cache:
                if ttl > 0:
                    self._cache.set(full_key, value, expire=ttl)
                else:
                    self._cache.set(full_key, value)
            else:
                # 内存后备模式：超过上限时淘汰最旧的条目
                if len(self._memory_store) >= self._memory_max_items and full_key not in self._memory_store:
                    oldest_key = min(
                        self._memory_store.keys(),
                        key=lambda k: self._memory_store[k].get("expire_at") or float("inf")
                    )
                    del self._memory_store[oldest_key]
                self._memory_store[full_key] = {
                    "value": value,
                    "expire_at": time.time() + ttl if ttl > 0 else None
                }
            return True
        except Exception as e:
            logger.error(f"diskcache set 失败: {e}")
            return False
    
    def get(self, key: str, prefix: str = "cache", default: Any = None) -> Any:
        """获取值"""
        full_key = self._key(prefix, key)
        
        try:
            if self._cache:
                value = self._cache.get(full_key, default=default)
                if value is None and default is not None:
                    return default
                return value
            else:
                data = self._memory_store.get(full_key)
                if data:
                    if data["expire_at"] and time.time() > data["expire_at"]:
                        del self._memory_store[full_key]
                        return default
                    return data["value"]
                return default
        except Exception as e:
            logger.error(f"diskcache get 失败: {e}")
            return default
    
    def delete(self, key: str, prefix: str = "cache") -> bool:
        """删除值"""
        full_key = self._key(prefix, key)
        
        try:
            if self._cache:
                del self._cache[full_key]
            else:
                self._memory_store.pop(full_key, None)
            return True
        except (KeyError, Exception) as e:
            logger.debug(f"diskcache delete: {e}")
            return True
    
    def exists(self, key: str, prefix: str = "cache") -> bool:
        """检查键是否存在"""
        full_key = self._key(prefix, key)
        
        try:
            if self._cache:
                return full_key in self._cache
            else:
                return full_key in self._memory_store
        except Exception as e:
            return False
    
    def lpush(self, key: str, value: Any, prefix: str = "list", max_len: int = 1000) -> int:
        """左侧推入"""
        full_key = self._key(prefix, key)
        
        try:
            if self._cache:
                current = self._cache.get(full_key, []) if full_key in self._cache else []
                if not isinstance(current, list):
                    current = []
                current.insert(0, value)
                if len(current) > max_len:
                    current = current[:max_len]
                self._cache.set(full_key, current)
                return len(current)
            else:
                if full_key not in self._memory_store:
                    self._memory_store[full_key] = {"type": "list", "data": []}
                self._memory_store[full_key]["data"].insert(0, value)
                if len(self._memory_store[full_key]["data"]) > max_len:
                    self._memory_store[full_key]["data"] = self._memory_store[full_key]["data"][:max_len]
                return len(self._memory_store[full_key]["data"])
        except Exception as e:
            logger.error(f"diskcache lpush 失败: {e}")
            return 0
    
    def lrange(self, key: str, start: int = 0, end: int = -1, prefix: str = "list") -> List:
        """获取列表范围"""
        full_key = self._key(prefix, key)
        
        try:
            if self._cache:
                items = self._cache.get(full_key, [])
                if not isinstance(items, list):
                    items = []
            else:
                data = self._memory_store.get(full_key, {})
                items = data.get("data", [])
            
            if end == -1:
                return items[start:]
            return items[start:end+1]
        except Exception as e:
            logger.error(f"diskcache lrange 失败: {e}")
            return []
    
    def hset(self, key: str, field: str, value: Any, prefix: str = "hash") -> bool:
        """设置哈希字段"""
        full_key = self._key(prefix, key)
        
        try:
            if self._cache:
                current = self._cache.get(full_key, {}) if full_key in self._cache else {}
                if not isinstance(current, dict):
                    current = {}
                current[field] = value
                self._cache.set(full_key, current)
            else:
                if full_key not in self._memory_store:
                    self._memory_store[full_key] = {"type": "hash", "data": {}}
                self._memory_store[full_key]["data"][field] = value
            return True
        except Exception as e:
            logger.error(f"diskcache hset 失败: {e}")
            return False
    
    def hget(self, key: str, field: str, prefix: str = "hash", default: Any = None) -> Any:
        """获取哈希字段"""
        full_key = self._key(prefix, key)
        
        try:
            if self._cache:
                data = self._cache.get(full_key, {})
                if isinstance(data, dict):
                    return data.get(field, default)
                return default
            else:
                data = self._memory_store.get(full_key, {})
                return data.get("data", {}).get(field, default)
        except Exception as e:
            logger.error(f"diskcache hget 失败: {e}")
            return default
    
    def hgetall(self, key: str, prefix: str = "hash") -> Dict:
        """获取所有哈希字段"""
        full_key = self._key(prefix, key)
        
        try:
            if self._cache:
                data = self._cache.get(full_key, {})
                return data if isinstance(data, dict) else {}
            else:
                data = self._memory_store.get(full_key, {})
                return data.get("data", {})
        except Exception as e:
            logger.error(f"diskcache hgetall 失败: {e}")
            return {}
    
    def search(self, pattern: str, prefix: str = "cache") -> List[str]:
        """搜索键（简单实现）"""
        try:
            search_key = pattern.replace("*", "")
            if self._cache:
                results = []
                for key in self._cache.iterkeys():
                    if key.startswith(prefix):
                        if search_key in key:
                            results.append(key.replace(f"{prefix}:", ""))
                return results
            else:
                prefix_str = f"{prefix}:"
                return [k.replace(prefix_str, "") 
                        for k in self._memory_store.keys() 
                        if k.startswith(prefix_str) and search_key in k]
        except Exception as e:
            logger.error(f"diskcache search 失败: {e}")
            return []
    
    def cache_short_memory(self, memory_id: str, data: dict, ttl: int = 300) -> bool:
        """缓存短期记忆"""
        return self.set(memory_id, data, prefix="short_term", ttl=ttl)
    
    def get_short_memory(self, memory_id: str) -> Optional[dict]:
        """获取缓存的短期记忆"""
        return self.get(memory_id, prefix="short_term")
    
    def cache_query_result(self, query_key: str, results: List[dict], ttl: int = 60) -> bool:
        """缓存查询结果"""
        return self.set(query_key, results, prefix="query", ttl=ttl)
    
    def get_cached_query(self, query_key: str) -> Optional[List[dict]]:
        """获取缓存的查询结果"""
        return self.get(query_key, prefix="query")
    
    def add_to_recent(self, memory_id: str, ttl: int = 3600) -> int:
        """添加到最近访问"""
        return self.lpush(memory_id, time.time(), prefix="recent", max_len=1000)
    
    def get_recent(self, limit: int = 10) -> List[str]:
        """获取最近访问"""
        return self.lrange("recent", 0, limit - 1, prefix="recent")
    
    def flush_prefix(self, prefix: str) -> bool:
        """清空指定前缀的所有键"""
        try:
            if self._cache:
                keys_to_delete = []
                for key in self._cache.iterkeys():
                    if key.startswith(f"{prefix}:"):
                        keys_to_delete.append(key)
                for key in keys_to_delete:
                    del self._cache[key]
            else:
                prefix_str = f"{prefix}:"
                keys_to_delete = [k for k in self._memory_store.keys() if k.startswith(prefix_str)]
                for k in keys_to_delete:
                    del self._memory_store[k]
            return True
        except Exception as e:
            logger.error(f"diskcache flush 失败: {e}")
            return False
    
    def get_stats(self) -> dict:
        """获取缓存统计"""
        try:
            if self._cache:
                stats = self._cache.statistics()
                return {
                    "hits": stats.get("hits", 0),
                    "misses": stats.get("misses", 0),
                    "size": self._cache.volume(),
                    "connected": True,
                    "mode": "diskcache"
                }
            else:
                return {
                    "memory_items": len(self._memory_store),
                    "connected": False,
                    "mode": "memory"
                }
        except Exception as e:
            return {"connected": False, "mode": "error"}
    
    def close(self):
        """关闭连接"""
        if self._cache:
            self._cache.close()


import threading as _threading

_disk_cache = None
_disk_cache_lock = _threading.Lock()


def get_disk_cache() -> "DiskCache":
    """获取磁盘缓存单例"""
    global _disk_cache
    if _disk_cache is None:
        with _disk_cache_lock:
            if _disk_cache is None:
                _disk_cache = DiskCache()
    return _disk_cache


# 向后兼容
disk_cache = get_disk_cache()
