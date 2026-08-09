"""
因果系统完整测试 — 因果图、因果树、深度回忆、反事实推理
"""
import pytest
import threading
import tempfile
import os

from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
from modules.memory.causal_tree import CausalTree


class TestCausalGraphBasic:
    def setup_method(self):
        self._tmp_db = tempfile.mktemp(suffix=".db")
        CausalGraph._instance = None
        CausalGraph._lock = threading.Lock()
        self.graph = CausalGraph(db_path=self._tmp_db)
        self.graph.clear_all()

    def teardown_method(self):
        if hasattr(self, '_tmp_db') and os.path.exists(self._tmp_db):
            try:
                os.remove(self._tmp_db)
            except Exception:
                pass
        CausalGraph._instance = None
        CausalGraph._lock = threading.Lock()

    def test_empty_graph(self):
        assert self.graph.list_nodes() == []
        assert self.graph.find_anchor_nodes("anything") == []
        assert self.graph.get_neighbors("nonexistent", hops=1) == []
        assert self.graph.get_predecessors("nonexistent") == []
        assert self.graph.get_node("nonexistent") is None

    def test_isolated_node(self):
        nid = self.graph.save_node(CausalNode(label="孤立节点"))
        assert self.graph.get_node(nid) is not None
        assert self.graph.delete_node(nid) is True

    def test_single_edge_and_2hop(self):
        a = CausalNode(label="A"); self.graph.save_node(a)
        b = CausalNode(label="B"); self.graph.save_node(b)
        c = CausalNode(label="C"); self.graph.save_node(c)
        self.graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id))
        self.graph.save_edge(CausalEdge(from_id=b.id, to_id=c.id))
        n1 = self.graph.get_neighbors(a.id, hops=1)
        assert len(n1) == 1
        assert n1[0][0].label == "B"
        n2 = self.graph.get_neighbors(a.id, hops=2)
        assert len(n2) == 2

    def test_cycle_rejected(self):
        a = CausalNode(label="A"); self.graph.save_node(a)
        b = CausalNode(label="B"); self.graph.save_node(b)
        c = CausalNode(label="C"); self.graph.save_node(c)
        self.graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id))
        self.graph.save_edge(CausalEdge(from_id=b.id, to_id=c.id))
        result = self.graph.save_edge(CausalEdge(from_id=c.id, to_id=a.id))
        assert result is None

    def test_neighbor_filter(self):
        a = CausalNode(label="root"); self.graph.save_node(a)
        b = self.graph.save_node(CausalNode(label="cause1"))
        c = self.graph.save_node(CausalNode(label="cause2"))
        self.graph.save_edge(CausalEdge(from_id=c, to_id=a.id, relation="prevents"))
        neighbors = self.graph.get_neighbors(a.id, hops=1, relation_filter="prevents")
        assert len(neighbors) == 1

    def test_anchor_search(self):
        self.graph.save_node(CausalNode(label="项目延期", keywords=["延期"]))
        anchors = self.graph.find_anchor_nodes("为什么项目延期")
        assert len(anchors) >= 1

    def test_get_edge_stats(self):
        a = CausalNode(label="A"); self.graph.save_node(a)
        b = CausalNode(label="B"); self.graph.save_node(b)
        self.graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id))
        stats = self.graph.get_edge_stats()
        assert stats["total_edges"] >= 1

    def test_list_all_edges(self):
        a = CausalNode(label="A"); self.graph.save_node(a)
        b = CausalNode(label="B"); self.graph.save_node(b)
        self.graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id))
        edges = self.graph.list_all_edges()
        assert len(edges) == 1

    def test_list_all_edges_with_filter(self):
        a = CausalNode(label="A"); self.graph.save_node(a)
        b = CausalNode(label="B"); self.graph.save_node(b)
        c = CausalNode(label="C"); self.graph.save_node(c)
        self.graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id))
        self.graph.save_edge(CausalEdge(from_id=a.id, to_id=c.id))
        edges = self.graph.list_all_edges(node_ids=[c.id])
        assert len(edges) == 1

    def test_metrics(self):
        a = CausalNode(label="A"); self.graph.save_node(a)
        b = CausalNode(label="B"); self.graph.save_node(b)
        self.graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id))
        m = self.graph.get_metrics()
        assert "causal_graph_nodes_total" in m
        prom = self.graph.get_metrics_prometheus()
        assert prom

    def test_record_query_time(self):
        self.graph.record_query_time(0.1)
        self.graph.record_query_time(0.2)
        m = self.graph.get_metrics()
        assert m["causal_graph_query_count"] == 2


class TestCausalTreeAndReasoning:
    def setup_method(self):
        self._tmp_db = tempfile.mktemp(suffix=".db")
        CausalGraph._instance = None
        CausalGraph._lock = threading.Lock()
        self.graph = CausalGraph(db_path=self._tmp_db)
        self._nodes = [
            CausalNode(label="项目延期", node_type="effect"),
            CausalNode(label="需求变更", node_type="cause"),
            CausalNode(label="技术难点", node_type="cause"),
            CausalNode(label="资源不足", node_type="cause"),
        ]
        for n in self._nodes:
            self.graph.save_node(n)
        for i in range(1, 4):
            e = CausalEdge(from_id=self._nodes[i].id, to_id=self._nodes[0].id, relation="causes")
            r = self.graph.save_edge(e)
            assert r is not None, f"Edge {i} failed"
        self.tree = CausalTree(self.graph)

    def teardown_method(self):
        if hasattr(self, '_tmp_db') and os.path.exists(self._tmp_db):
            try:
                os.remove(self._tmp_db)
            except Exception:
                pass
        CausalGraph._instance = None
        CausalGraph._lock = threading.Lock()

    def test_find_node(self):
        nodes = self.graph.find_nodes_by_label("项目延期")
        assert len(nodes) > 0

    def test_trace_up(self):
        nodes = self.graph.find_nodes_by_label("项目延期")
        assert len(nodes) == 1
        chains = self.tree.trace_up(nodes[0].id, max_depth=3)
        assert len(chains) >= 1
        labels = set()
        for chain in chains:
            labels.update(n.label for n in chain.nodes)
        assert "需求变更" in labels

    def test_trace_down(self):
        nodes = self.graph.find_nodes_by_label("需求变更")
        if nodes:
            chains = self.tree.trace_down(nodes[0].id, max_depth=3)
            labels = set()
            for chain in chains:
                labels.update(n.label for n in chain.nodes)
            assert "项目延期" in labels

    def test_trace_up_time_window(self):
        nodes = self.graph.find_nodes_by_label("项目延期")
        assert len(nodes) == 1
        chains = self.tree.trace_up(nodes[0].id, max_depth=3, time_window="365d")
        assert len(chains) >= 1

    def test_what_if(self):
        new_tech = CausalNode(label="新技术", node_type="cause")
        self.graph.save_node(new_tech)
        he = CausalEdge(from_id=new_tech.id, to_id=self._nodes[0].id,
                        relation="causes", confidence=0.8)
        chains = self.tree.what_if(node_id=new_tech.id, hypothetical_edge=he, max_depth=3)
        assert len(chains) >= 1
        labels = [n.label for n in chains[0].nodes]
        assert "新技术" in labels

    def test_expand_node(self):
        nodes = self.graph.find_nodes_by_label("项目延期")
        if nodes:
            tr = self.tree.expand_node(nodes[0].id)
            assert tr.node.label == "项目延期"

    def test_node_merge(self):
        a = CausalNode(label="性能问题"); self.graph.save_node(a)
        b = CausalNode(label="性能问题严重"); self.graph.save_node(b)
        count = self.graph.merge_similar_nodes(similarity_threshold=0.5)
        assert count >= 1

    def test_cache_invalidation(self):
        nodes = self.graph.find_nodes_by_label("项目延期")
        assert len(nodes) > 0
        chains1 = self.tree.trace_up(nodes[0].id, max_depth=3)
        self.tree.invalidate_cache()
        chains2 = self.tree.trace_up(nodes[0].id, max_depth=3)
        assert len(chains2) >= len(chains1) - 1
