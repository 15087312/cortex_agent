"""event_retrieval 扩展测试：检索流程 / 评分 / 过滤 / 因果扩散"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.memory.event_retrieval import (
    EventRetrieval,
    get_event_retrieval,
    _retrieval_instance,
    MIN_SEMANTIC_SIMILARITY,
)
from modules.memory.event_store import EventStore, MemoryEvent


class FakeEmbedder:
    def embed(self, text):
        if not text:
            return None
        return [1.0, 0.0, 0.0]


@pytest.fixture
def store(tmp_path):
    return EventStore(
        db_path=str(tmp_path / "er.db"),
        faiss_index_path=str(tmp_path / "er.faiss"),
        id_map_path=str(tmp_path / "er.json"),
    )


def _retriever(store):
    r = EventRetrieval()
    r._store = store
    r._embedder = FakeEmbedder()
    return r


def _ev(**kw):
    base = dict(fact="缓存优化提升性能", keywords=["缓存"], importance=0.7)
    base.update(kw)
    return MemoryEvent(**base)


# ── retrieve 主流程 ────────────────────────────────────────────────────

async def test_retrieve_full_flow(store):
    ev = _ev()
    store.save_event(ev)
    r = _retriever(store)
    r._vector_search = AsyncMock(return_value=[(ev, 0.8)])
    r._causal_search = MagicMock(return_value=[])
    results = await r.retrieve("缓存优化", threshold=0.06)
    assert results
    assert results[0].id == ev.id
    # touch_event 被调用（access_count 增加）
    assert store.get_event(ev.id).access_count >= 1


async def test_retrieve_embedding_none(store):
    r = _retriever(store)
    r._embedder = FakeEmbedder()
    r._embedder.embed = MagicMock(return_value=None)
    assert await r.retrieve("q") == []


async def test_retrieve_types_and_importance_filter(store):
    fact = _ev(type="fact")
    strategy = _ev(fact="策略事件", type="strategy", importance=0.9)
    store.save_event(fact)
    store.save_event(strategy)
    r = _retriever(store)
    r._vector_search = AsyncMock(return_value=[(strategy, 0.8), (fact, 0.8)])
    r._causal_search = MagicMock(return_value=[])
    results = await r.retrieve("q", types=["strategy"])
    assert all(e.type == "strategy" for e in results)
    results = await r.retrieve("q", min_importance=0.8)
    assert all(e.importance >= 0.8 for e in results)


async def test_retrieve_time_range_filter(store):
    early = _ev(time="2026-01-01T00:00:00")
    late = _ev(fact="晚期事件", time="2026-12-01T00:00:00")
    store.save_event(early)
    store.save_event(late)
    r = _retriever(store)
    r._vector_search = AsyncMock(return_value=[(early, 0.8), (late, 0.8)])
    r._causal_search = MagicMock(return_value=[])
    results = await r.retrieve("q", start_time="2026-06-01")
    assert all(e.id == late.id for e in results)


async def test_retrieve_owner_filter(store):
    owned = _ev(owner_id="expert_1")
    shared = _ev(fact="共享事件", owner_id="shared")
    store.save_event(owned)
    store.save_event(shared)
    r = _retriever(store)
    r._vector_search = AsyncMock(return_value=[(owned, 0.8), (shared, 0.8)])
    r._causal_search = MagicMock(return_value=[])
    results = await r.retrieve("q", owner_id="expert_1")
    assert all(e.owner_id == "expert_1" for e in results)
    # large 系列可看全部
    results = await r.retrieve("q", owner_id="large::large_primary")
    assert len(results) == 2


# ── retrieve_mixed ─────────────────────────────────────────────────────

async def test_retrieve_mixed_empty_and_invalid():
    r = EventRetrieval()
    r._embedder = FakeEmbedder()
    assert await r.retrieve_mixed({}) == []
    assert await r.retrieve_mixed({"a": 0, "b": 0}) == []


async def test_retrieve_mixed_normal(store):
    ev = _ev()
    store.save_event(ev)
    r = _retriever(store)
    r._vector_search = AsyncMock(return_value=[(ev, 0.8)])
    results = await r.retrieve_mixed({"缓存": 1.0, " ": 0.0}, threshold=0.0)
    assert results


async def test_retrieve_mixed_owner_filter(store):
    owned = _ev(owner_id="expert_1")
    shared = _ev(fact="共享", owner_id="shared")
    store.save_event(owned)
    store.save_event(shared)
    r = _retriever(store)
    r._vector_search = AsyncMock(return_value=[(owned, 0.8), (shared, 0.8)])
    results = await r.retrieve_mixed({"主题": 1.0}, threshold=0.0, owner_id="expert_1")
    assert all(e.owner_id == "expert_1" for e in results)


# ── 评分引擎 ───────────────────────────────────────────────────────────

def test_calculate_all_scores_filters_low_semantic():
    r = EventRetrieval()
    ev = _ev()
    now = datetime.now(timezone.utc)
    scored = r._calculate_all_scores([(ev, 0.1)], now)
    assert scored == []


def test_calculate_all_scores_content_bonus():
    r = EventRetrieval()
    ev = _ev(lesson="经验", importance=0.5)
    now = datetime.now(timezone.utc)
    scored = r._calculate_all_scores([(ev, 0.9)], now)
    assert len(scored) == 1
    assert scored[0][1] > 0.6  # semantic 主导


def test_calculate_all_scores_type_decay(store):
    r = EventRetrieval()
    now = datetime.now(timezone.utc)
    old = datetime.now(timezone.utc).replace(year=2000)
    ev_emotion = _ev(fact="e", type="emotion", last_accessed=old.isoformat())
    scored = r._calculate_all_scores([(ev_emotion, 0.9)], now)
    assert scored[0][1] < 0.8


def test_rank_and_filter(store):
    ev = _ev()
    store.save_event(ev)
    r = _retriever(store)
    assert r._rank_and_filter([], 0.0, 10) == []
    out = r._rank_and_filter([(ev, 1.0), (ev, 0.0)], threshold=0.1, max_results=10)
    assert len(out) == 1


# ── 工具函数 ───────────────────────────────────────────────────────────

def test_parse_dt():
    assert EventRetrieval._parse_dt("") is None
    assert EventRetrieval._parse_dt("2026-07-01").tzinfo is not None
    assert EventRetrieval._parse_dt("2026-07-01T10:00:00").tzinfo is not None
    assert EventRetrieval._parse_dt("not a date") is None


def test_in_time_range():
    assert EventRetrieval._in_time_range("2026-07-01T12:00:00", "", "") is True
    # 纯日期 end 含整天
    assert EventRetrieval._in_time_range("2026-07-01T23:00:00", "", "2026-07-01") is True
    assert EventRetrieval._in_time_range("2026-07-02T00:00:00", "", "2026-07-01") is False
    assert EventRetrieval._in_time_range("2026-06-30", "2026-07-01", "") is False
    assert EventRetrieval._in_time_range("bad", "2026-07-01", "") is True


def test_days_since():
    now = datetime.now(timezone.utc)
    assert EventRetrieval._days_since("", now) == 0.0
    assert EventRetrieval._days_since(now.isoformat(), now) == pytest.approx(0.0, abs=0.01)
    assert EventRetrieval._days_since("bogus", now) == 0.0
    future = now.replace(year=now.year + 1).isoformat()
    assert EventRetrieval._days_since(future, now) == 0.0


def test_days_since_naive_and_z():
    now = datetime.now(timezone.utc)
    naive = now.replace(tzinfo=None).isoformat()
    assert EventRetrieval._days_since(naive, now) >= 0.0
    assert EventRetrieval._days_since("2026-07-01T00:00:00Z", now) >= 0.0


# ── 因果扩散 ───────────────────────────────────────────────────────────

def test_causal_search(store, monkeypatch):
    from modules.memory.causal_graph import CausalGraph, CausalNode
    graph = CausalGraph(db_path=str(store._db_path).replace(".db", "_cg.db"))
    node = CausalNode(label="延迟")
    graph.save_node(node)
    ev = _ev(causal_node_ids=[node.id])
    store.save_event(ev)
    store.save_event(_ev(fact="无关"))
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: graph))
    r = _retriever(store)
    candidates = r._causal_search("延迟")
    assert any(e.id == ev.id for e in candidates)


def test_causal_search_exception(store, monkeypatch):
    from modules.memory.causal_graph import CausalGraph
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("boom"))))
    r = _retriever(store)
    assert r._causal_search("q") == []


async def test_vector_search_and_compute_similarities(store):
    ev = _ev()
    store.save_event(ev)
    r = _retriever(store)
    r._vector_search = AsyncMock(return_value=[])
    assert await r._vector_search([1.0, 0.0]) == []
    # _compute_similarities：store.get_embedding 不可用（无 FAISS）→ 跳过
    store._load_faiss = MagicMock()
    store._faiss_index = None
    assert r._compute_similarities([1.0, 0.0, 0.0], [ev]) == []


def test_get_event_retrieval_singleton(monkeypatch):
    import modules.memory.event_retrieval as mod
    monkeypatch.setattr(mod, "_retrieval_instance", None)
    a = get_event_retrieval()
    b = get_event_retrieval()
    assert a is b
    assert mod._retrieval_instance is a
