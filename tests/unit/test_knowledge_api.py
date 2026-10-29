"""知识库 REST 端点单测 — /knowledge/* 的返回结构与错误码

策略：不触发 lifespan，知识库门面用 stub 替换（不碰真实库与 embedding 模型）。
"""
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _mock_api_log_store(monkeypatch):
    """请求日志中间件落库 → mock，避免写真实 data/api_log.db"""
    store = MagicMock()
    monkeypatch.setattr(
        "modules.management.api_log_store.ApiLogStore.get_instance", lambda: store)
    return store


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("api.main._SIMPLE_API_KEY", "")
    from api.main import app
    return TestClient(app, raise_server_exceptions=False)


class _Stub:
    def __init__(self):
        self.deleted = True

    def list_docs(self, limit=50, offset=0):
        return [{"id": "kd_1", "title": "鉴权设计", "source_path": "docs/auth.md", "chunk_count": 2}]

    def ingest(self, path=""):
        return {"added_count": 1, "path": path}

    def delete(self, doc_id):
        return {"deleted": self.deleted, "doc_id": doc_id, "reason": "文档不存在"}

    def search(self, query, top_k=None, path_prefix=""):
        return [{"chunk_id": "kc_1", "text": "双令牌", "source_path": "docs/auth.md"}]

    def reindex(self, full=False):
        return {"mode": "full" if full else "incremental"}

    def stats(self):
        return {"documents": 1, "chunks": 2, "dim": 768}


@pytest.fixture
def stub(monkeypatch):
    svc = _Stub()
    import api.main as main
    monkeypatch.setattr(main, "_kb_service", lambda: svc)
    return svc


def test_list_docs(client, stub):
    r = client.get("/knowledge/docs?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"]["count"] == 1
    assert body["data"]["documents"][0]["source_path"] == "docs/auth.md"


def test_ingest_empty_path_triggers_full_scan(client, stub):
    r = client.post("/knowledge/ingest", json={})
    assert r.status_code == 200
    assert r.json()["data"]["added_count"] == 1


def test_ingest_error_maps_to_422(client, stub, monkeypatch):
    monkeypatch.setattr(stub, "ingest", lambda path="": {"error": "路径不在知识库白名单内"})
    r = client.post("/knowledge/ingest", json={"path": "/etc"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_search_requires_query(client, stub):
    assert client.post("/knowledge/search", json={"query": "  "}).status_code == 422


def test_search_returns_hits(client, stub):
    r = client.post("/knowledge/search", json={"query": "鉴权", "top_k": 3})
    assert r.status_code == 200
    assert r.json()["data"]["hits"][0]["text"] == "双令牌"


def test_delete_missing_doc_returns_404(client, stub, monkeypatch):
    monkeypatch.setattr(stub, "delete", lambda doc_id: {"deleted": False, "reason": "文档不存在"})
    r = client.delete("/knowledge/docs/kd_missing")
    assert r.status_code == 404


def test_reindex_full_flag(client, stub):
    r = client.post("/knowledge/reindex", json={"full": True})
    assert r.json()["data"]["mode"] == "full"


def test_stats(client, stub):
    r = client.get("/knowledge/stats")
    assert r.status_code == 200
    assert r.json()["data"]["dim"] == 768
