"""DiskCache 测试（此前 22% 覆盖）：缓存/列表/哈希 API"""
import pytest


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    from modules.database.disk_cache import DiskCache
    return DiskCache()


def test_set_get_delete(cache):
    cache.set("k1", {"a": 1})
    assert cache.get("k1") == {"a": 1}
    assert cache.exists("k1")
    cache.delete("k1")
    assert not cache.exists("k1")
    assert cache.get("k1", default="x") == "x"


def test_prefix_isolation(cache):
    cache.set("k", "v1", prefix="p1")
    assert cache.get("k", prefix="p1") == "v1"
    assert cache.get("k", prefix="p2") is None


def test_ttl_zero_is_permanent(cache):
    # ttl<=0 按 diskcache 语义视为永久（不设过期）
    assert cache.set("k", "v", ttl=0) is True
    assert cache.get("k") == "v"


def test_list_operations(cache):
    cache.lpush("l1", 1)
    cache.lpush("l1", 2)
    cache.lpush("l1", 3)
    items = cache.lrange("l1")
    assert 3 in items and 2 in items and 1 in items


def test_hash_operations(cache):
    cache.hset("h1", "f1", "v1")
    cache.hset("h1", "f2", "v2")
    assert cache.hget("h1", "f1") == "v1"
    assert cache.hget("h1", "missing", default="d") == "d"
    all_data = cache.hgetall("h1")
    assert all_data["f1"] == "v1" and all_data["f2"] == "v2"


def _mem_cache():
    from modules.database.disk_cache import DiskCache
    c = DiskCache.__new__(DiskCache)
    c._cache = None
    c._memory_store = {}
    c._memory_max_items = 5000
    return c


def test_mem_set_get_delete():
    c = _mem_cache()
    assert c.set("k1", "v1", ttl=300) is True
    assert c.get("k1") == "v1"
    assert c.exists("k1") is True
    assert c.delete("k1") is True
    assert c.get("k1", default="d") == "d"


def test_mem_ttl_expire():
    c = _mem_cache()
    import time as _time
    c.set("k", "v", ttl=1)
    c._memory_store["cache:k"]["expire_at"] = _time.time() - 100  # 强制过期
    assert c.get("k", default="gone") == "gone"


def test_mem_ttl_zero_permanent():
    c = _mem_cache()
    c.set("k", "v", ttl=0)
    import time as _time
    assert c.get("k") == "v"


def test_mem_list_ops():
    c = _mem_cache()
    c.lpush("lst", "a", max_len=3)
    c.lpush("lst", "b", max_len=3)
    c.lpush("lst", "c", max_len=3)
    c.lpush("lst", "d", max_len=3)  # 超上限截断
    items = c.lrange("lst", 0, -1)
    assert len(items) == 3


def test_mem_hash_ops():
    c = _mem_cache()
    assert c.hset("h", "f1", "v1") is True
    assert c.hget("h", "f1") == "v1"
    assert c.hgetall("h") == {"f1": "v1"}
    assert c.hget("h", "nope", default="x") == "x"


def test_mem_search():
    c = _mem_cache()
    c.set("abc", 1, prefix="cache")
    c.set("abd", 2, prefix="cache")
    assert "abc" in c.search("ab*", prefix="cache")
    assert "abd" in c.search("ab*", prefix="cache")


def test_mem_recent_and_flush():
    c = _mem_cache()
    c.add_to_recent("m1")
    c.add_to_recent("m2")
    assert "recent:m1" in c._memory_store
    assert "recent:m2" in c._memory_store
    assert c.flush_prefix("recent") is True
    assert c._memory_store == {}
