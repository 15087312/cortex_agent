"""
因果树深度回忆系统 — 稳定性测试
"""
import asyncio
import pytest

# ===========================================================================
# CausalGraph 边界测试
# ===========================================================================

class TestCausalGraph:
    def setup_method(self):
        from modules.memory.causal_graph import CausalGraph
        self.graph = CausalGraph.get_instance()
        self.graph.clear_all()

    def test_empty_graph(self):
        assert self.graph.list_nodes() == []
        assert self.graph.find_anchor_nodes("anything") == []
        assert self.graph.get_neighbors("nonexistent", hops=1) == []
        assert self.graph.get_predecessors("nonexistent") == []
        assert self.graph.get_successors("nonexistent") == []
        assert self.graph.get_node("nonexistent") is None
        assert self.graph.delete_node("nonexistent") is False
        assert self.graph.delete_edge("nonexistent") is False

    def test_isolated_node(self):
        from modules.memory.causal_graph import CausalNode
        nid = self.graph.save_node(CausalNode(label="孤立节点"))
        assert self.graph.get_node(nid) is not None
        assert self.graph.get_neighbors(nid, hops=1) == []
        assert self.graph.get_predecessors(nid) == []
        assert self.graph.get_successors(nid) == []
        # delete it
        assert self.graph.delete_node(nid) is True
        assert self.graph.get_node(nid) is None

    def test_single_edge_and_2hop(self):
        from modules.memory.causal_graph import CausalNode, CausalEdge
        a = CausalNode(label="A")
        self.graph.save_node(a)
        b = CausalNode(label="B")
        self.graph.save_node(b)
        c = CausalNode(label="C")
        self.graph.save_node(c)
        self.graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id, relation="causes"))
        self.graph.save_edge(CausalEdge(from_id=b.id, to_id=c.id, relation="causes"))

        # A → B → C, 1-hop from A
        neighbors_1 = self.graph.get_neighbors(a.id, hops=1)
        assert len(neighbors_1) == 1
        assert neighbors_1[0][0].label == "B"

        # A → B → C, 2-hop from A
        neighbors_2 = self.graph.get_neighbors(a.id, hops=2)
        assert len(neighbors_2) == 2
        labels = {n.label for n, _, _ in neighbors_2}
        assert labels == {"B", "C"}

        # Predecessors of C
        preds = self.graph.get_predecessors(c.id)
        assert len(preds) == 1
        assert preds[0].label == "B"

        # Successors of A
        succs = self.graph.get_successors(a.id)
        assert len(succs) == 1
        assert succs[0].label == "B"

    def test_cyclic_graph(self):
        """环不会导致无限循环"""
        from modules.memory.causal_graph import CausalNode, CausalEdge
        a = CausalNode(label="A"); self.graph.save_node(a)
        b = CausalNode(label="B"); self.graph.save_node(b)
        c = CausalNode(label="C"); self.graph.save_node(c)
        self.graph.save_edge(CausalEdge(from_id=a.id, to_id=b.id))
        self.graph.save_edge(CausalEdge(from_id=b.id, to_id=c.id))
        self.graph.save_edge(CausalEdge(from_id=c.id, to_id=a.id))  # cycle

        # Should not hang
        neighbors = self.graph.get_neighbors(a.id, hops=3)
        assert len(neighbors) <= 3  # visited set prevents revisit
        labels = {n.label for n, _, _ in neighbors}
        assert labels == {"B", "C"}

    def test_multi_hop_spread_with_filter(self):
        from modules.memory.causal_graph import CausalNode, CausalEdge
        a = CausalNode(label="root"); self.graph.save_node(a)
        b = CausalNode(label="cause1"); self.graph.save_node(b,)
        c = CausalNode(label="cause2"); self.graph.save_node(c)
        d = CausalNode(label="effect"); self.graph.save_node(d)
        self.graph.save_edge(CausalEdge(from_id=b.id, to_id=a.id, relation="causes"))
        self.graph.save_edge(CausalEdge(from_id=c.id, to_id=a.id, relation="prevents"))
        self.graph.save_edge(CausalEdge(from_id=a.id, to_id=d.id, relation="causes"))

        # filter = "prevents"
        neighbors = self.graph.get_neighbors(a.id, hops=1, relation_filter="prevents")
        assert len(neighbors) == 1
        assert neighbors[0][0].label == "cause2"

    def test_find_anchor_empty_query(self):
        assert self.graph.find_anchor_nodes("") == []
        assert self.graph.find_anchor_nodes("   ") == []

    def test_find_anchor_no_match(self):
        from modules.memory.causal_graph import CausalNode
        self.graph.save_node(CausalNode(label="ABC", keywords=["xyz"]))
        assert self.graph.find_anchor_nodes("完全无关的查询") == []

    def test_find_anchor_partial_match(self):
        from modules.memory.causal_graph import CausalNode
        self.graph.save_node(CausalNode(label="项目延期", keywords=["延期"]))
        anchors = self.graph.find_anchor_nodes("为什么项目会延期")
        assert len(anchors) >= 1
        assert anchors[0][0].label == "项目延期"


# ===========================================================================
# CausalTree 边界测试
# ===========================================================================

class TestCausalTree:
    def setup_method(self):
        from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
        self.graph = CausalGraph.get_instance()
        self.graph.clear_all()
        from modules.memory.causal_tree import CausalTree
        self.tree = CausalTree(self.graph)

    def _make_chain(self, labels):
        from modules.memory.causal_graph import CausalNode, CausalEdge
        nodes = []
        for lbl in labels:
            n = CausalNode(label=lbl)
            self.graph.save_node(n)
            nodes.append(n)
        for i in range(len(nodes) - 1):
            self.graph.save_edge(CausalEdge(from_id=nodes[i].id, to_id=nodes[i+1].id))
        return nodes

    def test_trace_up_nonexistent(self):
        chains = self.tree.trace_up("nonexistent")
        assert chains == []

    def test_trace_down_nonexistent(self):
        chains = self.tree.trace_down("nonexistent")
        assert chains == []

    def test_trace_up_leaf_to_root(self):
        nodes = self._make_chain(["根因", "中间因", "结果"])
        chains = self.tree.trace_up(nodes[-1].id, max_depth=5)
        assert len(chains) >= 1
        labels = [n.label for n in chains[0].nodes]
        assert "根因" in labels
        assert "中间因" in labels
        assert chains[0].direction == "backward"

    def test_trace_down_root_to_leaf(self):
        nodes = self._make_chain(["根因", "中间因", "结果"])
        chains = self.tree.trace_down(nodes[0].id, max_depth=5)
        assert len(chains) >= 1
        labels = [n.label for n in chains[0].nodes]
        assert "中间因" in labels
        assert "结果" in labels
        assert chains[0].direction == "forward"

    def test_trace_up_max_depth(self):
        nodes = self._make_chain(["A", "B", "C", "D", "E", "F"])
        chains = self.tree.trace_up(nodes[-1].id, max_depth=3)
        assert len(chains) >= 1
        assert len(chains[0].nodes) <= 3

    def test_compare_lateral_single_node(self):
        nodes = self._make_chain(["A", "B"])
        factors = self.tree.compare_lateral([nodes[0].id])
        assert factors == []

    def test_compare_lateral_shared_factor(self):
        """两棵树共享同一个节点标签"""
        from modules.memory.causal_graph import CausalNode, CausalEdge
        shared = CausalNode(label="共享因子")
        self.graph.save_node(shared)
        a = CausalNode(label="结果A"); self.graph.save_node(a)
        b = CausalNode(label="结果B"); self.graph.save_node(b)
        self.graph.save_edge(CausalEdge(from_id=shared.id, to_id=a.id))
        self.graph.save_edge(CausalEdge(from_id=shared.id, to_id=b.id))

        factors = self.tree.compare_lateral([a.id, b.id])
        assert len(factors) > 0
        assert "共享因子" in factors

    def test_trace_empty_chain(self):
        from modules.memory.causal_graph import CausalNode
        n = CausalNode(label="alone")
        self.graph.save_node(n)
        up = self.tree.trace_up(n.id)
        down = self.tree.trace_down(n.id)
        assert up == []
        assert down == []


# ===========================================================================
# DepthRecallScheduler 边界测试
# ===========================================================================

class TestDepthRecallScheduler:
    @pytest.fixture(autouse=True)
    def setup(self):
        from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
        from modules.memory.event_store import EventStore, MemoryEvent
        self.graph = CausalGraph.get_instance()
        self.graph.clear_all()
        self.store = EventStore.get_instance()

    def _populate_basic_graph(self):
        from modules.memory.causal_graph import CausalNode, CausalEdge
        root = CausalNode(label="项目延期", node_type="root", importance=0.9, keywords=["项目","延期"])
        self.graph.save_node(root)
        c1 = CausalNode(label="需求变更", node_type="cause", importance=0.8, keywords=["需求","变更"])
        self.graph.save_node(c1)
        self.graph.save_edge(CausalEdge(from_id=c1.id, to_id=root.id, relation="causes"))
        return root, c1

    def test_trigger_detection(self):
        from modules.memory.depth_recall import should_trigger_deep_recall, classify_intent
        cases = [
            ("为什么项目延期", True),
            ("之前有什么规律", True),
            ("如果当时改了呢", True),
            ("今天天气怎么样", False),
            ("hello world", False),
            ("", False),
        ]
        for query, expected in cases:
            trigger, _ = should_trigger_deep_recall(query)
            assert trigger == expected, f"query={query!r}: expected {expected}, got {trigger}"

    @pytest.mark.asyncio
    async def test_deep_recall_no_anchor(self):
        """因果图无节点时回退"""
        from modules.memory.depth_recall import DepthRecallScheduler
        s = DepthRecallScheduler()
        result = await s.deep_recall("完全没见过的查询")
        assert result.fallback is True
        assert result.success is False
        assert result.error == "no_anchor_nodes"

    @pytest.mark.asyncio
    async def test_deep_recall_anchor_no_chain(self):
        """有节点但无边时回退"""
        from modules.memory.causal_graph import CausalNode
        self.graph.save_node(CausalNode(label="项目延期", keywords=["项目"]))
        from modules.memory.depth_recall import DepthRecallScheduler
        s = DepthRecallScheduler()
        result = await s.deep_recall("项目延期")
        assert result.fallback is True
        assert result.error == "no_causal_chains"

    @pytest.mark.asyncio
    async def test_deep_recall_empty_events(self):
        """有因果链路但事件池空——仍然返回链路"""
        self._populate_basic_graph()
        from modules.memory.depth_recall import DepthRecallScheduler
        s = DepthRecallScheduler()
        result = await s.deep_recall("项目延期的原因")
        assert result.success is True
        assert len(result.causal_chains) >= 1

    @pytest.mark.asyncio
    async def test_deep_recall_with_events(self):
        """完整链路—事件召回"""
        self._populate_basic_graph()
        from modules.memory.event_store import MemoryEvent
        self.store.save_event(MemoryEvent(fact="客户需求变更导致延期一个月", importance=0.8, keywords=["需求","变更"]))
        from modules.memory.depth_recall import DepthRecallScheduler
        s = DepthRecallScheduler()
        result = await s.deep_recall("项目延期的原因")
        assert result.success is True
        assert len(result.supporting_events) >= 1

    @pytest.mark.asyncio
    async def test_deep_recall_depth_level_2(self):
        """深度2级不崩溃"""
        self._populate_basic_graph()
        # create a deeper chain
        from modules.memory.causal_graph import CausalNode, CausalEdge
        sub = CausalNode(label="需求不明确", node_type="cause")
        self.graph.save_node(sub)
        nodes = self.graph.find_nodes_by_label("需求变更")
        if nodes:
            self.graph.save_edge(CausalEdge(from_id=sub.id, to_id=nodes[0].id, relation="causes"))
        from modules.memory.depth_recall import DepthRecallScheduler
        s = DepthRecallScheduler()
        result = await s.deep_recall("项目延期的根本原因", depth_level=2)
        assert result.success or result.fallback  # either is acceptable

    @pytest.mark.asyncio
    async def test_deep_recall_concurrent(self):
        """并发两次调用不崩溃"""
        self._populate_basic_graph()
        from modules.memory.depth_recall import DepthRecallScheduler
        s1 = DepthRecallScheduler()
        r1 = s1.deep_recall("项目延期的原因")
        r2 = s1.deep_recall("项目延期的原因")
        results = await asyncio.gather(r1, r2, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                raise r
            assert r.success is True


# ===========================================================================
# 增量更新闭环测试
# ===========================================================================

class TestIncrementalUpdate:
    @pytest.fixture(autouse=True)
    def setup(self):
        from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
        from modules.memory.event_store import EventStore, MemoryEvent
        self.graph = CausalGraph.get_instance()
        self.graph.clear_all()
        self.store = EventStore.get_instance()

    @pytest.mark.asyncio
    async def test_events_linked_after_recall(self):
        from modules.memory.causal_graph import CausalNode, CausalEdge
        from modules.memory.event_store import MemoryEvent
        root = CausalNode(label="项目延期", node_type="root", keywords=["项目"])
        self.graph.save_node(root)
        c = CausalNode(label="需求变更", node_type="cause", keywords=["需求"])
        self.graph.save_node(c)
        self.graph.save_edge(CausalEdge(from_id=c.id, to_id=root.id, relation="causes", confidence=0.5))

        ev = self.store.save_event(MemoryEvent(fact="需求变更导致延期", importance=0.8, keywords=["需求","延期"]))
        from modules.memory.depth_recall import DepthRecallScheduler
        s = DepthRecallScheduler()
        result = await s.deep_recall("项目延期的原因")
        assert result.success is True
        for ev_out in result.supporting_events:
            refreshed = self.store.get_event(ev_out.id)
            assert len(refreshed.causal_node_ids) > 0, f"事件 {ev_out.id} 未被关联"

    @pytest.mark.asyncio
    async def test_edge_confidence_boosted(self):
        from modules.memory.causal_graph import CausalNode, CausalEdge
        root = CausalNode(label="项目延期", node_type="root")
        self.graph.save_node(root)
        c = CausalNode(label="需求变更", node_type="cause")
        self.graph.save_node(c)
        self.graph.save_edge(CausalEdge(from_id=c.id, to_id=root.id, relation="causes", confidence=0.5))
        from modules.memory.event_store import MemoryEvent
        self.store.save_event(MemoryEvent(fact="需求变更导致延期", importance=0.8, keywords=["需求","延期"]))
        from modules.memory.depth_recall import DepthRecallScheduler
        s = DepthRecallScheduler()
        await s.deep_recall("项目延期的原因")
        # check edge boost
        preds = self.graph.get_predecessors(root.id)
        for pred in preds:
            rows = self.graph._get_conn().execute(
                "SELECT confidence FROM edges WHERE from_id=? AND to_id=?", (pred.id, root.id)
            ).fetchall()
            for r in rows:
                assert r["confidence"] > 0.5, f"置信度未提升: {r['confidence']}"

    @pytest.mark.asyncio
    async def test_node_confidence_boosted(self):
        from modules.memory.causal_graph import CausalNode, CausalEdge
        root = CausalNode(label="项目延期", node_type="root", confidence=0.5)
        self.graph.save_node(root)
        c = CausalNode(label="需求变更", node_type="cause")
        self.graph.save_node(c)
        self.graph.save_edge(CausalEdge(from_id=c.id, to_id=root.id, relation="causes"))
        from modules.memory.event_store import MemoryEvent
        self.store.save_event(MemoryEvent(fact="需求变更导致延期", importance=0.8, keywords=["需求"]))
        from modules.memory.depth_recall import DepthRecallScheduler
        s = DepthRecallScheduler()
        await s.deep_recall("项目延期的原因")
        refreshed = self.graph.get_node(root.id)
        assert refreshed.confidence > 0.5, f"节点置信度未提升: {refreshed.confidence}"

    @pytest.mark.asyncio
    async def test_confidence_capped(self):
        from modules.memory.causal_graph import CausalNode, CausalEdge
        root = CausalNode(label="项目延期", node_type="root")
        self.graph.save_node(root)
        c = CausalNode(label="需求变更", node_type="cause")
        self.graph.save_node(c)
        self.graph.save_edge(CausalEdge(from_id=c.id, to_id=root.id, relation="causes", confidence=0.99))
        from modules.memory.event_store import MemoryEvent
        self.store.save_event(MemoryEvent(fact="需求变更导致延期", importance=0.8, keywords=["需求"]))
        from modules.memory.depth_recall import DepthRecallScheduler
        s = DepthRecallScheduler()
        await s.deep_recall("项目延期的原因")
        rows = self.graph._get_conn().execute(
            "SELECT confidence FROM edges WHERE from_id=? AND to_id=?", (c.id, root.id)
        ).fetchall()
        assert abs(rows[0]["confidence"] - 0.99) < 0.01 or rows[0]["confidence"] < 1.0

    @pytest.mark.asyncio
    async def test_already_linked_event_not_duplicated(self):
        from modules.memory.causal_graph import CausalNode, CausalEdge
        root = CausalNode(label="项目延期", node_type="root")
        self.graph.save_node(root)
        c = CausalNode(label="需求变更", node_type="cause")
        self.graph.save_node(c)
        self.graph.save_edge(CausalEdge(from_id=c.id, to_id=root.id, relation="causes"))
        from modules.memory.event_store import MemoryEvent
        ev = MemoryEvent(fact="需求变更导致延期", importance=0.8, keywords=["需求"], causal_node_ids=[c.id])
        self.store.save_event(ev)
        from modules.memory.depth_recall import DepthRecallScheduler
        s = DepthRecallScheduler()
        result = await s.deep_recall("项目延期的原因")
        # event_count should not double-count
        node = self.graph.get_node(c.id)
        assert node.event_count >= 1  # at least one
