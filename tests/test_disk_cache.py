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
