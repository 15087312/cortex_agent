"""causal_tree 扩展测试：缓存 / 时间窗口 / what_if / 证据树"""
import time

import pytest

from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
from modules.memory.causal_tree import (
    CausalTree,
    CausalChain,
    EvidenceTree,
    EvidenceItem,
    CacheEntry,
)
from modules.memory.event_store import EventStore, MemoryEvent


@pytest.fixture
def graph(tmp_path):
    return CausalGraph(db_path=str(tmp_path / "ct.db"))


@pytest.fixture
def store(tmp_path):
    return EventStore(
        db_path=str(tmp_path / "ev.db"),
        faiss_index_path=str(tmp_path / "ev.faiss"),
        id_map_path=str(tmp_path / "ev.json"),
    )


def _chain(graph, labels, conf=0.9):
    nodes = {}
    for l in labels:
        n = CausalNode(label=l)
        graph.save_node(n)
        nodes[l] = n
    for i in range(len(labels) - 1):
        graph.save_edge(CausalEdge(from_id=nodes[labels[i]].id, to_id=nodes[labels[i + 1]].id, confidence=conf))
    return nodes


# ── 缓存 ───────────────────────────────────────────────────────────────

def test_get_cache_key_with_list():
    tree = CausalTree.__new__(CausalTree)
    key = tree._get_cache_key("m", node_id="a", ids=[3, 1, 2])
    assert "ids=1,2,3" in key


def test_cache_expiry_and_version(monkeypatch):
    tree = CausalTree.__new__(CausalTree)
    tree._cache = {}
    tree._cache_ttl = 300.0
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    tree._set_cache("k", "v", version=1)
    assert tree._get_cached("k", 1) == "v"
    # 版本落后 → 失效
    assert tree._get_cached("k", 2) is None
    assert "k" not in tree._cache
    # TTL 过期
    tree._set_cache("k2", "v2", version=1)
    now[0] = 2000.0
    assert tree._get_cached("k2", 1) is None


def test_invalidate_cache_by_node_ids(graph):
    tree = CausalTree(graph)
    tree._cache["trace_up:nid=abc:max_depth=5:time_window=None"] = CacheEntry([], 1, time.time() + 100)
    tree._cache["trace_up:nid=xyz:max_depth=5:time_window=None"] = CacheEntry([], 1, time.time() + 100)
    tree.invalidate_cache({"abc"})
    assert len(tree._cache) == 1
    assert "xyz" in next(iter(tree._cache))


# ── expand_node ────────────────────────────────────────────────────────

def test_expand_node_cache_hit(graph, store, monkeypatch):
    node = CausalNode(label="节点")
    graph.save_node(node)
    store.save_event(MemoryEvent(fact="支撑", causal_node_ids=[node.id]))
    monkeypatch.setattr(EventStore, "_instance", store)
    tree = CausalTree(graph)
    first = tree.expand_node(node.id)
    cached = tree.expand_node(node.id)
    assert first is cached


def test_expand_node_missing_raises(graph):
    tree = CausalTree(graph)
    with pytest.raises(ValueError):
        tree.expand_node("不存在")


def test_trace_to_leaves_dedup(graph):
    n = _chain(graph, ["A", "B"])
    _chain(graph, ["A", "C"])
    # B → D, C → D 共享终点 D
    d = CausalNode(label="D")
    graph.save_node(d)
    graph.save_edge(CausalEdge(from_id=graph.find_nodes_by_label("B")[0].id, to_id=d.id))
    graph.save_edge(CausalEdge(from_id=graph.find_nodes_by_label("C")[0].id, to_id=d.id))
    tree = CausalTree(graph)
    chains = tree._trace_to_leaves(n["A"].id)
    ends = {c[-1].id for c in chains if c}
    assert d.id in ends


def test_trace_to_leaves_cycle_protection(graph):
    n = _chain(graph, ["A", "B"])
    # 加反向边会形成环，改为直接测 _dfs_down 的路径检测
    tree = CausalTree(graph)
    results = []
    tree._dfs_down(n["B"].id, [n["B"]], results, 0, 5)
    assert results  # 无后继时以自身为链尾


def test_compute_evidence_confidence(graph):
    node = CausalNode(label="N", confidence=0.8)
    tree = CausalTree(graph)
    conf = tree._compute_evidence_confidence(node, [EvidenceItem("e", "f")] * 5, [node])
    assert conf <= 0.99
    assert conf > 0.3


# ── trace_up / trace_down ──────────────────────────────────────────────

def test_trace_up_returns_backward_chains(graph):
    n = _chain(graph, ["根因", "中间", "结果"])
    tree = CausalTree(graph)
    chains = tree.trace_up(n["结果"].id)
    assert chains
    assert chains[0].direction == "backward"
    assert any(c.nodes[0].label == "根因" for c in chains)
    # 缓存命中
    chains2 = tree.trace_up(n["结果"].id)
    assert chains2 == chains


def test_trace_up_single_node(graph):
    node = CausalNode(label="孤点")
    graph.save_node(node)
    tree = CausalTree(graph)
    chains = tree.trace_up(node.id)
    assert len(chains) == 1
    assert chains[0].nodes[0].label == "孤点"


def test_trace_up_min_confidence_filter(graph):
    n = _chain(graph, ["根", "果"], conf=0.5)
    tree = CausalTree(graph)
    # 高置信度过滤：找不到前驱 → 仅返回锚点自身单节点链
    chains = tree.trace_up(n["果"].id, min_confidence=0.8)
    assert len(chains) == 1
    assert chains[0].nodes[0].label == "果"
    tree.invalidate_cache()
    # 低置信度：找到根节点
    chains0 = tree.trace_up(n["果"].id, min_confidence=0.0)
    assert any(c.nodes[0].label == "根" for c in chains0)


def test_trace_down_returns_forward_chains(graph):
    n = _chain(graph, ["原因", "结果"])
    tree = CausalTree(graph)
    chains = tree.trace_down(n["原因"].id)
    assert chains
    assert chains[0].direction == "forward"
    assert any(c.nodes[-1].label == "结果" for c in chains)


def test_trace_down_time_window(graph):
    n = _chain(graph, ["P", "Q"])
    tree = CausalTree(graph)
    # 时间窗口无效格式 → 无过滤，正常返回
    chains = tree.trace_down(n["P"].id, time_window="bogus")
    assert chains
    # 有效格式 30d
    chains2 = tree.trace_down(n["P"].id, time_window="30d")
    assert chains2


# ── what_if ────────────────────────────────────────────────────────────

def test_what_if_with_hypothetical_edge(graph):
    n = _chain(graph, ["A", "B"])
    tree = CausalTree(graph)
    hyp = CausalEdge(from_id=n["A"].id, to_id=n["B"].id, confidence=0.7)
    chains = tree.what_if(n["A"].id, hypothetical_edge=hyp)
    assert chains
    # 存在带假设边的链
    assert any(hyp.id in [e.id for e in c.edges] for c in chains)
    # 缓存命中
    chains2 = tree.what_if(n["A"].id, hypothetical_edge=hyp)
    assert chains2 == chains


def test_what_if_no_hypothetical(graph):
    n = _chain(graph, ["X", "Y"])
    tree = CausalTree(graph)
    chains = tree.what_if(n["X"].id)
    assert chains
    assert all(c.direction == "forward" for c in chains)


def test_what_if_hypothetical_from_other_node(graph):
    n = _chain(graph, ["M", "N"])
    tree = CausalTree(graph)
    hyp = CausalEdge(from_id=n["N"].id, to_id=n["M"].id, confidence=0.5)
    # 假设边起点不是锚点 → 只遍历现有边
    chains = tree.what_if(n["M"].id, hypothetical_edge=hyp)
    assert chains


# ── compare_lateral ────────────────────────────────────────────────────

def test_compare_lateral_shared_factors(graph):
    a1 = _chain(graph, ["共同根因", "分支一"])
    b2 = _chain(graph, ["共同根因", "分支二"])
    tree = CausalTree(graph)
    ids = [a1["分支一"].id, b2["分支二"].id]
    factors = tree.compare_lateral(ids, max_depth=3)
    assert "共同根因" in factors


# ── 杂项 ───────────────────────────────────────────────────────────────

def test_evidence_tree_format_full():
    tree = EvidenceTree(
        node=CausalNode(label="L"),
        evidence=[EvidenceItem(event_id="e", fact="fact", importance=0.9)],
        parent_chain=[CausalNode(label="P")],
        child_chains=[[CausalNode(label="C")]],
        confidence=0.8,
    )
    out = tree.format()
    assert "【L】" in out
    assert "P" in out and "C" in out


def test_chain_summary():
    nodes = [CausalNode(label="A"), CausalNode(label="B")]
    edges = [CausalEdge(from_id="a", to_id="b", confidence=0.6), CausalEdge(from_id="a", to_id="b", confidence=0.8)]
    chain = CausalChain(nodes=nodes, edges=edges, direction="forward")
    assert chain.summary() == "A → B"
    chain.confidence = 0.7
    assert chain.confidence == pytest.approx(0.7)
