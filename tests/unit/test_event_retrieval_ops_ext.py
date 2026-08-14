"""event_retrieval 补测：合并去重 / 真实向量搜索 / 相似度门槛 / 解析边界 / 单例竞态"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.memory.event_retrieval import (
    EventRetrieval,
    get_event_retrieval,
)
from modules.memory.event_store import EventStore, MemoryEvent


@pytest.fixture
def store(tmp_path):
    return EventStore(
        db_path=str(tmp_path / "er.db"),
        faiss_index_path=str(tmp_path / "er.faiss"),
        id_map_path=str(tmp_path / "er.json"),
    )


def _ev(**kw):
    base = dict(fact="缓存优化提升性能", keywords=["缓存"], importance=0.7)
    base.update(kw)
    return MemoryEvent(**base)


def _retriever(store):
    r = EventRetrieval()
    r._store = store
    r._embedder = type("E", (), {"embed": lambda self, text: [1.0, 0.0, 0.0]})()
    return r


# ── 单例竞态 ───────────────────────────────────────────────────────────

def test_get_instance_inner_check(monkeypatch):
    monkeypatch.setattr(EventRetrieval, "_instance", None)
    fake = EventRetrieval()

    class RacingLock:
        def __enter__(self):
            EventRetrieval._instance = fake
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(EventRetrieval, "_lock", RacingLock())
    assert EventRetrieval.get_instance() is fake


def test_get_event_retrieval_inner_check(monkeypatch):
    import modules.memory.event_retrieval as mod
    monkeypatch.setattr(mod, "_retrieval_instance", None)
    fake = EventRetrieval()

    class RacingLock:
        def __enter__(self):
            mod._retrieval_instance = fake
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod, "_retrieval_lock", RacingLock())
    assert get_event_retrieval() is fake


# ── retrieve 合并去重 ──────────────────────────────────────────────────

async def test_retrieve_merge_dedup(store):
    ev = _ev()
    store.save_event(ev)
    r = _retriever(store)
    r._vector_search = AsyncMock(return_value=[(ev, 0.8)])
    r._causal_search = MagicMock(return_value=[ev])
    r._compute_similarities = MagicMock(return_value=[(ev, 0.5)])
    results = await r.retrieve("q", threshold=0.0)
    assert results[0].id == ev.id  # 因果分更低 → 不覆盖


# ── retrieve_mixed 边界 ───────────────────────────────────────────────

async def test_retrieve_mixed_topic_no_embedding(store):
    ev = _ev()
    store.save_event(ev)
    r = _retriever(store)

    class PickyEmbedder:
        def embed(self, text):
            if text == "无向量":
                return None
            return [1.0, 0.0, 0.0]

    r._embedder = PickyEmbedder()
    r._vector_search = AsyncMock(return_value=[(ev, 0.8)])
    results = await r.retrieve_mixed({"无向量": 1.0, "有效": 1.0}, threshold=0.0)
    assert results


async def test_retrieve_mixed_types_filter(store):
    ev = _ev(type="strategy")
    store.save_event(ev)
    r = _retriever(store)
    r._vector_search = AsyncMock(return_value=[(ev, 0.8)])
    results = await r.retrieve_mixed({"主题": 1.0}, types=["fact"], threshold=0.0)
    assert results == []


async def test_retrieve_mixed_dedup(store):
    ev = _ev()
    store.save_event(ev)
    r = _retriever(store)
    r._vector_search = AsyncMock(return_value=[(ev, 0.8)])
    results = await r.retrieve_mixed({"缓存": 1.0, "优化": 0.5}, threshold=0.0)
    assert results and results[0].id == ev.id  # 低权重重复 → 跳过


# ── 真实 _vector_search / _compute_similarities ───────────────────────

async def test_vector_search_real_no_results():
    r = EventRetrieval()
    r._store = MagicMock()
    r._store.search_by_vector = MagicMock(return_value=[])
    assert await r._vector_search([1.0, 0.0]) == []


async def test_vector_search_real_missing_event():
    r = EventRetrieval()
    r._store = MagicMock()
    r._store.search_by_vector = MagicMock(return_value=[("e1", 0.9), ("ghost", 0.5)])
    r._store.get_event = MagicMock(side_effect=lambda eid: MagicMock() if eid == "e1" else None)
    out = await r._vector_search([1.0, 0.0])
    assert len(out) == 1


def test_compute_similarities_real_pass():
    r = EventRetrieval()
    r._store = MagicMock()
    r._store.get_embedding = MagicMock(return_value=[1.0, 0.0, 0.0])
    ev = _ev()
    out = r._compute_similarities([1.0, 0.0, 0.0], [ev])
    assert out == [(ev, 1.0)]


# ── 因果扩散无锚点 ─────────────────────────────────────────────────────

def test_causal_search_no_anchors(monkeypatch):
    from modules.memory.causal_graph import CausalGraph
    graph = MagicMock()
    graph.find_anchor_nodes = MagicMock(return_value=[])
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: graph))
    r = EventRetrieval()
    r._store = MagicMock()
    assert r._causal_search("q") == []


# ── 时间解析边界 ───────────────────────────────────────────────────────

def test_parse_dt_aware():
    t = EventRetrieval._parse_dt("2026-07-01T10:00:00+08:00")
    assert t is not None and t.tzinfo is not None


def test_in_time_range_end_datetime():
    # end 为完整时间戳 → 跳过"纯日期含整天"处理
    assert EventRetrieval._in_time_range("2026-07-01T23:00:00", "", "2026-07-01T12:00:00") is False


# ── 默认注入 ───────────────────────────────────────────────────────────

def test_get_store_default(monkeypatch):
    from modules.memory.event_store import EventStore
    fake = MagicMock()
    monkeypatch.setattr(EventStore, "get_instance", staticmethod(lambda: fake))
    r = EventRetrieval()
    assert r._get_store() is fake
