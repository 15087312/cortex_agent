"""ApiLogStore 测试（此前 51% 覆盖）：请求日志落库/查询/统计"""
import pytest

from modules.management.api_log_store import ApiLogStore


@pytest.fixture
def store(tmp_path):
    s = ApiLogStore(path=str(tmp_path / "api_log.db"))
    yield s
    s._stop.set()


def test_add_flush_query(store):
    store.add("GET", "/health", 200, 1.5)
    store.add("POST", "/tools/call", 500, 10.0)
    store.flush()
    rows = store.query()
    assert len(rows) == 2
    methods = {r["method"] for r in rows}
    assert methods == {"GET", "POST"}


def test_query_filter_by_method(store):
    store.add("GET", "/a", 200, 1.0)
    store.add("POST", "/b", 200, 2.0)
    store.flush()
    rows = store.query(method="GET")
    assert len(rows) == 1
    assert rows[0]["method"] == "GET"


def test_query_filter_by_path(store):
    store.add("GET", "/health", 200, 1.0)
    store.add("GET", "/config", 200, 2.0)
    store.flush()
    rows = store.query(path="health")
    assert len(rows) == 1
    assert rows[0]["path"] == "/health"


def test_query_filter_by_status(store):
    store.add("GET", "/a", 200, 1.0)
    store.add("POST", "/b", 500, 2.0)
    store.flush()
    rows = store.query(status=500)
    assert len(rows) == 1
    assert rows[0]["status"] == 500


def test_count(store):
    store.add("GET", "/a", 200, 1.0)
    store.add("GET", "/b", 200, 2.0)
    store.flush()
    assert store.count() == 2
    assert store.count(method="GET") == 2


def test_stats(store):
    store.add("GET", "/a", 200, 5.0)
    store.add("GET", "/b", 404, 10.0)
    store.flush()
    st = store.stats()
    assert "total" in st
    assert st["total"] >= 2
    assert "avg_ms" in st or "by_status" in st or "by_method" in st
