"""depth_recall 补测 — 覆盖 format 全分支 / 热缓存过期 / else 意图 / 事件合并 / embedding 路径 / 增量更新边界"""
import time
import pytest
from unittest.mock import MagicMock

from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
from modules.memory.causal_tree import CausalTree, CausalChain
from modules.memory.depth_recall import (
    DepthRecallScheduler, DeepRecallResult, classify_intent,
    _intent_cache,
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


def _make_chain(graph, labels, conf=0.8):
    nodes = {}
    for lbl in labels:
        n = CausalNode(label=lbl)
        graph.save_node(n)
        nodes[lbl] = n
    for i in range(len(labels) - 1):
        graph.save_edge(CausalEdge(
            from_id=nodes[labels[i]].id, to_id=nodes[labels[i + 1]].id, confidence=conf,
        ))
    return nodes


class FakeRetrieval:
    def __init__(self, events):
        self._events = events

    async def retrieve(self, query, max_results=10, threshold=0.0):
        return self._events


def _sched(graph, store, retrieval):
    return DepthRecallScheduler(
        graph=graph, tree=CausalTree(graph), store=store, retrieval=retrieval,
    )


# ── format 全分支 ───────────────────────────────────────────────────────

def _result(**kw):
    defaults = dict(success=True, fallback=False)
    defaults.update(kw)
    return DeepRecallResult(**defaults)


def test_format_fallback_returns_empty():
    assert _result(success=True, fallback=True).format() == ""
    assert _result(success=False).format() == ""


def test_format_with_all_sections():
    from modules.memory.causal_tree import CausalChain
    a = CausalNode(label="原因")
    b = CausalNode(label="结果")
    chain = CausalChain(nodes=[a, b], direction="forward", confidence=0.8)
    r = _result(
        causal_conclusion="结论",
        shared_factors=["因子A", "因子B"],
        causal_chains=[chain],
        supporting_events=[MemoryEvent(fact="佐证", importance=0.9)],
        counter_examples=[MemoryEvent(fact="反例")],
    )
    out = r.format()
    assert "【因果结论】" in out
    assert "【共享因子】" in out
    assert "1. 原因 → 结果" in out
    assert "【佐证事件】" in out
    assert "【反例 / 例外】" in out


def test_format_with_backward_chain():
    from modules.memory.causal_tree import CausalChain
    chain = CausalChain(nodes=[CausalNode(label="根"), CausalNode(label="果")],
                        direction="backward", confidence=0.5)
    out = _result(causal_chains=[chain]).format()
    assert "根 → 果" in out
    assert "50%" in out


def test_format_max_events_truncation():
    r = _result(supporting_events=[MemoryEvent(fact=f"e{i}", importance=0.5) for i in range(7)])
    out = r.format(max_events=2)
    assert out.count("· e") == 2


def test_format_no_sections():
    assert _result().format() == ""


# ── classify_intent 其它模式 ────────────────────────────────────────────

def test_classify_intent_predict_and_counterfactual():
    saved = dict(_intent_cache)
    _intent_cache.clear()
    assert classify_intent("会有什么后果") == "predict"
    assert classify_intent("如果当时") == "counterfactual"
    assert classify_intent("调试这个程序") == "analyze"
    _intent_cache.clear()
    _intent_cache.update(saved)


def test_classify_intent_cache_returns_cached():
    saved = dict(_intent_cache)
    _intent_cache.clear()
    _intent_cache["已缓存查询"] = ("shallow", time.time())
    assert classify_intent("已缓存查询") == "shallow"
    _intent_cache.clear()
    _intent_cache.update(saved)


# ── should_trigger_deep_recall ──────────────────────────────────────────

def test_should_trigger_branches():
    from modules.memory.depth_recall import should_trigger_deep_recall
    assert should_trigger_deep_recall("为什么", None, "") == (True, "query_contains_logic_words")
    assert should_trigger_deep_recall("普通问题", 0.1, "") == (True, "shallow_recall_low_confidence")
    assert should_trigger_deep_recall("普通问题", 0.5, "decision") == (True, "decision_task")
    assert should_trigger_deep_recall("普通问题", 0.9, "") == (False, "")


# ── 热缓存过期 → 重算 ───────────────────────────────────────────────────

async def test_hot_cache_expired_recomputes(graph, store, monkeypatch):
    nodes = _make_chain(graph, ["根因甲", "后果乙"])
    store.save_event(MemoryEvent(fact="根因甲导致后果乙", keywords=["后果乙"], causal_node_ids=[nodes["后果乙"].id]))
    s = _sched(graph, store, FakeRetrieval([]))
    first = await s.deep_recall("为什么后果乙", depth_level=2)
    assert first.success
    now = [time.time()]
    monkeypatch.setattr("modules.memory.depth_recall.time.time", lambda: now[0])
    key = "为什么后果乙:2"
    s._hot_cache[key] = (first, now[0] - s._hot_cache_ttl - 10)
    second = await s.deep_recall("为什么后果乙", depth_level=2)
    assert second is not first


# ── else 意图分支（analyze / counterfactual / optimize / evaluate）──────

async def test_deep_recall_else_intent(graph, store):
    """intent 非 trace/predict/generalize → else 分支（264-266）"""
    nodes = _make_chain(graph, ["根因", "现象"])
    store.save_event(MemoryEvent(fact="根因导致现象", keywords=["现象"], causal_node_ids=[nodes["现象"].id]))
    s = _sched(graph, store, FakeRetrieval([]))
    result = await s.deep_recall("分析这个问题的根因")
    assert result.success
    assert result.intent in ("analyze", "trace")


async def test_deep_recall_no_causal_chains_fallback(graph, store, monkeypatch):
    """找到锚点但 tree 返回空因果链 → fallback no_causal_chains"""
    node = CausalNode(label="孤点原因")
    graph.save_node(node)
    store.save_event(MemoryEvent(fact="孤点事件", keywords=["孤点原因"], causal_node_ids=[node.id]))
    s = _sched(graph, store, FakeRetrieval([]))
    monkeypatch.setattr(s._tree, "trace_up", lambda *a, **k: [])
    monkeypatch.setattr(s._tree, "trace_down", lambda *a, **k: [])
    monkeypatch.setattr(s._tree, "compare_lateral", lambda *a, **k: [])
    result = await s.deep_recall("这个孤点原因为什么出现")
    assert result.fallback is True
    assert result.error == "no_causal_chains"


# ── _recall_events 事件合并 ─────────────────────────────────────────────

async def test_recall_events_merge_semantic_and_keyword(graph, store):
    node = CausalNode(label="性能")
    graph.save_node(node)
    ev = MemoryEvent(
        fact="性能问题描述", importance=0.7, keywords=["性能"],
        causal_node_ids=[node.id],
    )
    store.save_event(ev)
    chain = CausalChain(nodes=[node], direction="forward", confidence=0.8)
    s = _sched(graph, store, FakeRetrieval([ev]))
    supporting, counter = await s._recall_events("q", [chain], [node], 10, intent="trace")
    assert any(e.id == ev.id for e in supporting)


async def test_recall_events_calls_retrieval_and_counter(graph, store):
    node = CausalNode(label="冷门")
    graph.save_node(node)
    ev = MemoryEvent(fact="完全无关的内容", importance=0.1, keywords=[], causal_node_ids=[])
    chain = CausalChain(nodes=[node], direction="forward", confidence=0.8)
    s = _sched(graph, store, FakeRetrieval([ev]))
    supporting, counter = await s._recall_events("q", [chain], [node], 10, intent="predict")
    assert isinstance(supporting, list)
    assert isinstance(counter, list)


async def test_recall_events_retrieval_exception(graph, store):
    node = CausalNode(label="标签")
    graph.save_node(node)
    ev = MemoryEvent(fact="事件", keywords=["标签"], causal_node_ids=[node.id])
    store.save_event(ev)

    class BoomRetrieval:
        async def retrieve(self, query, max_results=10, threshold=0.0):
            raise RuntimeError("retrieval down")

    chain = CausalChain(nodes=[node], direction="forward", confidence=0.8)
    s = _sched(graph, store, BoomRetrieval())
    supporting, counter = await s._recall_events("q", [chain], [node], 10)
    assert any(e.id == ev.id for e in supporting)


# ── _causal_relevance embedding 路径 ────────────────────────────────────

async def test_recall_events_node_not_in_graph(graph, store):
    """causal_node_ids 中有不存在的节点 → node_labels 跳过（338->336）"""
    node = CausalNode(label="存在")
    graph.save_node(node)
    ev = MemoryEvent(fact="事件", importance=0.7, keywords=["存在"], causal_node_ids=[node.id])
    store.save_event(ev)
    from modules.memory.causal_tree import CausalNode as CN
    ghost = CN(label="幽灵")
    ghost.id = "ghost-id"
    chain = CausalChain(nodes=[node, ghost], direction="forward", confidence=0.8)
    s = _sched(graph, store, FakeRetrieval([]))
    supporting, counter = await s._recall_events("q", [chain], [node, ghost], 10)
    assert any(e.id == ev.id for e in supporting)


async def test_recall_events_semantic_not_in_merged(graph, store):
    """语义事件与关键词事件不同 → 打分循环 se.id==ev_id 不命中"""
    node = CausalNode(label="检索")
    graph.save_node(node)
    ev = MemoryEvent(fact="检索事件", importance=0.7, keywords=["检索"], causal_node_ids=[node.id])
    store.save_event(ev)
    other = MemoryEvent(fact="另一条语义事件", importance=0.1)
    chain = CausalChain(nodes=[node], direction="forward", confidence=0.8)
    s = _sched(graph, store, FakeRetrieval([other]))
    supporting, counter = await s._recall_events("q", [chain], [node], 10, intent="generalize")
    assert any(e.id == ev.id for e in supporting)


# ── _causal_relevance embedding 路径 ────────────────────────────────────

def test_causal_relevance_event_vec_none(graph, monkeypatch):
    """embed 返回 None → 跳过向量路径（410->427）"""
    monkeypatch.setattr("modules.memory.causal_graph.CausalGraph.get_instance",
                        staticmethod(lambda: graph))
    node = CausalNode(label="原因", keywords=["键"])
    graph.save_node(node)
    ev = MemoryEvent(fact="事件")
    fake_eng = MagicMock()
    fake_eng._loaded = True
    fake_eng.embed.return_value = None
    monkeypatch.setattr("modules.memory.embedding.EmbeddingEngine.get_instance",
                        lambda: fake_eng)
    s = _sched(graph, None, None)
    score = s._causal_relevance(ev, {node.id})
    assert score >= 0.0


def test_causal_relevance_node_vec_none(graph, monkeypatch):
    """节点向量为空 → continue（417->412）"""
    monkeypatch.setattr("modules.memory.causal_graph.CausalGraph.get_instance",
                        staticmethod(lambda: graph))
    node = CausalNode(label="原因", keywords=["键"])
    graph.save_node(node)
    ev = MemoryEvent(fact="事件")
    fake_eng = MagicMock()
    fake_eng._loaded = True
    fake_eng.embed.side_effect = lambda text: [0.1, 0.2] if "事件" in text else None
    monkeypatch.setattr("modules.memory.embedding.EmbeddingEngine.get_instance",
                        lambda: fake_eng)
    s = _sched(graph, None, None)
    score = s._causal_relevance(ev, {node.id})
    assert score >= 0.0


def test_causal_relevance_max_sim_zero(graph, monkeypatch):
    """最大相似度为 0 → 走文本兜底（421->427）"""
    monkeypatch.setattr("modules.memory.causal_graph.CausalGraph.get_instance",
                        staticmethod(lambda: graph))
    node = CausalNode(label="原因", keywords=["键"])
    graph.save_node(node)
    ev = MemoryEvent(fact="事件")
    fake_eng = MagicMock()
    fake_eng._loaded = True
    fake_eng.embed.side_effect = lambda text: [1.0, 1.0]
    monkeypatch.setattr("modules.memory.embedding.EmbeddingEngine.get_instance",
                        lambda: fake_eng)
    s = _sched(graph, None, None)
    score = s._causal_relevance(ev, {node.id})
    assert score > 0.0  # 文本匹配兜底命中


def test_causal_relevance_embedding_missing_node(graph, monkeypatch):
    """向量路径中节点不存在 → continue（414->412）"""
    monkeypatch.setattr("modules.memory.causal_graph.CausalGraph.get_instance",
                        staticmethod(lambda: graph))
    node = CausalNode(label="原因")
    graph.save_node(node)
    ev = MemoryEvent(fact="事件")
    fake_eng = MagicMock()
    fake_eng._loaded = True
    fake_eng.embed.side_effect = lambda text: [0.1, 0.2]
    monkeypatch.setattr("modules.memory.embedding.EmbeddingEngine.get_instance",
                        lambda: fake_eng)
    s = _sched(graph, None, None)
    score = s._causal_relevance(ev, {"ghost-node"})
    assert score >= 0.0


def test_causal_relevance_embedding_loaded(graph, monkeypatch):
    monkeypatch.setattr("modules.memory.causal_graph.CausalGraph.get_instance",
                        staticmethod(lambda: graph))
    node = CausalNode(label="原因")
    graph.save_node(node)
    ev = MemoryEvent(fact="事件文本", thought="想法", lesson="教训")
    fake_eng = MagicMock()
    fake_eng._loaded = True
    fake_eng.embed.side_effect = lambda text: [0.1, 0.2, 0.3]
    monkeypatch.setattr("modules.memory.embedding.EmbeddingEngine.get_instance",
                        lambda: fake_eng)
    s = _sched(graph, None, None)
    score = s._causal_relevance(ev, {node.id})
    assert score > 0.0


def test_causal_relevance_embedding_error(graph, monkeypatch):
    monkeypatch.setattr("modules.memory.causal_graph.CausalGraph.get_instance",
                        staticmethod(lambda: graph))
    node = CausalNode(label="原因", keywords=["键"])
    graph.save_node(node)
    ev = MemoryEvent(fact="事件文本")

    class BoomEng:
        _loaded = True

        def embed(self, text):
            raise RuntimeError("embed down")

    monkeypatch.setattr("modules.memory.embedding.EmbeddingEngine.get_instance",
                        lambda: BoomEng())
    s = _sched(graph, None, None)
    score = s._causal_relevance(ev, {node.id})
    assert score >= 0.0


def test_causal_relevance_direct_hit_zero(graph):
    """有 causal_node_ids 但交集为 0 → 走后续文本匹配"""
    node = CausalNode(label="甲", keywords=["乙"])
    graph.save_node(node)
    ev = MemoryEvent(fact="事件", causal_node_ids=["not-in-set"])
    s = _sched(graph, None, None)
    score = s._causal_relevance(ev, {node.id})
    assert score >= 0.0


def test_causal_relevance_no_event_causal_ids(graph):
    node = CausalNode(label="标签")
    graph.save_node(node)
    ev = MemoryEvent(fact="事件", causal_node_ids=None)
    s = _sched(graph, None, None)
    assert s._causal_relevance(ev, {node.id}) >= 0.0


def test_causal_relevance_empty_text_with_labels(graph, monkeypatch):
    """文本为空且节点存在于 get_instance 图 → 429 return 0.0"""
    monkeypatch.setattr("modules.memory.causal_graph.CausalGraph.get_instance",
                        staticmethod(lambda: graph))
    node = CausalNode(label="标签")
    graph.save_node(node)
    ev = MemoryEvent(fact="", thought="", lesson="", keywords=[])
    s = _sched(graph, None, None)
    assert s._causal_relevance(ev, {node.id}) == 0.0


# ── _time_decay ─────────────────────────────────────────────────────────

def test_time_decay_branches():
    from modules.memory.depth_recall import DepthRecallScheduler as DRS
    assert DRS._time_decay("") == 0.5
    assert DRS._time_decay("not-a-date") == 0.5
    v = DRS._time_decay("2024-01-01T00:00:00+00:00")
    assert 0.0 < v <= 1.0
    # Z 后缀
    v2 = DRS._time_decay("2024-01-01T00:00:00Z")
    assert 0.0 < v2 <= 1.0
    # None 输入
    assert DRS._time_decay(None) == 0.5


# ── _build_conclusion ───────────────────────────────────────────────────

def test_build_conclusion_empty_and_forward():
    s = _sched(None, None, None)
    assert s._build_conclusion([], []) == ""
    chain = CausalChain(nodes=[CausalNode(label="A"), CausalNode(label="B")],
                        direction="forward", confidence=0.8)
    out = s._build_conclusion([chain], [])
    assert "→" in out
    out2 = s._build_conclusion([chain], ["因子"])
    assert "共享因子" in out2
    chain_bw = CausalChain(nodes=[CausalNode(label="A"), CausalNode(label="B")],
                           direction="backward", confidence=0.8)
    # 补全锚点后链路恒为因→果顺序，backward 也统一用 →
    assert "→" in s._build_conclusion([chain_bw], [])


# ── _incremental_update 边界 ────────────────────────────────────────────

def test_incremental_update_edge_boost_saturated(graph, store):
    nodes = _make_chain(graph, ["源", "目标"], conf=0.99)
    edge = CausalEdge(from_id=nodes["源"].id, to_id=nodes["目标"].id, confidence=0.99)
    graph.save_edge(edge)
    chain = CausalChain(nodes=[nodes["源"], nodes["目标"]],
                        edges=[graph.get_edge(edge.id)], direction="forward", confidence=0.9)
    result = DeepRecallResult(success=True, anchor=nodes["目标"], causal_chains=[chain])
    s = _sched(graph, store, None)
    s._incremental_update(result, {nodes["源"].id, nodes["目标"].id})
    assert s._update_stats.get("boosted", 0) == 0


def test_incremental_update_missing_edge(graph, store):
    nodes = _make_chain(graph, ["源", "目标"])
    missing = CausalEdge(from_id=nodes["源"].id, to_id=nodes["目标"].id, confidence=0.5)
    missing.id = "ce_not_in_db"
    chain = CausalChain(nodes=[nodes["源"], nodes["目标"]],
                        edges=[missing], direction="forward", confidence=0.9)
    result = DeepRecallResult(success=True, anchor=nodes["目标"], causal_chains=[chain])
    s = _sched(graph, store, None)
    s._incremental_update(result, {nodes["源"].id, nodes["目标"].id})
    assert s._update_stats.get("boosted", 0) == 0


def test_incremental_update_duplicate_edge_seen(graph, store):
    nodes = _make_chain(graph, ["源", "目标"])
    edge = CausalEdge(from_id=nodes["源"].id, to_id=nodes["目标"].id, confidence=0.5)
    graph.save_edge(edge)
    chain = CausalChain(nodes=[nodes["源"], nodes["目标"]],
                        edges=[graph.get_edge(edge.id), graph.get_edge(edge.id)],
                        direction="forward", confidence=0.9)
    result = DeepRecallResult(success=True, anchor=nodes["目标"], causal_chains=[chain])
    s = _sched(graph, store, None)
    s._incremental_update(result, {nodes["源"].id, nodes["目标"].id})
    assert s._update_stats.get("boosted", 0) == 1


def test_incremental_update_event_already_linked(graph, store):
    """事件已含全部 node_ids → 不重复累加 event_count"""
    node = CausalNode(label="N")
    graph.save_node(node)
    ev = MemoryEvent(fact="事件", causal_node_ids=[node.id], importance=0.8)
    store.save_event(ev)
    result = DeepRecallResult(success=True, anchor=node, supporting_events=[ev])
    s = _sched(graph, store, None)
    s._incremental_update(result, {node.id})
    assert s._update_stats.get("linked", 0) == 0


def test_incremental_update_node_missing(graph, store):
    """node_ids 中有不存在的节点 → get_node None 跳过；有因果关联的事件仍挂链"""
    node = CausalNode(label="N")
    graph.save_node(node)
    ev = MemoryEvent(fact="事件", importance=0.8, causal_node_ids=[node.id])
    store.save_event(ev)
    result = DeepRecallResult(success=True, anchor=node, supporting_events=[ev])
    s = _sched(graph, store, None)
    s._incremental_update(result, {node.id, "ghost-node"})
    assert s._update_stats.get("linked", 0) == 1


def test_incremental_update_no_causal_relevance_skipped(graph, store):
    """佐证准入守卫：与 node_ids 无因果关联的事件（即使高重要度）不挂链，
    避免跨场景事件被永久污染进因果图"""
    node = CausalNode(label="N")
    graph.save_node(node)
    ev = MemoryEvent(fact="完全不相关的内容", importance=0.9)
    store.save_event(ev)
    result = DeepRecallResult(success=True, anchor=node, supporting_events=[ev])
    s = _sched(graph, store, None)
    s._incremental_update(result, {node.id})
    assert s._update_stats.get("linked", 0) == 0


def test_incremental_update_anchor_missing(graph, store):
    """anchor 不在图中 → get_node None 跳过锚点提升"""
    from modules.memory.causal_tree import CausalChain
    ghost = CausalNode(label="幽灵")
    ghost.id = "ghost_anchor"
    chain = CausalChain(nodes=[ghost], direction="forward", confidence=0.8)
    result = DeepRecallResult(success=True, anchor=ghost, causal_chains=[chain])
    s = _sched(graph, store, None)
    s._incremental_update(result, set())
    assert s._update_stats == {"linked": 0, "boosted": 0}
