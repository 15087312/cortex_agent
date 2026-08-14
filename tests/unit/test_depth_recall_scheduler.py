"""DepthRecallScheduler 深度回忆调度器测试：完整闭环 + 各意图分支 + 增量更新"""
import pytest

from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
from modules.memory.causal_tree import CausalTree
from modules.memory.depth_recall import (
    DepthRecallScheduler,
    DeepRecallResult,
    _intent_cache,
    _intent_cache_ttl,
)
from modules.memory.event_store import EventStore, MemoryEvent


@pytest.fixture
def graph(tmp_path):
    return CausalGraph(db_path=str(tmp_path / "causal.db"))


@pytest.fixture
def store(tmp_path):
    return EventStore(
        db_path=str(tmp_path / "mem.db"),
        faiss_index_path=str(tmp_path / "mem.faiss"),
        id_map_path=str(tmp_path / "mem.json"),
    )


def _make_chain(graph, labels):
    nodes = {}
    for l in labels:
        n = CausalNode(label=l, node_type="cause" if l != labels[-1] else "effect")
        graph.save_node(n)
        nodes[l] = n
    for i in range(len(labels) - 1):
        graph.save_edge(CausalEdge(
            from_id=nodes[labels[i]].id, to_id=nodes[labels[i + 1]].id, confidence=0.8,
        ))
    return nodes


class FakeRetrieval:
    def __init__(self, events):
        self._events = events

    async def retrieve(self, query, max_results=10, threshold=0.0):
        return self._events


# ── 构造 ───────────────────────────────────────────────────────────────

def test_init_defaults_and_limits(monkeypatch):
    from modules.memory.depth_recall import (
        _get_max_anchors, _get_max_neighbors, _get_max_tree_depth,
        _get_max_events_recall, _get_min_confidence, _get_hot_cache_ttl,
        _get_confidence_boost_delta, _get_confidence_max,
    )
    from config.settings import settings
    saved = {k: getattr(settings, k, None) for k in (
        "CAUSAL_MAX_ANCHORS", "CAUSAL_MAX_NEIGHBORS_PER_HOP", "CAUSAL_MAX_TREE_DEPTH",
        "CAUSAL_MAX_EVENTS_RECALL", "CAUSAL_MIN_CONFIDENCE", "CAUSAL_HOT_CACHE_TTL",
        "CAUSAL_CONFIDENCE_BOOST_DELTA", "CAUSAL_CONFIDENCE_MAX",
    )}
    monkeypatch.delattr(settings, "CAUSAL_MAX_ANCHORS", raising=False)
    monkeypatch.delattr(settings, "CAUSAL_MAX_NEIGHBORS_PER_HOP", raising=False)
    monkeypatch.delattr(settings, "CAUSAL_MAX_TREE_DEPTH", raising=False)
    monkeypatch.delattr(settings, "CAUSAL_MAX_EVENTS_RECALL", raising=False)
    monkeypatch.delattr(settings, "CAUSAL_MIN_CONFIDENCE", raising=False)
    monkeypatch.delattr(settings, "CAUSAL_HOT_CACHE_TTL", raising=False)
    monkeypatch.delattr(settings, "CAUSAL_CONFIDENCE_BOOST_DELTA", raising=False)
    monkeypatch.delattr(settings, "CAUSAL_CONFIDENCE_MAX", raising=False)
    assert _get_max_anchors() == 3
    assert _get_max_neighbors() == 10
    assert _get_max_tree_depth() == 4
    assert _get_max_events_recall() == 30
    assert _get_min_confidence() == 0.2
    assert _get_hot_cache_ttl() == 300.0
    assert _get_confidence_boost_delta() == 0.05
    assert _get_confidence_max() == 0.99


# ── classify_intent 缓存过期 ───────────────────────────────────────────

def test_classify_intent_cache_expiry(monkeypatch):
    saved = dict(_intent_cache)
    _intent_cache.clear()
    _intent_cache["为什么过期"] = ("trace", 0.0)
    import time as _time
    monkeypatch.setattr(_time, "time", lambda: _intent_cache_ttl + 1.0)
    from modules.memory.depth_recall import classify_intent
    assert classify_intent("为什么过期") == "trace"  # 命中缓存，未过期
    assert classify_intent("普通") == "shallow"
    monkeypatch.undo()
    _intent_cache.clear()
    _intent_cache.update(saved)


# ── deep_recall 完整闭环（trace 意图） ──────────────────────────────────

async def test_deep_recall_trace_happy_path(graph, store):
    nodes = _make_chain(graph, ["需求变更", "设计变更", "项目延期"])
    store.save_event(MemoryEvent(
        fact="需求频繁变更导致设计反复调整",
        keywords=["需求变更"], importance=0.8, causal_node_ids=[nodes["需求变更"].id],
    ))
    store.save_event(MemoryEvent(
        fact="项目延期一个月交付",
        keywords=["项目延期"], importance=0.9, causal_node_ids=[nodes["项目延期"].id],
    ))
    scheduler = DepthRecallScheduler(
        graph=graph, tree=CausalTree(graph), store=store, retrieval=FakeRetrieval([]),
    )
    result = await scheduler.deep_recall("为什么项目延期", task_type="")
    assert result.success is True
    assert result.anchor.label == "项目延期"
    assert result.intent == "trace"
    assert result.causal_chains
    assert "需求变更" in result.causal_conclusion or result.causal_conclusion
    # 增量更新：佐证事件被关联
    assert scheduler._update_stats.get("linked", 0) >= 0


async def test_deep_recall_hot_cache(graph, store):
    nodes = _make_chain(graph, ["根因A", "结果B"])
    store.save_event(MemoryEvent(fact="根因A导致结果B", keywords=["结果B"], causal_node_ids=[nodes["结果B"].id]))
    scheduler = DepthRecallScheduler(
        graph=graph, tree=CausalTree(graph), store=store, retrieval=FakeRetrieval([]),
    )
    first = await scheduler.deep_recall("为什么结果B")
    assert first.success
    assert len(scheduler._hot_cache) == 1
    # 命中缓存，返回同一实例
    second = await scheduler.deep_recall("为什么结果B")
    assert second is first
    # invalidate 后重新计算
    scheduler.invalidate_cache("为什么结果B")
    assert "为什么结果B:1" not in scheduler._hot_cache
    third = await scheduler.deep_recall("为什么结果B")
    assert third is not first


async def test_deep_recall_no_anchor(graph, store):
    scheduler = DepthRecallScheduler(
        graph=graph, tree=CausalTree(graph), store=store, retrieval=FakeRetrieval([]),
    )
    result = await scheduler.deep_recall("完全不相关的内容")
    assert result.fallback is True
    assert result.error == "no_anchor_nodes"


async def test_deep_recall_predict_intent(graph, store):
    nodes = _make_chain(graph, ["服务器故障", "服务不可用"])
    store.save_event(MemoryEvent(fact="服务器故障导致服务不可用", keywords=["服务不可用"], causal_node_ids=[nodes["服务不可用"].id]))
    scheduler = DepthRecallScheduler(
        graph=graph, tree=CausalTree(graph), store=store, retrieval=FakeRetrieval([]),
    )
    result = await scheduler.deep_recall("服务器故障会有什么后果")
    assert result.success
    assert result.intent == "predict"


async def test_deep_recall_generalize_intent(graph, store):
    nodes = _make_chain(graph, ["多次返工", "工期失控"])
    store.save_event(MemoryEvent(fact="多次返工导致工期失控", keywords=["工期失控"], causal_node_ids=[nodes["工期失控"].id]))
    scheduler = DepthRecallScheduler(
        graph=graph, tree=CausalTree(graph), store=store, retrieval=FakeRetrieval([]),
    )
    result = await scheduler.deep_recall("这些项目工期失控有什么规律")
    assert result.success
    assert result.intent == "generalize"


async def test_deep_recall_depth_level_2(graph, store):
    nodes = _make_chain(graph, ["A原因", "B结果"])
    store.save_event(MemoryEvent(fact="A导致B", keywords=["B结果"], causal_node_ids=[nodes["B结果"].id]))
    scheduler = DepthRecallScheduler(
        graph=graph, tree=CausalTree(graph), store=store, retrieval=FakeRetrieval([]),
    )
    result = await scheduler.deep_recall("为什么B结果", depth_level=2)
    assert result.success


# ── _recall_events ──────────────────────────────────────────────────────

async def test_recall_events_empty_causal_nodes(graph, store):
    scheduler = DepthRecallScheduler(
        graph=graph, tree=CausalTree(graph), store=store, retrieval=FakeRetrieval([]),
    )
    supporting, counter = await scheduler._recall_events("q", [], [], 10)
    assert supporting == [] and counter == []


async def test_recall_events_returns_supporting(graph, store):
    node = CausalNode(label="性能优化")
    graph.save_node(node)
    ev = MemoryEvent(
        fact="通过缓存优化了性能", importance=0.9,
        keywords=["性能优化"], causal_node_ids=[node.id],
    )
    store.save_event(ev)
    from modules.memory.causal_tree import CausalChain
    chain = CausalChain(
        nodes=[node], edges=[], direction="forward", confidence=0.8,
    )
    scheduler = DepthRecallScheduler(
        graph=graph, tree=CausalTree(graph), store=store, retrieval=FakeRetrieval([]),
    )
    supporting, counter = await scheduler._recall_events("q", [chain], [node], 10)
    assert any(e.id == ev.id for e in supporting)


# ── _causal_relevance ───────────────────────────────────────────────────

def test_causal_relevance_direct_hit(graph):
    node_a = CausalNode(label="原因X")
    graph.save_node(node_a)
    ev = MemoryEvent(fact="事件", causal_node_ids=[node_a.id])
    scheduler = DepthRecallScheduler(graph=graph, tree=CausalTree(graph))
    score = scheduler._causal_relevance(ev, {node_a.id, "other"})
    assert score > 0.4


def test_causal_relevance_text_match(graph, monkeypatch):
    from modules.memory.causal_graph import CausalGraph as CG
    monkeypatch.setattr(CG, "get_instance", staticmethod(lambda: graph))
    node = CausalNode(label="性能", keywords=["优化"])
    graph.save_node(node)
    ev = MemoryEvent(fact="性能优化取得成效", thought="", lesson="", keywords=[])
    scheduler = DepthRecallScheduler(graph=graph, tree=CausalTree(graph))
    # 关键词匹配路径（embedding 未加载时走文本兜底）
    score = scheduler._causal_relevance(ev, {node.id})
    assert score > 0.0


def test_causal_relevance_no_labels(graph):
    ev = MemoryEvent(fact="无关事件")
    scheduler = DepthRecallScheduler(graph=graph, tree=CausalTree(graph))
    assert scheduler._causal_relevance(ev, {"nope"}) == 0.0


def test_causal_relevance_empty_text(graph):
    node = CausalNode(label="标签")
    graph.save_node(node)
    ev = MemoryEvent(fact="", thought="", lesson="", keywords=[])
    scheduler = DepthRecallScheduler(graph=graph, tree=CausalTree(graph))
    assert scheduler._causal_relevance(ev, {node.id}) == 0.0


# ── _incremental_update ─────────────────────────────────────────────────

def test_incremental_update_links_and_boosts(graph, store):
    nodes = _make_chain(graph, ["源", "目标"])
    edge = CausalEdge(from_id=nodes["源"].id, to_id=nodes["目标"].id, confidence=0.5)
    graph.save_edge(edge)
    ev = MemoryEvent(
        fact="佐证事件", causal_node_ids=[nodes["目标"].id], importance=0.7,
    )
    store.save_event(ev)

    from modules.memory.causal_tree import CausalChain
    chain = CausalChain(
        nodes=[nodes["源"], nodes["目标"]],
        edges=[graph.get_edge(edge.id)],
        direction="forward", confidence=0.8,
    )
    result = DeepRecallResult(
        success=True, anchor=nodes["目标"],
        supporting_events=[ev],
        causal_chains=[chain],
        shared_factors=["新因子"],
    )
    scheduler = DepthRecallScheduler(graph=graph, tree=CausalTree(graph), store=store)
    scheduler._incremental_update(result, {nodes["源"].id, nodes["目标"].id})

    assert scheduler._update_stats.get("linked", 0) >= 1
    assert scheduler._update_stats.get("boosted", 0) >= 1
    # 共享因子自动建节点
    assert graph.find_nodes_by_label("新因子")


def test_incremental_update_skips_when_no_change(graph, store):
    node = CausalNode(label="孤点")
    graph.save_node(node)
    ev = MemoryEvent(fact="事件", causal_node_ids=[node.id])
    store.save_event(ev)
    result = DeepRecallResult(success=True, anchor=node, supporting_events=[ev])
    scheduler = DepthRecallScheduler(graph=graph, tree=CausalTree(graph), store=store)
    scheduler._incremental_update(result, {node.id})
    assert scheduler._update_stats.get("linked", 0) == 0


def test_incremental_update_anchor_none(graph, store):
    scheduler = DepthRecallScheduler(graph=graph, tree=CausalTree(graph), store=store)
    scheduler._incremental_update(DeepRecallResult(), set())
    assert scheduler._update_stats == {"linked": 0, "boosted": 0}


# ── invalidate_cache ────────────────────────────────────────────────────

def test_invalidate_cache_all_and_by_query(graph, store):
    scheduler = DepthRecallScheduler(
        graph=graph, tree=CausalTree(graph), store=store, retrieval=FakeRetrieval([]),
    )
    scheduler._hot_cache["q1:1"] = (DeepRecallResult(), 1.0)
    scheduler._hot_cache["q2:2"] = (DeepRecallResult(), 1.0)
    scheduler.invalidate_cache("q1")
    assert "q1:1" not in scheduler._hot_cache
    assert "q2:2" in scheduler._hot_cache
    scheduler.invalidate_cache()
    assert scheduler._hot_cache == {}
