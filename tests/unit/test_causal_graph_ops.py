"""causal_graph 扩展测试：CRUD / 环路检测 / 邻域扩散 / 共现 / 合并"""
import pytest

from modules.memory.causal_graph import (
    CausalGraph,
    CausalNode,
    CausalEdge,
    CAUSAL_RELATIONS,
    NODE_TYPES,
)
from modules.memory.event_store import EventStore, MemoryEvent


@pytest.fixture
def graph(tmp_path):
    return CausalGraph(db_path=str(tmp_path / "cg.db"))


@pytest.fixture
def store(tmp_path):
    return EventStore(
        db_path=str(tmp_path / "ev.db"),
        faiss_index_path=str(tmp_path / "ev.faiss"),
        id_map_path=str(tmp_path / "ev.json"),
    )


# ── 节点 CRUD ──────────────────────────────────────────────────────────

def test_node_to_from_dict_roundtrip():
    n = CausalNode(
        id="n1", label="标签", node_type="root", description="描述",
        keywords=["k1", "k2"], importance=0.9, confidence=0.8, event_count=3,
    )
    d = n.to_dict()
    assert d["keywords"] == '["k1", "k2"]'
    n2 = CausalNode.from_dict(d)
    assert n2.label == "标签"
    assert n2.keywords == ["k1", "k2"]
    assert n2.node_type == "root"


def test_edge_to_from_dict_roundtrip():
    e = CausalEdge(
        id="e1", from_id="a", to_id="b", relation="prevents",
        edge_type="causal", confidence=0.9, label="避免", created_at="t",
        version=1,
    )
    d = e.to_dict()
    e2 = CausalEdge.from_dict(d)
    assert e2.relation == "prevents"
    assert e2.created_at == "t"


def test_edge_from_dict_defaults():
    e = CausalEdge.from_dict({"id": "x", "from_id": "a", "to_id": "b"})
    assert e.relation == "causes"
    assert e.confidence == 0.5


def test_save_and_get_node(graph):
    n = CausalNode(label="需求变更")
    nid = graph.save_node(n)
    assert nid == n.id
    got = graph.get_node(nid)
    assert got.label == "需求变更"
    # save_node 为无 id 节点生成 id
    n2 = CausalNode(label="无 id")
    graph.save_node(n2)
    assert n2.id.startswith("cn_")


def test_delete_node_removes_edges(graph):
    a, b = CausalNode(label="A"), CausalNode(label="B")
    graph.save_node(a)
    graph.save_node(b)
    graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id))
    assert graph.delete_node(a.id) is True
    assert graph.get_node(a.id) is None
    assert graph.list_all_edges() == []
    assert graph.delete_node("不存在") is False


def test_find_nodes_by_label(graph):
    graph.save_node(CausalNode(label="性能优化方案"))
    assert len(graph.find_nodes_by_label("性能")) == 1
    assert graph.find_nodes_by_label("无关") == []


def test_list_nodes_limit_offset(graph):
    for i in range(5):
        graph.save_node(CausalNode(label=f"N{i}", importance=i / 10))
    nodes = graph.list_nodes(limit=2, offset=0)
    assert len(nodes) == 2
    assert nodes[0].label == "N4"  # importance DESC


# ── 边 CRUD + 环路检测 ─────────────────────────────────────────────────

def test_save_edge_auto_id_and_created_at(graph):
    a, b = CausalNode(label="A"), CausalNode(label="B")
    graph.save_node(a)
    graph.save_node(b)
    e = CausalEdge(from_id=a.id, to_id=b.id)
    eid = graph.save_edge(e)
    assert eid
    assert e.created_at  # 自动填充
    got = graph.get_edge(eid)
    assert got.from_id == a.id


def test_save_edge_rejects_cycle(graph):
    a, b = CausalNode(label="A"), CausalNode(label="B")
    graph.save_node(a)
    graph.save_node(b)
    assert graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id))
    # 反向边形成环 → 拒绝
    assert graph.save_edge(CausalEdge(from_id=b.id, to_id=a.id)) is None
    # 自环拒绝
    assert graph.save_edge(CausalEdge(from_id=a.id, to_id=a.id)) is None


def test_delete_edge(graph):
    a, b = CausalNode(label="A"), CausalNode(label="B")
    graph.save_node(a)
    graph.save_node(b)
    eid = graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id))
    assert graph.delete_edge(eid) is True
    assert graph.delete_edge(eid) is False


# ── 邻域扩散 ───────────────────────────────────────────────────────────

def _triangle(graph):
    """A → B, A → C, B → D"""
    n = {l: CausalNode(label=l) for l in ["A", "B", "C", "D"]}
    for node in n.values():
        graph.save_node(node)
    graph.save_edge(CausalEdge(from_id=n["A"].id, to_id=n["B"].id, confidence=0.9))
    graph.save_edge(CausalEdge(from_id=n["A"].id, to_id=n["C"].id, confidence=0.5))
    graph.save_edge(CausalEdge(from_id=n["B"].id, to_id=n["D"].id, confidence=0.8))
    return n


def test_get_predecessors_and_successors(graph):
    n = _triangle(graph)
    preds = graph.get_predecessors(n["D"].id)
    assert [p.label for p in preds] == ["B"]
    succs = graph.get_successors(n["A"].id)
    assert {s.label for s in succs} == {"B", "C"}
    # 置信度过滤
    succs_high = graph.get_successors(n["A"].id, min_confidence=0.8)
    assert [s.label for s in succs_high] == ["B"]


def test_get_neighbors_multi_hop(graph):
    n = _triangle(graph)
    neighbors = graph.get_neighbors(n["A"].id, hops=2)
    labels = {node.label: hop for node, _, hop in neighbors}
    assert labels["B"] == 1
    assert labels["C"] == 1
    assert labels["D"] == 2
    # relation 过滤
    filtered = graph.get_neighbors(n["A"].id, hops=1, relation_filter="prevents")
    assert filtered == []
    filtered2 = graph.get_neighbors(n["A"].id, hops=1, relation_filter="causes")
    assert {node.label for node, _, _ in filtered2} == {"B", "C"}


def test_get_neighbors_no_edges(graph):
    n = CausalNode(label="孤点")
    graph.save_node(n)
    assert graph.get_neighbors(n.id) == []


# ── find_anchor_nodes ───────────────────────────────────────────────────

def test_find_anchor_keyword_match(graph):
    graph.save_node(CausalNode(label="项目延期"))
    graph.save_node(CausalNode(label="需求变更"))
    anchors = graph.find_anchor_nodes("项目延期", top_k=5)
    assert anchors
    assert anchors[0][0].label == "项目延期"


def test_find_anchor_no_keywords(graph):
    assert graph.find_anchor_nodes("", top_k=5) == []
    assert graph.find_anchor_nodes("a b", top_k=5) == []  # 短词被过滤


def test_find_anchor_with_semantic(graph, monkeypatch):
    class FakeEmbedder:
        def __init__(self):
            self._loaded = False
        def embed(self, text):
            return [1.0, 0.0]
        def embed_batch(self, texts):
            return [[0.8, 0.2]] * len(texts)
    import modules.memory.embedding as emb_mod
    monkeypatch.setattr(emb_mod.EmbeddingEngine, "get_instance", staticmethod(lambda: FakeEmbedder()))
    node = CausalNode(label="延期")
    graph.save_node(node)
    anchors = graph.find_anchor_nodes("延期")
    assert any(a[0].id == node.id for a in anchors)


def test_extract_keywords():
    assert set(CausalGraph._extract_keywords("性能优化 performance")) >= {"性能优化", "performance"}


# ── 共现统计 ───────────────────────────────────────────────────────────

def test_update_cooccurrence_creates_edge(graph, store):
    n1 = CausalNode(label="原因1")
    n2 = CausalNode(label="结果1")
    graph.save_node(n1)
    graph.save_node(n2)
    for _ in range(2):
        store.save_event(MemoryEvent(fact="f", causal_node_ids=[n1.id, n2.id]))
    result = graph.update_cooccurrence(
        event_ids=[e.id for e in store.list_events()], min_cooccur=2, store=store,
    )
    assert result >= 1
    edges = graph.list_all_edges()
    assert edges


def test_update_cooccurrence_boosts_existing(graph, store):
    n1 = CausalNode(label="A")
    n2 = CausalNode(label="B")
    graph.save_node(n1)
    graph.save_node(n2)
    graph.save_edge(CausalEdge(from_id=n1.id, to_id=n2.id, relation="causes", edge_type="correlation", confidence=0.5))
    for _ in range(2):
        store.save_event(MemoryEvent(fact="f", causal_node_ids=[n1.id, n2.id]))
    result = graph.update_cooccurrence(
        event_ids=[e.id for e in store.list_events()], min_cooccur=2, store=store,
    )
    assert result >= 1
    edge = graph.list_all_edges()[0]
    assert edge.edge_type == "causal"


def test_update_cooccurrence_insufficient_events(graph, store):
    assert graph.update_cooccurrence(store=store) == 0


def test_get_related_events(graph, store):
    n1 = CausalNode(label="节点")
    graph.save_node(n1)
    store.save_event(MemoryEvent(fact="相关事件", causal_node_ids=[n1.id]))
    store.save_event(MemoryEvent(fact="无关事件"))
    related = graph.get_related_events(n1.id, store=store)
    assert [e.fact for e in related] == ["相关事件"]


# ── list_all_edges / get_edge_stats ─────────────────────────────────────

def test_list_all_edges_filters(graph):
    n = _triangle(graph)
    all_edges = graph.list_all_edges()
    assert len(all_edges) == 3
    a_id = n["A"].id
    subset = graph.list_all_edges(node_ids=[a_id])
    assert len(subset) == 2
    # 非法时间窗口格式：不抛异常
    assert graph.list_all_edges(time_window="bogus") == all_edges


def test_get_edge_stats(graph):
    _triangle(graph)
    stats = graph.get_edge_stats()
    assert stats["total_edges"] == 3
    assert stats["from_nodes"] == 2
    assert stats["avg_confidence"] > 0


# ── 指标 ───────────────────────────────────────────────────────────────

def test_metrics(graph):
    _triangle(graph)
    graph._update_metrics()
    m = graph.get_metrics()
    assert m["causal_graph_nodes_total"] == 4
    assert m["causal_graph_edges_total"] == 3
    graph.record_query_time(0.1)
    graph.record_query_time(0.3)
    assert graph.get_metrics()["causal_graph_query_count"] == 2
    prom = graph.get_metrics_prometheus()
    assert "causal_graph_nodes_total" in prom


# ── 节点合并 ───────────────────────────────────────────────────────────

def test_merge_similar_nodes_label_contains(graph, store, monkeypatch):
    a = CausalNode(label="性能优化")
    b = CausalNode(label="性能优化方案")
    graph.save_node(a)
    graph.save_node(b)
    graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id, confidence=0.9))
    store.save_event(MemoryEvent(fact="e", causal_node_ids=[a.id]))
    monkeypatch.setattr(EventStore, "_instance", store)
    count = graph.merge_similar_nodes()
    assert count == 1
    # 边已转移，被合并节点删除
    assert graph.get_node(a.id) is None or graph.get_node(b.id) is None
    assert graph.list_all_edges() == []


def test_merge_similar_nodes_keyword_overlap(graph):
    a = CausalNode(label="A", keywords=["缓存", "性能"])
    b = CausalNode(label="B", keywords=["缓存", "性能", "延迟"])
    graph.save_node(a)
    graph.save_node(b)
    # 重合率 2/3 = 0.67 < 0.9 → 不合并
    assert graph.merge_similar_nodes(similarity_threshold=0.9) == 0
    assert graph.merge_similar_nodes(similarity_threshold=0.6) == 1


def test_merge_similar_nodes_no_merge(graph):
    a = CausalNode(label="甲")
    b = CausalNode(label="乙")
    graph.save_node(a)
    graph.save_node(b)
    assert graph.merge_similar_nodes() == 0


def test_clear_all_and_close(graph):
    graph.save_node(CausalNode(label="X"))
    graph.clear_all()
    assert graph.list_nodes() == []
    graph.close()
    assert graph._conn is None
    graph.close()  # 幂等


def test_get_instance_singleton(monkeypatch):
    monkeypatch.setattr(CausalGraph, "_instance", None)
    a = CausalGraph.get_instance()
    b = CausalGraph.get_instance()
    assert a is b
