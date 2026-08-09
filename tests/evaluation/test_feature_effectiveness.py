"""
高级功能使用评估测试
====================
评估那些"看起来高级"但实际使用效果不明的功能模块。
不验证代码能否运行，而是验证功能是否真的有用。
"""
import pytest
import json
from unittest.mock import MagicMock, AsyncMock


# ─────────────────────────────────────────────
# 1. Attention 系统评估
# ─────────────────────────────────────────────

class TestAttentionEffectiveness:
    """评估注意力系统的实际区分能力"""

    def test_vector_differentiation(self):
        """5D向量是否对不同输入有实际区分度"""
        from modules.attention.analyzer import AttentionAnalyzer

        analyzer = AttentionAnalyzer.__new__(AttentionAnalyzer)
        analyzer._cfg = MagicMock(importance_enabled=True, force_static_level=None)

        # 测试不同场景的向量差异
        scenarios = [
            ("紧急故障！", "紧急场景"),
            ("帮我写个文档", "普通任务"),
            ("今天天气不错", "闲聊"),
            ("系统崩溃了，立刻修复", "高紧急任务"),
        ]

        vectors = []
        for text, label in scenarios:
            result = analyzer.analyze(text)
            vectors.append({
                "label": label,
                "score": result.importance_score,
                "vector": result.vector.to_dict() if result.vector else None,
            })

        # 评估：紧急场景的分数应该明显高于闲聊
        urgent_score = next(v for v in vectors if v["label"] == "紧急场景")["score"]
        chat_score = next(v for v in vectors if v["label"] == "闲聊")["score"]

        print(f"\n【Attention 评估】")
        for v in vectors:
            print(f"  {v['label']:12s} score={v['score']:.2f} emotion={v['vector']['emotion']:.2f}")

        assert urgent_score > chat_score, "紧急场景分数应高于闲聊"

    def test_vector_practical_value(self):
        """5D向量是否对prompt注入有实际价值"""
        from modules.attention.analyzer import AttentionAnalyzer

        analyzer = AttentionAnalyzer.__new__(AttentionAnalyzer)
        analyzer._cfg = MagicMock(importance_enabled=True, force_static_level=None)

        # 模拟实际使用场景
        result = analyzer.analyze("系统崩溃了，需要紧急修复")
        context = result.importance_context

        print(f"\n【Attention 实际输出】")
        print(f"  重要性上下文: {context[:100]}...")

        # 验证：输出应该包含明确的分数和建议
        assert "任务重要性" in context
        assert result.importance_score > 0.7, "紧急场景应该得到高分"

    def test_vector_vs_scalar(self):
        """对比：5D向量 vs 单一分数，哪个更有用"""
        from modules.attention.analyzer import AttentionAnalyzer

        analyzer = AttentionAnalyzer.__new__(AttentionAnalyzer)
        analyzer._cfg = MagicMock(importance_enabled=True, force_static_level=None)

        # 测试：情感强度是否真的能区分场景
        result_urgent = analyzer.analyze("紧急故障！系统崩溃！")
        result_normal = analyzer.analyze("帮我查一下文档")

        print(f"\n【Attention 向量对比】")
        print(f"  紧急场景 emotion={result_urgent.vector.emotion:.2f}")
        print(f"  普通场景 emotion={result_normal.vector.emotion:.2f}")

        # 情感强度应该有区分
        assert result_urgent.vector.emotion > result_normal.vector.emotion, \
            "紧急场景的情感强度应高于普通场景"


# ─────────────────────────────────────────────
# 2. Causal Memory 系统评估
# ─────────────────────────────────────────────

class TestCausalMemoryEffectiveness:
    """评估因果记忆系统的实际价值"""

    def test_retrieval_quality(self, tmp_path):
        """因果检索是否比关键词检索更准"""
        from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
        from modules.memory.event_store import EventStore, MemoryEvent

        # 设置因果图
        db_path = str(tmp_path / "test_causal.db")
        graph = CausalGraph(db_path=db_path)
        store = EventStore(db_path=db_path)

        # 创建因果链：需求变更 → 测试不足 → 项目延期
        n1 = CausalNode(label="需求变更", node_type="cause", keywords=["需求", "变更"])
        n2 = CausalNode(label="测试不足", node_type="cause", keywords=["测试", "质量"])
        n3 = CausalNode(label="项目延期", node_type="effect", keywords=["延期", "进度"])

        graph.save_node(n1)
        graph.save_node(n2)
        graph.save_node(n3)
        graph.save_edge(CausalEdge(from_id=n1.id, to_id=n3.id, relation="causes"))
        graph.save_edge(CausalEdge(from_id=n2.id, to_id=n3.id, relation="causes"))

        # 创建关联事件
        ev1 = MemoryEvent(fact="需求频繁变更导致开发周期延长", keywords=["需求变更", "延期"],
                         causal_node_ids=[n1.id, n3.id], importance=0.8)
        ev2 = MemoryEvent(fact="测试时间不足导致上线后问题多", keywords=["测试", "质量"],
                         causal_node_ids=[n2.id, n3.id], importance=0.7)
        store.save_event(ev1)
        store.save_event(ev2)

        # 测试检索
        from modules.memory.event_retrieval import EventRetrieval

        # 查询"为什么延期"应该能关联到因果链
        retrieval = EventRetrieval()
        events = retrieval.retrieve("项目延期的原因", max_results=5, threshold=0.0)

        print(f"\n【Causal Memory 检索评估】")
        print(f"  查询: '项目延期的原因'")
        print(f"  返回事件数: {len(events)}")
        for ev in events[:3]:
            print(f"    - {ev.fact[:50]}...")

        # 验证：应该能返回关联事件
        assert len(events) > 0, "应该能检索到关联事件"

    def test_trace_effectiveness(self, tmp_path):
        """因果链追踪是否真的有用"""
        from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
        from modules.memory.causal_tree import CausalTree

        db_path = str(tmp_path / "test_trace.db")
        graph = CausalGraph(db_path=db_path)

        # 创建复杂因果链
        nodes = []
        for label in ["需求变更", "测试不足", "人员流失", "项目延期", "客户投诉"]:
            n = CausalNode(label=label, keywords=[label[:2]])
            graph.save_node(n)
            nodes.append(n)

        # 构建因果图
        graph.save_edge(CausalEdge(from_id=nodes[0].id, to_id=nodes[3].id))  # 需求变更 → 延期
        graph.save_edge(CausalEdge(from_id=nodes[1].id, to_id=nodes[3].id))  # 测试不足 → 延期
        graph.save_edge(CausalEdge(from_id=nodes[2].id, to_id=nodes[3].id))  # 人员流失 → 延期
        graph.save_edge(CausalEdge(from_id=nodes[3].id, to_id=nodes[4].id))  # 延期 → 投诉

        tree = CausalTree(graph)

        # 追踪"项目延期"的原因
        target = graph.find_nodes_by_label("项目延期")[0]
        chains = tree.trace_up(target.id, max_depth=2)

        print(f"\n【Causal Memory 追踪评估】")
        print(f"  目标节点: 项目延期")
        print(f"  找到原因链数: {len(chains)}")
        for chain in chains:
            labels = [n.label for n in chain.nodes]
            print(f"    {' ← '.join(labels)}")

        # 验证：应该能追踪到多个原因
        assert len(chains) > 0, "应该找到原因链"
        all_causes = set()
        for chain in chains:
            all_causes.update(n.label for n in chain.nodes[:-1])  # 排除目标节点
        assert "需求变更" in all_causes or "测试不足" in all_causes, "应该找到具体原因"


# ─────────────────────────────────────────────
# 3. Conscience 系统评估
# ─────────────────────────────────────────────

class TestConscienceEffectiveness:
    """评估良知系统的内心独白质量"""

    def test_knowledge_extraction(self, tmp_path):
        """因果知识提取是否准确"""
        from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
        from modules.memory.event_store import EventStore, MemoryEvent
        from modules.thinking.conscience import Conscience

        db_path = str(tmp_path / "test_conscience.db")
        graph = CausalGraph(db_path=db_path)
        store = EventStore(db_path=db_path)

        # 创建因果知识
        n1 = CausalNode(label="性能问题", node_type="cause", keywords=["性能", "慢"])
        n2 = CausalNode(label="用户体验差", node_type="effect", keywords=["体验", "用户"])
        graph.save_node(n1)
        graph.save_node(n2)
        graph.save_edge(CausalEdge(from_id=n1.id, to_id=n2.id))

        # 创建关联事件
        ev = MemoryEvent(fact="数据库查询未加索引导致页面加载慢",
                        keywords=["性能", "索引"], causal_node_ids=[n1.id, n2.id])
        store.save_event(ev)

        cons = Conscience()
        knowledge = cons._get_causal_knowledge("用户反馈系统很慢")

        print(f"\n【Conscience 知识提取评估】")
        print(f"  查询: '用户反馈系统很慢'")
        print(f"  提取知识:\n{knowledge}")

        # 验证：应该提取到相关因果知识
        assert "性能问题" in knowledge or "用户体验" in knowledge, "应该提取到因果知识"

    def test_inner_monologue_quality(self, tmp_path):
        """内心独白是否自然、有用"""
        from modules.thinking.conscience import Conscience

        # 模拟一个有模型的场景
        class MockModel:
            async def generate(self, prompt, max_tokens=0, temperature=0):
                return "（我记得上次遇到类似问题时，数据库索引是个关键因素，这次也应该先检查查询性能）"

        cons = Conscience(model_client=MockModel())
        cons.add_to_dialog("user", "系统好慢")

        import asyncio
        result = asyncio.run(cons.think("系统好慢"))

        print(f"\n【Conscience 内心独白评估】")
        print(f"  输出: {result}")

        # 验证：输出应该是第一人称、自然的内心独白
        assert len(result) > 10, "内心独白应该有足够长度"
        assert "我" in result or "记得" in result, "应该是第一人称视角"


# ─────────────────────────────────────────────
# 4. EventReducer 评估
# ─────────────────────────────────────────────

class TestEventReducerEffectiveness:
    """评估记忆提炼质量"""

    def test_parse_response_quality(self):
        """LLM返回的事件格式是否正确解析"""
        from modules.memory.event_reducer import EventReducer

        reducer = EventReducer.__new__(EventReducer)
        reducer._model_client = None

        # 模拟LLM返回
        test_cases = [
            # 标准JSON
            json.dumps({
                "events": [
                    {"fact": "完成了用户认证模块重构", "thought": "用了JWT替代Session",
                     "lesson": "无状态认证更适合微服务", "keywords": ["认证", "JWT"],
                     "importance": "high", "type": "strategy"}
                ],
                "causal_nodes": [],
                "causal_edges": []
            }),
            # 带markdown包裹
            "```json\n" + json.dumps({
                "events": [{"fact": "修复了内存泄漏", "keywords": ["内存", "泄漏"]}]
            }) + "\n```",
            # 旧格式（纯数组）
            json.dumps([{"fact": "部署了新版本", "keywords": ["部署"]}]),
        ]

        print(f"\n【EventReducer 解析评估】")
        for i, text in enumerate(test_cases):
            result = reducer._parse_response(text)
            events = result.get("events", [])
            print(f"  案例{i+1}: {len(events)} 个事件")
            for ev in events:
                print(f"    - {ev.fact[:40]}... (type={ev.type}, imp={ev.importance})")

            assert len(events) > 0, f"案例{i+1}应该解析出事件"

    def test_importance_scoring(self):
        """重要性评分是否合理"""
        from modules.memory.event_reducer import _parse_importance

        test_cases = [
            ("critical", 1.0),
            ("high", 0.70),
            ("medium", 0.40),
            ("low", 0.15),
            ("trivial", 0.03),
            (0.8, 0.8),  # 数值格式
            (1.5, 1.0),  # 越界处理
        ]

        print(f"\n【EventReducer 重要性评分评估】")
        for label, expected in test_cases:
            result = _parse_importance(label)
            status = "✓" if abs(result - expected) < 0.01 else "✗"
            print(f"  {status} {label:10s} → {result:.2f} (期望 {expected:.2f})")
            assert abs(result - expected) < 0.01, f"{label} 评分错误"


# ─────────────────────────────────────────────
# 5. PetEngine 评估
# ─────────────────────────────────────────────

class TestPetEngineEffectiveness:
    """评估桌宠对话流程"""

    def test_context_building(self, tmp_path):
        """上下文构建是否包含有用信息"""
        import asyncio
        from modules.desktop_pet.pet_engine import PetEngine
        from modules.database.session_repo import SessionRepository
        import modules.database.connection as conn
        import threading

        # 设置临时数据库
        monkeypatch_db = type('MonkeyPatch', (), {
            'setattr': staticmethod(lambda obj, name, val: setattr(obj, name, val))
        })()

        db_path = str(tmp_path / "pet_test.db")
        conn.config.sqlite_path = db_path

        # 初始化数据库
        manager = conn.get_db_manager()
        manager.initialize()

        repo = SessionRepository()
        repo.create_session("pet_main")
        repo.save_message("pet_main", "user", "你好")
        repo.save_message("pet_main", "assistant", "你好！有什么可以帮你的吗？")

        pe = PetEngine(event_bus=None)

        # 测试上下文构建
        context = asyncio.run(pe._build_context("你好"))

        print(f"\n【PetEngine 上下文评估】")
        print(f"  构建的上下文:\n{context[:300]}...")

        # 验证：应该包含时间、用户身份等基础信息
        assert "时间" in context or "用户" in context, "应该包含基础上下文"

    def test_message_building(self):
        """消息构建是否正确"""
        from modules.desktop_pet.pet_engine import PetEngine

        pe = PetEngine(event_bus=None)
        messages = pe._build_messages("你好")

        print(f"\n【PetEngine 消息构建评估】")
        print(f"  消息数量: {len(messages)}")
        for i, msg in enumerate(messages):
            print(f"  [{i}] {msg.role}: {msg.content[:50]}...")

        # 验证：应该有system、history、user消息
        assert len(messages) >= 2, "应该至少有system和user消息"
        assert messages[0].role == "system", "第一条应该是system消息"


# ─────────────────────────────────────────────
# 6. 综合评估：功能 vs 实际价值
# ─────────────────────────────────────────────

class TestFeatureValueAssessment:
    """综合评估各功能的实际价值"""

    def test_attention_value_for_money(self):
        """注意力系统的投入产出比"""
        from modules.attention.analyzer import AttentionAnalyzer

        analyzer = AttentionAnalyzer.__new__(AttentionAnalyzer)
        analyzer._cfg = MagicMock(importance_enabled=True, force_static_level=None)

        # 测试10个不同场景
        scenarios = [
            "紧急故障", "帮我查文档", "今天天气", "系统崩溃",
            "部署上线", "代码review", "需求讨论", "会议记录",
            "bug修复", "性能优化"
        ]

        results = []
        for text in scenarios:
            r = analyzer.analyze(text)
            results.append({
                "input": text,
                "score": r.importance_score,
                "level": r.attention_level,
            })

        # 评估：分数分布是否合理
        scores = [r["score"] for r in results]
        score_range = max(scores) - min(scores)

        print(f"\n【Attention 投入产出评估】")
        print(f"  测试场景数: {len(scenarios)}")
        print(f"  分数范围: {score_range:.2f} ({min(scores):.2f} - {max(scores):.2f})")
        print(f"  平均分: {sum(scores)/len(scores):.2f}")

        # 如果分数范围太小，说明区分度不够
        if score_range < 0.3:
            print(f"  ⚠️  区分度不足，建议增加更多维度")
        else:
            print(f"  ✓ 区分度合理")

    def test_causal_memory_roi(self, tmp_path):
        """因果记忆的投入产出比"""
        from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
        from modules.memory.event_store import EventStore, MemoryEvent

        db_path = str(tmp_path / "test_roi.db")
        graph = CausalGraph(db_path=db_path)
        store = EventStore(db_path=db_path)

        # 模拟真实场景：创建10个因果节点和20个事件
        nodes = []
        for i in range(10):
            n = CausalNode(label=f"概念{i}", keywords=[f"关键词{i}"])
            graph.save_node(n)
            nodes.append(n)

        # 随机连接
        for i in range(15):
            graph.save_edge(CausalEdge(
                from_id=nodes[i % 10].id,
                to_id=nodes[(i + 3) % 10].id
            ))

        # 创建事件
        for i in range(20):
            ev = MemoryEvent(
                fact=f"事件{i}：关于概念{i%10}的内容",
                keywords=[f"关键词{i%10}"],
                causal_node_ids=[nodes[i % 10].id] if i % 2 == 0 else []
            )
            store.save_event(ev)

        # 评估：查询效率
        import time
        start = time.time()
        for query in ["概念0", "概念5", "关键词3"]:
            nodes_found = graph.find_anchor_nodes(query)
        query_time = time.time() - start

        print(f"\n【Causal Memory ROI 评估】")
        print(f"  节点数: {len(nodes)}")
        print(f"  事件数: 20")
        print(f"  查询耗时: {query_time*1000:.2f}ms (3次查询)")
        print(f"  平均每次: {query_time/3*1000:.2f}ms")

        # 验证：查询应该在合理时间内完成
        assert query_time < 1.0, "查询应该在1秒内完成"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
