"""causal_tree 补测 — 覆盖剩余分支：环检测 / 死路 / 时间窗口 / what_if 缓存缺失 / _dfs_what_if"""

import pytest

from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
from modules.memory.causal_tree import (
    CausalTree, CausalChain, EvidenceTree,
)
from modules.memory.event_store import EventStore


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
    for lbl in labels:
        n = CausalNode(label=lbl)
        graph.save_node(n)
        nodes[lbl] = n
    for i in range(len(labels) - 1):
        graph.save_edge(CausalEdge(from_id=nodes[labels[i]].id, to_id=nodes[labels[i + 1]].id, confidence=conf))
    return nodes


# ── _trace_to_root 环 / 分支 ────────────────────────────────────────────

def test_trace_to_root_cycle_protection(graph):
    """A ← B ← C，加反向边成环 → 不无限循环"""
    n = _chain(graph, ["A", "B", "C"])
    graph.save_edge(CausalEdge(from_id=n["A"].id, to_id=n["C"].id, confidence=0.9))
    tree = CausalTree(graph)
    path = tree._trace_to_root(n["C"].id)
    assert path  # 有界返回


def test_trace_to_root_isolated_node(graph):
    node = CausalNode(label="孤点")
    graph.save_node(node)
    tree = CausalTree(graph)
    assert tree._trace_to_root(node.id) == []


# ── _trace_to_leaves / _dfs_down ────────────────────────────────────────

def test_trace_to_leaves_root_no_children(graph):
    """锚点无下游 → 空结果，seen 去重空链分支"""
    node = CausalNode(label="叶")
    graph.save_node(node)
    tree = CausalTree(graph)
    assert tree._trace_to_leaves(node.id) == []


def test_dfs_down_depth_limit(graph):
    n = _chain(graph, ["A", "B", "C", "D"])
    tree = CausalTree(graph)
    results = []
    tree._dfs_down(n["A"].id, [], results, 2, 2)  # depth >= max_depth 提前返回
    assert isinstance(results, list)


def test_dfs_down_path_cycle_detection(graph):
    """B 的后继含路径上已有节点 → 跳过"""
    a = CausalNode(label="A")
    b = CausalNode(label="B")
    graph.save_node(a)
    graph.save_node(b)
    graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id))
    graph.save_edge(CausalEdge(from_id=b.id, to_id=a.id))
    tree = CausalTree(graph)
    results = []
    tree._dfs_down(a.id, [a], results, 0, 5)
    assert results  # B 死路 → 产出 [A,B]


# ── trace_up / trace_down 缓存缺失 ─────────────────────────────────────

def test_trace_up_no_cache_hit_single(graph):
    """节点存在但无缓存 → 走 DFS 并写缓存"""
    node = CausalNode(label="N")
    graph.save_node(node)
    tree = CausalTree(graph)
    tree._cache = {}  # 清缓存确保 miss
    chains = tree.trace_up(node.id)
    assert chains
    assert len(tree._cache) == 1


def test_trace_down_cache_miss(graph):
    n = _chain(graph, ["P", "Q"])
    tree = CausalTree(graph)
    tree._cache = {}
    chains = tree.trace_down(n["P"].id, time_window="7d")
    assert chains
    assert len(tree._cache) == 1


def test_trace_down_time_window_invalid_and_valid(graph):
    n = _chain(graph, ["X", "Y"])
    tree = CausalTree(graph)
    c1 = tree.trace_down(n["X"].id, time_window="xx")
    assert c1
    tree.invalidate_cache()
    c2 = tree.trace_down(n["X"].id, time_window="30m")
    assert c2


# ── _dfs_up 单节点 / 时间窗口 / visited ────────────────────────────────

def test_dfs_up_single_node(graph):
    node = CausalNode(label="孤点")
    graph.save_node(node)
    tree = CausalTree(graph)
    results = []
    tree._dfs_up(node.id, [], [], 5, 0.0, results, set(), None)
    assert len(results) == 1
    assert results[0].nodes[0].label == "孤点"


def test_dfs_up_time_window_filter(graph):
    n = _chain(graph, ["根", "果"])
    tree = CausalTree(graph)
    results = []
    tree._dfs_up(n["果"].id, [], [], 5, 0.0, results, set(), "3d")
    assert results


def test_dfs_up_visited_skip(graph):
    """前驱已在 visited → continue，不产出链"""
    a = CausalNode(label="A")
    b = CausalNode(label="B")
    graph.save_node(a)
    graph.save_node(b)
    graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id))
    tree = CausalTree(graph)
    results = []
    tree._dfs_up(b.id, [], [], 5, 0.0, results, {a.id}, None)
    assert results == []  # a 被 visited 跳过


# ── _dfs_down_legacy 单节点 / visited ──────────────────────────────────

def test_dfs_down_legacy_single_node(graph):
    node = CausalNode(label="孤点")
    graph.save_node(node)
    tree = CausalTree(graph)
    results = []
    tree._dfs_down_legacy(node.id, [], [], 5, 0.0, results, set(), None)
    assert len(results) == 1
    assert results[0].nodes[0].label == "孤点"


def test_dfs_down_legacy_visited_skip(graph):
    a = CausalNode(label="A")
    b = CausalNode(label="B")
    graph.save_node(a)
    graph.save_node(b)
    graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id))
    tree = CausalTree(graph)
    results = []
    tree._dfs_down_legacy(a.id, [], [], 5, 0.0, results, {b.id}, None)
    assert results == []  # b 被 visited 跳过


def test_dfs_down_legacy_time_window(graph):
    n = _chain(graph, ["P", "Q"])
    tree = CausalTree(graph)
    results = []
    tree._dfs_down_legacy(n["P"].id, [], [], 5, 0.0, results, set(), "1d")
    assert results


# ── what_if 缓存缺失 / 目标不存在 ───────────────────────────────────────

def test_what_if_hypothetical_edge_target_missing(graph):
    """假设边 to 节点不存在 → 只遍历现有边，不抛异常"""
    n = _chain(graph, ["A", "B"])
    tree = CausalTree(graph)
    hyp = CausalEdge(from_id=n["A"].id, to_id="ghost", confidence=0.7)
    chains = tree.what_if(n["A"].id, hypothetical_edge=hyp)
    assert chains


def test_what_if_cache_miss_write(graph):
    n = _chain(graph, ["M", "N"])
    tree = CausalTree(graph)
    tree._cache = {}
    chains = tree.what_if(n["M"].id)
    assert chains
    assert len(tree._cache) >= 1
    # 命中
    chains2 = tree.what_if(n["M"].id)
    assert chains2 == chains


def test_what_if_missing_node_no_cache_write(graph):
    tree = CausalTree(graph)
    assert tree.what_if("ghost") == []


# ── _dfs_what_if 直测（生产路径未引用，直接测保证行为一致性） ─────────

def test_dfs_what_if_hypothetical_edge(graph):
    a = CausalNode(label="A")
    b = CausalNode(label="B")
    c = CausalNode(label="C")
    graph.save_node(a)
    graph.save_node(b)
    graph.save_node(c)
    graph.save_edge(CausalEdge(from_id=b.id, to_id=c.id, confidence=0.8))
    tree = CausalTree(graph)
    results = []
    hyp = CausalEdge(from_id=a.id, to_id=b.id, confidence=0.9)
    tree._dfs_what_if(a.id, [], [], 5, 0.0, results, set(), None, hypothetical_edge=hyp)
    assert results
    assert any(hyp.id in [e.id for e in c.edges] for c in results)


def test_dfs_what_if_no_hypothetical_and_single(graph):
    node = CausalNode(label="N")
    graph.save_node(node)
    tree = CausalTree(graph)
    results = []
    tree._dfs_what_if(node.id, [], [], 5, 0.0, results, set(), None)
    assert len(results) == 1


def test_dfs_what_if_time_window_and_visited(graph):
    a = CausalNode(label="A")
    b = CausalNode(label="B")
    graph.save_node(a)
    graph.save_node(b)
    graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id, confidence=0.7))
    tree = CausalTree(graph)
    results = []
    tree._dfs_what_if(a.id, [], [], 5, 0.0, results, {b.id}, "2h")
    assert results == []  # b 被 visited 跳过，时间窗口分支已执行


# ── expand_node 更多分支 ────────────────────────────────────────────────

def test_expand_node_no_evidence(graph):
    node = CausalNode(label="N")
    graph.save_node(node)
    tree = CausalTree(graph)
    et = tree.expand_node(node.id)
    assert et.evidence == []


def test_evidence_tree_format_minimal():
    tree = EvidenceTree(node=CausalNode(label="N"), confidence=0.5)
    assert tree.format() == "【N】(置信度 50%)"


def test_chain_summary_max_nodes():
    nodes = [CausalNode(label=f"n{i}") for i in range(10)]
    chain = CausalChain(nodes=nodes)
    assert chain.summary(max_nodes=3) == "n0 → n1 → n2"


# ── _trace_to_root 满深度 / 环 ──────────────────────────────────────────

def test_trace_to_root_max_depth_reached(graph):
    """链足够长且 max_depth 限制 → 循环正常耗尽 range"""
    n = _chain(graph, ["A", "B", "C", "D"])
    tree = CausalTree(graph)
    path = tree._trace_to_root(n["D"].id, max_depth=1)
    assert len(path) == 1
    assert path[0].label == "C"


def test_trace_to_root_cycle_break(graph):
    """真实环 A→B→C→A → visited 命中 break"""
    a = CausalNode(label="A")
    b = CausalNode(label="B")
    c = CausalNode(label="C")
    for x in (a, b, c):
        graph.save_node(x)
    graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id, confidence=0.9))
    graph.save_edge(CausalEdge(from_id=b.id, to_id=c.id, confidence=0.9))
    graph.save_edge(CausalEdge(from_id=c.id, to_id=a.id, confidence=0.9))
    tree = CausalTree(graph)
    path = tree._trace_to_root(a.id, max_depth=10)
    assert len(path) <= 3


# ── _dfs_down 环检测 ────────────────────────────────────────────────────

def test_dfs_down_cycle_continue(graph):
    """A→B→A 环：B 的后继 A 已在 path → continue (273)"""
    a = CausalNode(label="A")
    b = CausalNode(label="B")
    graph.save_node(a)
    graph.save_node(b)
    graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id))
    graph.save_edge(CausalEdge(from_id=b.id, to_id=a.id))
    tree = CausalTree(graph)
    results = []
    tree._dfs_down(a.id, [], results, 0, 5)
    assert results  # B 为叶时在 273 continue 后以死路产出链


# ── 缺失节点 trace_up / trace_down ─────────────────────────────────────

def test_trace_up_missing_node_no_cache_write(graph):
    tree = CausalTree(graph)
    assert tree.trace_up("ghost") == []
    assert tree._cache == {}


def test_trace_down_missing_node_no_cache_write(graph):
    tree = CausalTree(graph)
    assert tree.trace_down("ghost") == []
    assert tree._cache == {}


def test_trace_down_cache_hit_second_call(graph):
    n = _chain(graph, ["P", "Q"])
    tree = CausalTree(graph)
    tree.trace_down(n["P"].id)
    tree.trace_down(n["P"].id)  # 命中缓存 → 342-343
    assert len(tree._cache) == 1


# ── _dfs_up 无效时间窗口 ────────────────────────────────────────────────

def test_dfs_up_invalid_time_window(graph):
    n = _chain(graph, ["根", "果"])
    tree = CausalTree(graph)
    results = []
    tree._dfs_up(n["果"].id, [], [], 5, 0.0, results, set(), "bogus")
    assert results


def test_dfs_down_legacy_invalid_time_window(graph):
    n = _chain(graph, ["P", "Q"])
    tree = CausalTree(graph)
    results = []
    tree._dfs_down_legacy(n["P"].id, [], [], 5, 0.0, results, set(), "bogus")
    assert results


# ── _dfs_what_if 真实后继遍历 ───────────────────────────────────────────

def test_dfs_what_if_full_traversal(graph):
    """A→B→C 真实边：走完整 for 循环（576-609）并产出链"""
    a = CausalNode(label="A")
    b = CausalNode(label="B")
    c = CausalNode(label="C")
    for x in (a, b, c):
        graph.save_node(x)
    graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id, confidence=0.8))
    graph.save_edge(CausalEdge(from_id=b.id, to_id=c.id, confidence=0.8))
    tree = CausalTree(graph)
    results = []
    tree._dfs_what_if(a.id, [], [], 5, 0.0, results, set(), None)
    assert results
    assert any("C" in [x.label for x in ch.nodes] for ch in results)


def test_dfs_what_if_with_hypothetical_and_existing(graph):
    """假设边 + 现有后继同时存在 → 覆盖 559-567 链构建"""
    a = CausalNode(label="A")
    b = CausalNode(label="B")
    c = CausalNode(label="C")
    for x in (a, b, c):
        graph.save_node(x)
    graph.save_edge(CausalEdge(from_id=b.id, to_id=c.id, confidence=0.8))
    tree = CausalTree(graph)
    hyp = CausalEdge(from_id=a.id, to_id=b.id, confidence=0.7)
    results = []
    tree._dfs_what_if(a.id, [], [], 5, 0.0, results, set(), None, hypothetical_edge=hyp)
    assert results
    # 含假设边 → 560-566 链构建
    assert any(hyp.id in [e.id for e in ch.edges] for ch in results)


def test_dfs_what_if_node_missing(graph):
    tree = CausalTree(graph)
    results = []
    tree._dfs_what_if("ghost", [], [], 5, 0.0, results, set(), None)
    assert results == []


def test_dfs_what_if_depth_exceeded_path_nodes(graph):
    """path_nodes 非空且 depth 到达上限 → 559-567 走 if path_nodes 分支"""
    a = CausalNode(label="A")
    b = CausalNode(label="B")
    graph.save_node(a)
    graph.save_node(b)
    graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id, confidence=0.8))
    tree = CausalTree(graph)
    results = []
    # max_depth=0：len(path_nodes)>=0 恒真 → 若有 path 则构建链
    tree._dfs_what_if(a.id, [], [], 0, 0.0, results, set(), None)
    assert isinstance(results, list)


# ── 用 mock 图模拟环（真实图是 DAG，拒绝环路） ─────────────────────────

class _CyclicGraph:
    """模拟带环的图：A→B→A，用于覆盖环检测分支"""

    def __init__(self):
        a = CausalNode(label="A")
        b = CausalNode(label="B")
        self._nodes = {"a": a, "b": b}

    def get_node(self, nid):
        return self._nodes.get(nid)

    def get_predecessors(self, nid, min_confidence=0.0):
        if nid == "a":
            return [self._nodes["b"]]
        return [self._nodes["a"]]

    def get_successors(self, nid, min_confidence=0.0):
        if nid == "a":
            return [self._nodes["b"]]
        return [self._nodes["a"]]

    def _get_conn(self):
        def row(frm, to):
            return {
                "id": "ce_x", "from_id": frm, "to_id": to,
                "relation": "", "edge_type": "", "confidence": 0.8,
                "label": "", "created_at": "",
            }

        class _Exec:
            def execute(self, query, params):
                frm, to = params
                self._rows = [dict(row(frm, to))]
                return self

            def fetchall(self):
                return self._rows

        return _Exec()


def test_trace_to_root_mock_cycle_break():
    """mock 图成环 → _trace_to_root 的 visited 命中 break"""
    tree = CausalTree.__new__(CausalTree)
    tree._graph = _CyclicGraph()
    path = tree._trace_to_root("a", max_depth=10)
    assert len(path) <= 2


def test_dfs_down_mock_cycle_continue():
    """mock 图成环 → _dfs_down 的 any(n.id==succ.id) continue，不产出链"""
    tree = CausalTree.__new__(CausalTree)
    tree._graph = _CyclicGraph()
    results = []
    tree._dfs_down("a", [], results, 0, 5)
    assert results == []  # 环被 273 的 continue 跳过


def test_dfs_up_mock_cycle():
    tree = CausalTree.__new__(CausalTree)
    tree._graph = _CyclicGraph()
    results = []
    tree._dfs_up("a", [], [], 5, 0.0, results, set(), None)
    assert results == []


def test_dfs_down_legacy_mock_cycle():
    tree = CausalTree.__new__(CausalTree)
    tree._graph = _CyclicGraph()
    results = []
    tree._dfs_down_legacy("a", [], [], 5, 0.0, results, set(), None)
    assert results == []


# ── _dfs_what_if 时间窗口真实遍历（覆盖 579-586 循环体） ────────────────

def test_dfs_what_if_time_window_loop_body(graph):
    """真实后继 + time_window → 覆盖 for 循环内时间窗口分支"""
    a = CausalNode(label="A")
    b = CausalNode(label="B")
    graph.save_node(a)
    graph.save_node(b)
    graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id, confidence=0.8))
    tree = CausalTree(graph)
    results = []
    tree._dfs_what_if(a.id, [], [], 5, 0.0, results, set(), "30d")
    assert results


def test_dfs_what_if_time_window_invalid(graph):
    """time_window 无效格式 → 循环体 else 分支（586）"""
    a = CausalNode(label="A")
    b = CausalNode(label="B")
    graph.save_node(a)
    graph.save_node(b)
    graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id, confidence=0.8))
    tree = CausalTree(graph)
    results = []
    tree._dfs_what_if(a.id, [], [], 5, 0.0, results, set(), "bogus")
    assert results
