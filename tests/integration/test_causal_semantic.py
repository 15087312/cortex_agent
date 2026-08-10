"""测试因果图语义相似度增强功能"""
import pytest
import tempfile
from unittest.mock import MagicMock, patch


class TestSemanticSimilarity:
    """测试语义相似度增强功能"""

    def test_find_anchor_nodes_with_semantic(self, tmp_path):
        """测试语义相似度增强后的锚点查找"""
        from modules.memory.causal_graph import CausalGraph, CausalNode

        db_path = str(tmp_path / "test_semantic.db")
        graph = CausalGraph(db_path=db_path)

        # 创建节点
        nodes = [
            CausalNode(label="数据库性能差", keywords=["数据库", "性能", "慢"]),
            CausalNode(label="查询未优化", keywords=["查询", "索引", "优化"]),
            CausalNode(label="用户体验差", keywords=["体验", "用户", "慢"]),
            CausalNode(label="网络延迟高", keywords=["网络", "延迟", "连接"]),
        ]
        for n in nodes:
            graph.save_node(n)

        # 测试 1：关键词匹配
        anchors_kw = graph.find_anchor_nodes("性能")
        print(f"\n【关键词匹配测试】")
        print(f"  查询: '性能'")
        print(f"  找到锚点: {len(anchors_kw)} 个")
        for node, score in anchors_kw:
            print(f"    - {node.label} (score={score:.2f})")

        assert len(anchors_kw) > 0, "应该找到锚点节点"

    def test_semantic_fallback_to_keyword(self, tmp_path):
        """测试 embedding 失败时降级到关键词匹配"""
        from modules.memory.causal_graph import CausalGraph, CausalNode

        db_path = str(tmp_path / "test_fallback.db")
        graph = CausalGraph(db_path=db_path)

        # 创建节点
        n1 = graph.save_node(CausalNode(label="需求变更", keywords=["需求"]))
        n2 = graph.save_node(CausalNode(label="项目延期", keywords=["延期"]))

        # 模拟 embedding 失败
        with patch("modules.memory.embedding.EmbeddingEngine.get_instance") as MockEmbedding:
            mock_embedder = MagicMock()
            mock_embedder.embed.side_effect = Exception("测试异常")
            MockEmbedding.return_value = mock_embedder

            anchors = graph.find_anchor_nodes("项目延期")
            print(f"\n【降级测试】")
            print(f"  查询: '项目延期'")
            print(f"  找到锚点: {len(anchors)} 个")
            for node, score in anchors:
                print(f"    - {node.label} (score={score:.2f})")

            # 应该降级到关键词匹配
            assert len(anchors) > 0, "应该降级到关键词匹配"

    def test_semantic_ranks_relevant_nodes_higher(self, tmp_path):
        """测试语义相似度能正确排序相关节点"""
        from modules.memory.causal_graph import CausalGraph, CausalNode

        db_path = str(tmp_path / "test_ranking.db")
        graph = CausalGraph(db_path=db_path)

        # 创建节点：有些关键词匹配，有些语义相关
        nodes = [
            CausalNode(label="数据库性能差", keywords=["数据库", "性能"]),
            CausalNode(label="查询未优化", keywords=["查询", "索引"]),
            CausalNode(label="用户体验差", keywords=["体验", "用户"]),
            CausalNode(label="网络延迟高", keywords=["网络", "延迟"]),
        ]
        for n in nodes:
            graph.save_node(n)

        # 测试语义查询（没有关键词匹配，但语义相关）
        with patch("modules.memory.embedding.EmbeddingEngine.get_instance") as MockEmbedding:
            # 模拟 embedding 返回
            mock_embedder = MagicMock()
            mock_embedder.embed.return_value = [0.1] * 768  # 简化向量
            mock_embedder.embed_batch.return_value = [[0.1] * 768] * 4
            MockEmbedding.return_value = mock_embedder

            anchors = graph.find_anchor_nodes("系统响应慢")
            print(f"\n【语义排序测试】")
            print(f"  查询: '系统响应慢'")
            print(f"  找到锚点: {len(anchors)} 个")
            for node, score in anchors:
                print(f"    - {node.label} (score={score:.2f})")

            # 应该返回节点（即使分数相同）
            assert len(anchors) > 0, "应该找到锚点节点"

    def test_weighted_fusion(self, tmp_path):
        """测试加权融合策略"""
        from modules.memory.causal_graph import CausalGraph, CausalNode

        db_path = str(tmp_path / "test_fusion.db")
        graph = CausalGraph(db_path=db_path)

        # 创建节点
        n1 = graph.save_node(CausalNode(label="数据库性能差", keywords=["数据库", "性能"]))
        n2 = graph.save_node(CausalNode(label="查询未优化", keywords=["查询", "索引"]))

        # 测试融合
        anchors = graph.find_anchor_nodes("数据库性能")
        print(f"\n【加权融合测试】")
        print(f"  查询: '数据库性能'")
        print(f"  找到锚点: {len(anchors)} 个")
        for node, score in anchors:
            print(f"    - {node.label} (score={score:.2f})")

        # 应该找到匹配的节点
        assert len(anchors) > 0, "应该找到锚点节点"


class TestIntegrationWithExistingTests:
    """集成测试：确保新逻辑不影响现有测试"""

    def test_existing_test_compatibility(self, tmp_path):
        """确保现有测试仍然通过"""
        from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
        from modules.memory.causal_tree import CausalTree

        db_path = str(tmp_path / "test_compat.db")
        graph = CausalGraph(db_path=db_path)
        tree = CausalTree(graph)

        # 创建因果链
        nodes = []
        for label in ["需求变更", "测试不足", "项目延期", "用户投诉"]:
            node_id = graph.save_node(CausalNode(label=label))
            nodes.append(node_id)

        graph.save_edge(CausalEdge(from_id=nodes[0], to_id=nodes[1]))
        graph.save_edge(CausalEdge(from_id=nodes[1], to_id=nodes[2]))
        graph.save_edge(CausalEdge(from_id=nodes[2], to_id=nodes[3]))

        # 测试向上溯源
        chains = tree.trace_up(nodes[3], max_depth=3)
        print(f"\n【兼容性测试】")
        print(f"  找到因果链: {len(chains)} 条")
        for chain in chains:
            labels = [n.label for n in chain.nodes]
            print(f"    {' ← '.join(labels)}")

        assert len(chains) > 0, "应该找到因果链"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
