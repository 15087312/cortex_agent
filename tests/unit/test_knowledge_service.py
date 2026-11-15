"""知识库门面 — 开关短路 / 检索渲染 / 摄取与删除 / 统计

这一层是工具层和 REST 的唯一入口，主要风险是「知识库关着还在查」和
「返回结构不一致」。用替身注入 store/ingestor/retrieval，不碰真库。
"""
from unittest.mock import MagicMock

import pytest

from modules.knowledge import service as svc_mod
from modules.knowledge.service import KnowledgeService, get_knowledge_service, reset_knowledge_service


class FakeHit:
    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


class FakeDoc:
    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


class FakeResult:
    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


@pytest.fixture
def parts():
    store = MagicMock(name="store")
    ingestor = MagicMock(name="ingestor")
    retrieval = MagicMock(name="retrieval")
    return store, ingestor, retrieval


@pytest.fixture
def svc(parts):
    store, ingestor, retrieval = parts
    return KnowledgeService(store=store, ingestor=ingestor, retrieval=retrieval)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(svc_mod, "is_enabled", lambda: True)


@pytest.fixture
def disabled(monkeypatch):
    monkeypatch.setattr(svc_mod, "is_enabled", lambda: False)


# ── 惰性依赖 ──

def test_components_are_created_lazily(monkeypatch):
    monkeypatch.setattr(svc_mod.KnowledgeStore, "get_instance", classmethod(lambda cls: "STORE"))
    monkeypatch.setattr(svc_mod, "KnowledgeIngestor", lambda store=None: ("ING", store))
    monkeypatch.setattr(svc_mod, "KnowledgeRetrieval", lambda store=None: ("RET", store))

    s = KnowledgeService()
    assert s.store == "STORE"
    assert s.ingestor == ("ING", "STORE")
    assert s.retrieval == ("RET", "STORE")
    # 重复访问复用同一实例
    assert s.ingestor is s.ingestor


# ── 读取 ──

def test_search_short_circuits_when_disabled(svc, parts, disabled):
    _, _, retrieval = parts
    assert svc.search("x") == []
    retrieval.search.assert_not_called()


def test_search_maps_hits_to_dicts(svc, parts, enabled):
    _, _, retrieval = parts
    retrieval.search.return_value = [FakeHit({"doc": "a"}), FakeHit({"doc": "b"})]
    assert svc.search("q", top_k=3, path_prefix="docs/") == [{"doc": "a"}, {"doc": "b"}]
    retrieval.search.assert_called_once_with("q", top_k=3, path_prefix="docs/")


def test_search_text_returns_rendered_block(svc, parts, enabled):
    _, _, retrieval = parts
    retrieval.search.return_value = [FakeHit({"doc": "a"})]
    retrieval.format.return_value = "[1] a"
    assert svc.search_text("q") == "[1] a"
    retrieval.format.assert_called_once()


def test_search_text_empty_when_disabled(svc, parts, disabled):
    _, _, retrieval = parts
    assert svc.search_text("q") == ""
    retrieval.search.assert_not_called()


def test_should_retrieve_delegates(monkeypatch):
    monkeypatch.setattr(svc_mod, "should_retrieve", lambda q: q == "怎么部署")
    s = KnowledgeService(store=MagicMock(), ingestor=MagicMock(), retrieval=MagicMock())
    assert s.should_retrieve("怎么部署") is True
    assert s.should_retrieve("你好") is False


def test_list_docs_serializes(parts):
    store, ingestor, retrieval = parts
    store.list_documents.return_value = [FakeDoc({"id": "1", "path": "a.md"})]
    s = KnowledgeService(store=store, ingestor=ingestor, retrieval=retrieval)
    assert s.list_docs(limit=5, offset=10) == [{"id": "1", "path": "a.md"}]
    store.list_documents.assert_called_once_with(limit=5, offset=10)


# ── 写入 ──

def test_ingest_disabled_returns_error(svc, disabled):
    r = svc.ingest("docs/a.md")
    assert r["error"].startswith("知识库已关闭")


def test_ingest_single_path(svc, parts, enabled):
    _, ingestor, _ = parts
    ingestor.ingest_path.return_value = FakeResult({"ingested": 2})
    assert svc.ingest("docs/a.md") == {"ingested": 2}
    ingestor.ingest_path.assert_called_once_with("docs/a.md")
    ingestor.ingest_roots.assert_not_called()


def test_ingest_all_roots_when_path_empty(svc, parts, enabled):
    _, ingestor, _ = parts
    ingestor.ingest_roots.return_value = FakeResult({"ingested": 7})
    assert svc.ingest("") == {"ingested": 7}
    ingestor.ingest_roots.assert_called_once()


def test_delete_requires_enabled(svc, parts, enabled):
    store, _, _ = parts
    store.delete_document.return_value = {"success": True}
    assert svc.delete("doc1") == {"success": True}
    store.delete_document.assert_called_once_with("doc1")


def test_delete_disabled_does_not_touch_store(svc, parts, disabled):
    store, _, _ = parts
    assert "知识库已关闭" in svc.delete("doc1")["error"]
    store.delete_document.assert_not_called()


def test_reindex_passes_full_flag(svc, parts, enabled):
    _, ingestor, _ = parts
    ingestor.reindex.return_value = {"reindexed": 3}
    assert svc.reindex(full=True) == {"reindexed": 3}
    ingestor.reindex.assert_called_once_with(full=True)


def test_reindex_disabled_does_not_touch_ingestor(svc, parts, disabled):
    _, ingestor, _ = parts
    assert "知识库已关闭" in svc.reindex()["error"]
    ingestor.reindex.assert_not_called()


# ── 统计 ──

def test_stats_merges_roots_and_auto_inject(svc, parts, monkeypatch):
    store, ingestor, _ = parts
    store.stats.return_value = {"docs": 4}
    ingestor.roots.return_value = ["docs", "notes"]
    monkeypatch.setattr(svc_mod.settings, "KNOWLEDGE_AUTO_INJECT", False)

    assert svc.stats() == {"docs": 4, "roots": ["docs", "notes"], "auto_inject": False}


def test_stats_defaults_auto_inject_when_setting_missing(svc, parts, monkeypatch):
    store, ingestor, _ = parts
    store.stats.return_value = {}
    ingestor.roots.return_value = []
    monkeypatch.delattr(svc_mod.settings, "KNOWLEDGE_AUTO_INJECT", raising=False)
    assert svc.stats()["auto_inject"] is True


# ── 单例 ──

def test_service_singleton_can_be_reset():
    reset_knowledge_service()
    first = get_knowledge_service()
    assert get_knowledge_service() is first
    reset_knowledge_service()
    assert get_knowledge_service() is not first
