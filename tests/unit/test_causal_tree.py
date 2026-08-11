"""CausalTree 图遍历测试（此前 12% 覆盖）：溯源/下游/证据收集/展开"""
import pytest

from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
from modules.memory.causal_tree import CausalTree
from modules.memory.event_store import EventStore, MemoryEvent


@pytest.fixture
def graph(tmp_path):
    return CausalGraph(db_path=str(tmp_path / "causal.db"))


def _chain(graph, labels):
    """创建 labels 链：A → B → C（前者是后者的原因）"""
    nodes = {}
    for l in labels:
        n = CausalNode(label=l, node_type="cause" if l != labels[-1] else "effect")
        graph.save_node(n)
        nodes[l] = n
    for i in range(len(labels) - 1):
        graph.save_edge(CausalEdge(from_id=nodes[labels[i]].id, to_id=nodes[labels[i + 1]].id))
    return nodes


def test_trace_to_root(graph):
    nodes = _chain(graph, ["需求变更", "设计变更", "项目延期"])
    tree = CausalTree(graph)
    path = tree._trace_to_root(nodes["项目延期"].id)
    assert [n.label for n in path] == ["需求变更", "设计变更"]  # 根因在前


def test_trace_to_root_single_node(graph):
    n = CausalNode(label="孤点")
    graph.save_node(n)
    tree = CausalTree(graph)
    assert tree._trace_to_root(n.id) == []


def test_trace_to_root_max_depth(graph):
    # 长链 + 深度限制
    labels = [f"N{i}" for i in range(10)]
    _chain(graph, labels)
    last = labels[-1]
    tree = CausalTree(graph)
    # 找最后一个节点
    nodes = graph.list_nodes()
    node_map = {n.label: n for n in nodes}
    path = tree._trace_to_root(node_map[last].id, max_depth=3)
    assert len(path) <= 3


def test_collect_evidence(graph, tmp_path, monkeypatch):
    from modules.memory.event_store import EventStore as ES
    store = ES(
        db_path=str(tmp_path / "ev.db"),
        faiss_index_path=str(tmp_path / "ev.faiss"),
        id_map_path=str(tmp_path / "ev.json"),
    )
    node = CausalNode(label="目标")
    graph.save_node(node)
    store.save_event(MemoryEvent(fact="低重要性", importance=0.3, causal_node_ids=[node.id]))
    store.save_event(MemoryEvent(fact="高重要性", importance=0.9, causal_node_ids=[node.id]))
    store.save_event(MemoryEvent(fact="无关", importance=0.9))
    monkeypatch.setattr(ES, "_instance", store)

    tree = CausalTree(graph)
    evidence = tree._collect_evidence(node.id, limit=10)
    assert len(evidence) == 2
    assert evidence[0].fact == "高重要性"  # 按重要性降序


def test_expand_node_full(graph, tmp_path, monkeypatch):
    from modules.memory.event_store import EventStore as ES
    store = ES(
        db_path=str(tmp_path / "ev2.db"),
        faiss_index_path=str(tmp_path / "ev2.faiss"),
        id_map_path=str(tmp_path / "ev2.json"),
    )
    nodes = _chain(graph, ["根因", "中间", "结果"])
    store.save_event(MemoryEvent(fact="支撑事件", importance=0.8, causal_node_ids=[nodes["中间"].id]))
    monkeypatch.setattr(ES, "_instance", store)

    tree = CausalTree(graph)
    et = tree.expand_node(nodes["中间"].id)
    assert et.node.label == "中间"
    assert [n.label for n in et.parent_chain] == ["根因"]  # 上游
    assert [c[0].label for c in et.child_chains] == ["结果"]  # 下游
    assert len(et.evidence) == 1
    assert et.evidence[0].fact == "支撑事件"


def test_expand_node_missing_raises(graph):
    tree = CausalTree(graph)
    with pytest.raises(ValueError):
        tree.expand_node("不存在")
