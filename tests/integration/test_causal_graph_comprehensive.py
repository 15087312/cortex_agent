"""因果图系统全面测试
==================
测试因果图存储、查询、推理、共现统计等核心功能
"""
import pytest
import time
import tempfile
import os
from datetime import datetime, timezone

# find_anchor_nodes 等语义查询会真实加载 embedding 模型（BERT），
# 超过 pytest 全局 --timeout=10（与 conscience/image_analyzer 同款）
pytestmark = pytest.mark.timeout(60)


class TestCausalGraphStorage:
    """测试因果图存储功能"""

    def test_create_and_list_nodes(self, tmp_path):
        """创建和列出节点"""
        from modules.memory.causal_graph import CausalGraph, CausalNode

        db_path = str(tmp_path / "test_graph.db")
        graph = CausalGraph(db_path=db_path)

        # 创建节点
        nodes = [
            CausalNode(label="需求变更", node_type="cause", keywords=["需求", "变更"], importance=0.8),
            CausalNode(label="测试不足", node_type="cause", keywords=["测试", "质量"], importance=0.7),
            CausalNode(label="项目延期", node_type="effect", keywords=["延期", "进度"], importance=0.9),
            CausalNode(label="用户投诉", node_type="effect", keywords=["投诉", "满意度"], importance=0.85),
        ]

        for node in nodes:
            node_id = graph.save_node(node)
            assert node_id, "应该返回节点 ID"

        # 列出所有节点
        all_nodes = graph.list_nodes()
        print(f"\n【节点列表测试】")
        print(f"  总节点数: {len(all_nodes)}")
        for n in all_nodes:
            print(f"    - {n.label} (type={n.node_type}, imp={n.importance})")

        assert len(all_nodes) == 4, f"期望 4 个节点，得到 {len(all_nodes)}"

    def test_save_and_get_edge(self, tmp_path):
        """保存和获取边"""
        from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge

        db_path = str(tmp_path / "test_edge.db")
        graph = CausalGraph(db_path=db_path)

        # 创建节点
        n1 = graph.save_node(CausalNode(label="需求变更", keywords=["需求"]))
        n2 = graph.save_node(CausalNode(label="项目延期", keywords=["延期"]))

        # 保存边
        edge = CausalEdge(from_id=n1, to_id=n2, relation="causes", confidence=0.8)
        edge_id = graph.save_edge(edge)
        assert edge_id, "应该返回边 ID"

        # 获取边
        retrieved = graph.get_edge(edge_id)
        assert retrieved is not None
        assert retrieved.from_id == n1
        assert retrieved.to_id == n2
        print(f"\n【边操作测试】✓ 边保存和获取正常")

    def test_dag_cycle_detection(self, tmp_path):
        """DAG 环路检测"""
        from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge

        db_path = str(tmp_path / "test_cycle.db")
        graph = CausalGraph(db_path=db_path)

        # 创建节点 A → B → C
        n1 = graph.save_node(CausalNode(label="A"))
        n2 = graph.save_node(CausalNode(label="B"))
        n3 = graph.save_node(CausalNode(label="C"))

        graph.save_edge(CausalEdge(from_id=n1, to_id=n2))
        graph.save_edge(CausalEdge(from_id=n2, to_id=n3))

        # 尝试创建环路 C → A
        result = graph.save_edge(CausalEdge(from_id=n3, to_id=n1))
        assert result is None, "环路应该被拒绝"

        print(f"\n【DAG 环路检测测试】✓ 环路被正确拒绝")


class TestCausalGraphQuery:
    """测试因果图查询功能"""

    def test_find_anchor_nodes(self, tmp_path):
        """锚点节点查找"""
        from modules.memory.causal_graph import CausalGraph, CausalNode

        db_path = str(tmp_path / "test_anchor.db")
        graph = CausalGraph(db_path=db_path)

        # 创建节点
        nodes = [
            CausalNode(label="数据库性能差", keywords=["数据库", "性能", "慢"]),
            CausalNode(label="查询未优化", keywords=["查询", "索引", "优化"]),
            CausalNode(label="用户体验差", keywords=["体验", "用户", "慢"]),
        ]
        for n in nodes:
            graph.save_node(n)

        # 测试锚点查找 - 用节点标签中的词查询
        anchors = graph.find_anchor_nodes("性能")
        print(f"\n【锚点查找测试】")
        print(f"  查询: '性能'")
        print(f"  找到锚点: {len(anchors)} 个")
        for node, score in anchors:
            print(f"    - {node.label} (score={score:.1f})")

        assert len(anchors) > 0, "应该找到锚点节点"

    def test_get_neighbors(self, tmp_path):
        """邻域扩散"""
        from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge

        db_path = str(tmp_path / "test_neighbors.db")
        graph = CausalGraph(db_path=db_path)

        # 创建因果链：A → B → C → D
        nodes = []
        for label in ["需求变更", "测试不足", "项目延期", "用户投诉"]:
            n = graph.save_node(CausalNode(label=label))
            nodes.append(n)

        graph.save_edge(CausalEdge(from_id=nodes[0], to_id=nodes[1]))
        graph.save_edge(CausalEdge(from_id=nodes[1], to_id=nodes[2]))
        graph.save_edge(CausalEdge(from_id=nodes[2], to_id=nodes[3]))

        # 测试邻域扩散
        neighbors = graph.get_neighbors(nodes[0], hops=2)
        print(f"\n【邻域扩散测试】")
        print(f"  从 '{nodes[0]}' 扩散 2 跳")
        print(f"  找到邻居: {len(neighbors)} 个")
        for node, edge, dist in neighbors:
            print(f"    - {node.label} (距离={dist})")

        assert len(neighbors) >= 2, "应该找到至少 2 个邻居"

    def test_get_predecessors_and_successors(self, tmp_path):
        """前驱和后继节点"""
        from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge

        db_path = str(tmp_path / "test_pred_succ.db")
        graph = CausalGraph(db_path=db_path)

        # 创建：A → B → C
        n1 = graph.save_node(CausalNode(label="A"))
        n2 = graph.save_node(CausalNode(label="B"))
        n3 = graph.save_node(CausalNode(label="C"))

        graph.save_edge(CausalEdge(from_id=n1, to_id=n2))
        graph.save_edge(CausalEdge(from_id=n2, to_id=n3))

        # 测试前驱
        preds = graph.get_predecessors(n2)
        print(f"\n【前驱后继测试】")
        print(f"  B 的前驱: {[n.label for n in preds]}")
        assert len(preds) == 1
        assert preds[0].label == "A"

        # 测试后继
        succs = graph.get_successors(n2)
        print(f"  B 的后继: {[n.label for n in succs]}")
        assert len(succs) == 1
        assert succs[0].label == "C"


class TestCausalTree:
    """测试因果树推理功能"""

    def test_trace_up(self, tmp_path):
        """向上溯源"""
        from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
        from modules.memory.causal_tree import CausalTree

        db_path = str(tmp_path / "test_trace.db")
        graph = CausalGraph(db_path=db_path)
        tree = CausalTree(graph)

        # 创建因果链：需求变更 → 测试不足 → 项目延期 → 用户投诉
        nodes = []
        for label in ["需求变更", "测试不足", "项目延期", "用户投诉"]:
            node_id = graph.save_node(CausalNode(label=label))
            nodes.append(node_id)

        graph.save_edge(CausalEdge(from_id=nodes[0], to_id=nodes[1]))
        graph.save_edge(CausalEdge(from_id=nodes[1], to_id=nodes[2]))
        graph.save_edge(CausalEdge(from_id=nodes[2], to_id=nodes[3]))

        # 测试向上溯源
        chains = tree.trace_up(nodes[3], max_depth=3)
        print(f"\n【向上溯源测试】")
        print(f"  目标: 用户投诉")
        print(f"  找到因果链: {len(chains)} 条")
        for chain in chains:
            labels = [n.label for n in chain.nodes]
            print(f"    {' ← '.join(labels)}")

        assert len(chains) > 0, "应该找到因果链"

    def test_trace_down(self, tmp_path):
        """向下预测"""
        from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
        from modules.memory.causal_tree import CausalTree

        db_path = str(tmp_path / "test_down.db")
        graph = CausalGraph(db_path=db_path)
        tree = CausalTree(graph)

        # 创建因果链
        nodes = []
        for label in ["需求变更", "项目延期", "用户投诉"]:
            node_id = graph.save_node(CausalNode(label=label))
            nodes.append(node_id)

        graph.save_edge(CausalEdge(from_id=nodes[0], to_id=nodes[1]))
        graph.save_edge(CausalEdge(from_id=nodes[1], to_id=nodes[2]))

        # 测试向下预测
        chains = tree.trace_down(nodes[0], max_depth=2)
        print(f"\n【向下预测测试】")
        print(f"  起点: 需求变更")
        print(f"  找到因果链: {len(chains)} 条")
        for chain in chains:
            labels = [n.label for n in chain.nodes]
            print(f"    {' → '.join(labels)}")

        assert len(chains) > 0, "应该找到因果链"

    def test_expand_node(self, tmp_path):
        """展开节点证据"""
        from modules.memory.causal_graph import CausalGraph, CausalNode
        from modules.memory.causal_tree import CausalTree
        from modules.memory.event_store import EventStore, MemoryEvent

        db_path = str(tmp_path / "test_expand.db")
        graph = CausalGraph(db_path=db_path)
        store = EventStore(db_path=db_path)
        tree = CausalTree(graph)

        # 创建节点
        n1 = graph.save_node(CausalNode(label="性能问题", keywords=["性能"]))
        n2 = graph.save_node(CausalNode(label="用户体验差", keywords=["体验"]))
        n3 = graph.save_node(CausalNode(label="数据库慢", keywords=["数据库"]))

        # 创建关联事件
        store.save_event(MemoryEvent(
            fact="查询未加索引导致加载慢",
            keywords=["索引", "查询"],
            causal_node_ids=[n1, n3]
        ))
        store.save_event(MemoryEvent(
            fact="页面响应时间超过 5 秒",
            keywords=["响应", "时间"],
            causal_node_ids=[n1]
        ))

        # 测试展开节点
        evidence = tree.expand_node(n1)
        print(f"\n【节点展开测试】")
        print(f"  节点: {evidence.node.label}")
        print(f"  证据数量: {len(evidence.evidence)}")
        for ev in evidence.evidence:
            print(f"    - {ev.fact[:40]}...")

        assert evidence.node.label == "性能问题"


class TestCooccurrenceStats:
    """测试共现统计功能"""

    def test_auto_edge_creation(self, tmp_path):
        """自动创建因果边"""
        from modules.memory.causal_graph import CausalGraph, CausalNode
        from modules.memory.event_store import EventStore, MemoryEvent

        db_path = str(tmp_path / "test_cooccur.db")
        graph = CausalGraph(db_path=db_path)
        store = EventStore(db_path=db_path)

        # 创建节点
        n1 = graph.save_node(CausalNode(label="需求变更", keywords=["需求"]))
        n2 = graph.save_node(CausalNode(label="项目延期", keywords=["延期"]))
        n3 = graph.save_node(CausalNode(label="测试不足", keywords=["测试"]))

        # 创建共现事件（多次同时出现）
        for i in range(5):
            store.save_event(MemoryEvent(
                fact=f"事件{i}：需求变更导致延期",
                keywords=["需求", "延期"],
                causal_node_ids=[n1, n2]
            ))

        for i in range(5):
            store.save_event(MemoryEvent(
                fact=f"事件{i}：测试不足导致延期",
                keywords=["测试", "延期"],
                causal_node_ids=[n3, n2]
            ))

        # 运行共现统计
        edges_created = graph.update_cooccurrence(store=store, min_cooccur=2)
        print(f"\n【共现统计测试】")
        print(f"  创建边数: {edges_created}")

        # 验证边已创建
        edges = graph.list_all_edges()
        print(f"  总边数: {len(edges)}")
        for e in edges:
            print(f"    - {e.from_id} → {e.to_id} (conf={e.confidence:.2f})")

        assert edges_created > 0, "应该创建因果边"


class TestPerformance:
    """测试性能"""

    def test_query_performance(self, tmp_path):
        """查询性能测试"""
        from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge

        db_path = str(tmp_path / "test_perf.db")
        graph = CausalGraph(db_path=db_path)

        # 创建 100 个节点和 200 条边
        print(f"\n【性能测试】")
        print(f"  创建 100 个节点...")
        nodes = []
        for i in range(100):
            node_id = graph.save_node(CausalNode(label=f"节点{i}", keywords=[f"关键词{i}"]))
            nodes.append(node_id)

        print(f"  创建 200 条边...")
        for i in range(200):
            graph.save_edge(CausalEdge(from_id=nodes[i % 100], to_id=nodes[(i + 1) % 100]))

        # 测试查询性能
        start = time.time()
        for _ in range(10):
            graph.find_anchor_nodes("节点")
            graph.get_neighbors(nodes[0], hops=2)
        query_time = time.time() - start

        print(f"  10 次查询耗时: {query_time*1000:.2f}ms")
        print(f"  平均每次: {query_time/10*1000:.2f}ms")

        # 验证：查询应该在合理时间内完成（语义相似度会增加一些时间，100 节点约 300ms/次）
        assert query_time < 5.0, f"查询应该 <5s，实际 {query_time:.2f}s"


class TestIntegrationWithEventStore:
    """测试与事件存储的集成"""

    def test_full_workflow(self, tmp_path):
        """完整工作流测试"""
        from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
        from modules.memory.causal_tree import CausalTree
        from modules.memory.event_store import EventStore, MemoryEvent

        db_path = str(tmp_path / "test_full.db")
        graph = CausalGraph(db_path=db_path)
        store = EventStore(db_path=db_path)
        tree = CausalTree(graph)

        print(f"\n【完整工作流测试】")

        # 1. 创建因果节点
        print(f"  1. 创建因果节点...")
        n1 = graph.save_node(CausalNode(label="需求变更", node_type="cause", keywords=["需求"]))
        n2 = graph.save_node(CausalNode(label="项目延期", node_type="effect", keywords=["延期"]))
        graph.save_edge(CausalEdge(from_id=n1, to_id=n2, relation="causes", confidence=0.8))

        # 2. 创建关联事件
        print(f"  2. 创建关联事件...")
        for i in range(5):
            store.save_event(MemoryEvent(
                fact=f"需求变更导致项目延期 #{i+1}",
                keywords=["需求变更", "延期"],
                causal_node_ids=[n1, n2],
                importance=0.8
            ))

        # 3. 查询因果知识
        print(f"  3. 查询因果知识...")
        anchors = graph.find_anchor_nodes("项目延期")
        print(f"     找到锚点: {len(anchors)} 个")

        # 4. 向上溯源
        print(f"  4. 向上溯源...")
        chains = tree.trace_up(n2, max_depth=2)
        print(f"     找到因果链: {len(chains)} 条")
        for chain in chains:
            labels = [n.label for n in chain.nodes]
            print(f"       {' ← '.join(labels)}")

        # 5. 展开节点证据
        print(f"  5. 展开节点证据...")
        evidence = tree.expand_node(n2)
        print(f"     证据数量: {len(evidence.evidence)}")

        print(f"\n  ✓ 完整工作流测试通过")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
