"""
因果树深度回忆系统 — 稳定性测试
"""
import asyncio
import pytest
import threading
import tempfile
import os

# ===========================================================================
# 深度回忆系统测试
# ===========================================================================

# TestDepthRecall removed - see test_causal_system.py
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
        # The anchor node's label is included as a shared factor
        assert "A" in factors

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
        # 孤立节点返回单节点链（深度回忆需要至少知道锚点存在）
        assert len(up) == 1
        assert len(down) == 1
        assert up[0].nodes[0].label == "alone"
        assert down[0].nodes[0].label == "alone"


# ===========================================================================
# DepthRecallScheduler 边界测试
# ===========================================================================

class TestDepthRecallScheduler:
    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
        from modules.memory.event_store import EventStore, MemoryEvent
        self.graph = CausalGraph.get_instance()
        self.graph.clear_all()
        # 重置 EventStore 单例，确保 schema 是最新的
        EventStore._instance = None
        self.store = EventStore.get_instance()

        # Mock EmbeddingEngine 以跳过模型加载
        from modules.memory.embedding import EmbeddingEngine
        eng = EmbeddingEngine.get_instance()
        eng._loaded = True
        eng._attempted = True
        eng.dim = 16
        import hashlib
        def _mock_embed(text):
            h = hashlib.md5(text.encode()).digest()
            return [((b - 128) / 128.0) for b in h][:16]
        eng.embed = _mock_embed

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
        """有节点但无边时，返回单节点链（不 fallback）"""
        from modules.memory.causal_graph import CausalNode
        self.graph.save_node(CausalNode(label="项目延期", keywords=["项目"]))
        from modules.memory.depth_recall import DepthRecallScheduler
        from unittest.mock import AsyncMock, patch
        s = DepthRecallScheduler()
        result = await s.deep_recall("项目延期")
        # 修复后：孤立节点也返回单节点链，不再 fallback
        assert result.success is True
        assert len(result.causal_chains) >= 1

    @pytest.mark.asyncio
    async def test_deep_recall_empty_events(self):
        """有因果链路但事件池空——仍然返回链路"""
        self._populate_basic_graph()
        from modules.memory.depth_recall import DepthRecallScheduler
        from unittest.mock import AsyncMock, patch
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
        from unittest.mock import AsyncMock, patch
        s = DepthRecallScheduler()
        result = await s.deep_recall("项目延期的原因")
        assert result.success is True
        assert len(result.causal_chains) >= 1

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
        from unittest.mock import AsyncMock, patch
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
        # 重置 EventStore 单例，确保 schema 是最新的
        EventStore._instance = None
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
        """事件已关联到节点后，再次深度回忆不应重复计数。"""
        from modules.memory.causal_graph import CausalNode, CausalEdge
        root = CausalNode(label="项目延期", node_type="root", keywords=["项目", "延期"])
        self.graph.save_node(root)
        c = CausalNode(label="需求变更", node_type="cause", keywords=["需求", "需求变更"])
        self.graph.save_node(c)
        self.graph.save_edge(CausalEdge(from_id=c.id, to_id=root.id, relation="causes"))
        from modules.memory.event_store import MemoryEvent
        ev = MemoryEvent(fact="需求变更导致延期", importance=0.8, keywords=["需求", "需求变更"])
        self.store.save_event(ev)
        from modules.memory.depth_recall import DepthRecallScheduler
        s = DepthRecallScheduler()
        result = await s.deep_recall("项目延期的原因")
        # 第一次回忆：事件应被关联到因果节点，event_count 为 1
        node = self.graph.get_node(c.id)
        assert node.event_count >= 1, f"首次回忆应关联事件: {node.event_count}"

        # 第二次回忆：已关联的事件不应重复计数（不 double-count）
        result2 = await s.deep_recall("项目延期的原因")
        node2 = self.graph.get_node(c.id)
        assert node2.event_count == node.event_count, (
            f"再次回忆不应重复计数: {node.event_count} → {node2.event_count}"
        )
