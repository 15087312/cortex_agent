"""causal_graph_edit 工具测试：删除因果边/节点 / 事件解关联 / 异常路径"""
import json
import pytest
from unittest.mock import patch

from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
from modules.memory.event_store import EventStore, MemoryEvent

from infra.tool_manager.tools import causal_graph_edit as cge


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """隔离的因果图 + 事件库，替换工具内部的 get_instance"""
    graph = CausalGraph(db_path=str(tmp_path / "causal.db"))
    store = EventStore(
        db_path=str(tmp_path / "mem.db"),
        faiss_index_path=str(tmp_path / "mem.faiss"),
        id_map_path=str(tmp_path / "mem.json"),
    )
    monkeypatch.setattr(CausalGraph, "get_instance", classmethod(lambda cls: graph))
    monkeypatch.setattr(EventStore, "get_instance", classmethod(lambda cls: store))
    return graph, store


def _seed(graph, store):
    a = CausalNode(label="需求频繁变更", keywords=["需求"])
    b = CausalNode(label="项目延期", keywords=["延期"])
    c = CausalNode(label="质量问题", keywords=["质量"])
    graph.save_node(a)
    graph.save_node(b)
    graph.save_node(c)
    edge = CausalEdge(from_id=a.id, to_id=b.id, confidence=0.8)
    graph.save_edge(edge)
    graph.save_edge(CausalEdge(from_id=b.id, to_id=c.id, confidence=0.6))
    ev = MemoryEvent(fact="项目延期了两周", importance=0.9,
                     causal_node_ids=[a.id, b.id])
    store.save_event(ev)
    return a, b, c, edge


def test_delete_edge_success(isolated):
    graph, store = isolated
    a, b, c, edge = _seed(graph, store)
    out = json.loads(cge.causal_graph_edit(
        action="delete_edge", from_label="需求频繁变更", to_label="项目延期"))
    assert out["success"] is True
    assert out["count"] == 1
    assert out["deleted_edges"][0]["confidence"] == 0.8
    assert graph.get_edge(edge.id) is None
    # 其余边不受影响
    assert len(graph.list_all_edges()) == 1


def test_delete_edge_missing_labels(isolated):
    graph, store = isolated
    out = json.loads(cge.causal_graph_edit(action="delete_edge", from_label="", to_label="项目延期"))
    assert "error" in out
    assert "from_label" in out["error"]


def test_delete_edge_no_matching_nodes(isolated):
    graph, store = isolated
    _seed(graph, store)
    out = json.loads(cge.causal_graph_edit(
        action="delete_edge", from_label="不存在的原因", to_label="项目延期"))
    assert "error" in out


def test_delete_edge_no_such_edge(isolated):
    graph, store = isolated
    a, b, c, edge = _seed(graph, store)
    out = json.loads(cge.causal_graph_edit(
        action="delete_edge", from_label="项目延期", to_label="需求频繁变更"))
    # 反向删除 → 不存在
    assert "error" in out
    assert graph.get_edge(edge.id) is not None


def test_delete_node_success_and_clean_events(isolated):
    graph, store = isolated
    a, b, c, edge = _seed(graph, store)
    out = json.loads(cge.causal_graph_edit(action="delete_node", node_label="需求频繁变更"))
    assert out["success"] is True
    assert out["count"] == 1
    assert graph.get_node(a.id) is None
    # 连带删除相关边
    assert len(graph.list_all_edges()) == 1  # 只剩 b→c
    # 事件解除关联
    assert out["cleaned_events"] >= 1
    ev = store.get_event(store.list_events(limit=1)[0].id)
    assert a.id not in ev.causal_node_ids


def test_delete_node_missing_label(isolated):
    graph, store = isolated
    _seed(graph, store)
    out = json.loads(cge.causal_graph_edit(action="delete_node", node_label=""))
    assert "error" in out


def test_delete_node_not_found(isolated):
    graph, store = isolated
    _seed(graph, store)
    out = json.loads(cge.causal_graph_edit(action="delete_node", node_label="幽灵节点"))
    assert "error" in out


def test_unknown_action(isolated):
    graph, store = isolated
    out = json.loads(cge.causal_graph_edit(action="explode"))
    assert "error" in out
    assert "delete_edge" in out["supported"]


def test_exception_path(isolated, monkeypatch):
    graph, store = isolated
    _seed(graph, store)

    def boom(*a, **k):
        raise RuntimeError("存储损坏")

    monkeypatch.setattr(graph, "find_nodes_by_label", boom)
    out = json.loads(cge.causal_graph_edit(action="delete_node", node_label="需求频繁变更"))
    assert "存储损坏" in out["error"]


def test_tool_registered_core_and_mutation():
    from infra.tool_manager.tool_registry import ToolRegistry
    info = ToolRegistry.get_tool("causal_graph_edit")
    assert info is not None
    assert info.core is True
    assert info.category == "mutation"
    assert info.risk_level == "MEDIUM"
