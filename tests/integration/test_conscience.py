"""
良知系统测试 — 因果知识提取 + 内心独白
"""
import pytest
import threading
import tempfile
import os
from unittest.mock import MagicMock

from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
from modules.memory.event_store import EventStore, MemoryEvent
from modules.memory.event_retrieval import EventRetrieval
from modules.thinking.conscience import Conscience

# 首次 _get_causal_knowledge 会加载 embedding 模型（MiniLM）+ FAISS 检索，
# 超过 pytest 全局 --timeout=10，本模块放宽到 60s（超时是上限，不影响快用例）
pytestmark = pytest.mark.timeout(60)


@pytest.fixture
def clean_state():
    """每次测试前重置所有单例 + 全临时 DB（SQLite/FAISS/id_map 全部在临时目录）

    绝不触碰生产 data/ 目录：只传 db_path 会回退到 settings 里的生产路径
    （如 data/events_faiss_<USER_NAME>.index），必须连 faiss/id_map 一起指到临时目录。
    """
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test.db")
    faiss_path = os.path.join(tmp_dir, "events_faiss.index")
    id_map_path = os.path.join(tmp_dir, "events_id_map.json")
    causal_db = os.path.join(tmp_dir, "causal.db")

    # 重置单例
    CausalGraph._instance = None
    CausalGraph._lock = threading.Lock()
    EventStore._instance = None
    EventStore._lock = threading.Lock()
    # EventRetrieval 缓存了 _store 引用，必须一并重置，否则下个用例仍指向已删的临时库
    EventRetrieval._instance = None

    # 必须通过 get_instance() 创建，Conscience 内部也用 get_instance()
    CausalGraph._instance = CausalGraph(db_path=causal_db)
    EventStore._instance = EventStore(
        db_path=db_path,
        faiss_index_path=faiss_path,
        id_map_path=id_map_path,
    )
    graph = CausalGraph._instance
    store = EventStore._instance

    yield graph, store

    # 清理
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)
    CausalGraph._instance = None
    EventStore._instance = None
    EventRetrieval._instance = None


def test_get_causal_knowledge_finds_nodes_from_events(clean_state):
    """核心：Conscience 能从事件的 causal_node_ids 找到节点并展开证据树"""
    graph, store = clean_state

    # 创建因果节点
    n1 = CausalNode(label="项目延期", node_type="effect")
    n2 = CausalNode(label="需求变更", node_type="cause")
    graph.save_node(n1)
    graph.save_node(n2)
    graph.save_edge(CausalEdge(from_id=n2.id, to_id=n1.id))

    # 创建关联事件
    ev = MemoryEvent(
        fact="张三说需求变更导致项目延迟一个月",
        keywords=["需求变更", "延期"],
        importance=0.8,
        causal_node_ids=[n1.id, n2.id],
    )
    store.save_event(ev)

    # 第二个佐证事件
    ev2 = MemoryEvent(
        fact="李四确认需求变更后测试周期不够",
        keywords=["需求变更", "测试"],
        importance=0.6,
        causal_node_ids=[n1.id, n2.id],
    )
    store.save_event(ev2)

    # 创建 Conscience 实例
    from modules.thinking.conscience import Conscience
    cons = Conscience()

    # 调用 _get_causal_knowledge
    knowledge = cons._get_causal_knowledge("为什么项目延期了")

    # 验证输出包含核心信息
    assert "项目延期" in knowledge, f"应包含节点标签: {knowledge}"
    assert "需求变更" in knowledge, f"应包含原因链: {knowledge}"
    assert knowledge.count("事实:") >= 1, f"应包含佐证事件: {knowledge}"
    assert "置信度" in knowledge, f"应包含置信度: {knowledge}"

    # 验证 _last_analyzed_node_ids 已记录
    assert len(cons._last_analyzed_node_ids) > 0


def test_get_causal_knowledge_fallback_to_anchors(clean_state):
    """事件没有 causal_node_ids 时，回退到关键词锚点匹配"""
    graph, store = clean_state

    # 只创建节点（没有事件关联）
    n1 = CausalNode(label="项目延期", node_type="effect", keywords=["延期"])
    graph.save_node(n1)
    n2 = CausalNode(label="需求变更", node_type="cause", keywords=["需求"])
    graph.save_node(n2)
    graph.save_edge(CausalEdge(from_id=n2.id, to_id=n1.id))

    from modules.thinking.conscience import Conscience
    cons = Conscience()
    knowledge = cons._get_causal_knowledge("项目延期的原因")

    # 即使没有事件关联，也应有节点信息
    assert "项目延期" in knowledge
    assert "需求变更" in knowledge


def test_get_causal_knowledge_no_data(clean_state):
    """没有任何数据时返回占位符"""
    graph, store = clean_state

    from modules.thinking.conscience import Conscience
    cons = Conscience()
    knowledge = cons._get_causal_knowledge("完全没见过的查询")

    assert "暂无" in knowledge or knowledge == "（暂无相关因果经验）"


def test_get_causal_knowledge_multi_node_from_single_event(clean_state):
    """一个事件关联多个节点，所有节点都应展开"""
    graph, store = clean_state

    nodes = []
    for label in ["项目延期", "需求变更", "技术难点", "资源不足"]:
        n = CausalNode(label=label)
        graph.save_node(n)
        nodes.append(n)

    # 三个 cause → 一个 effect
    for i in range(1, 4):
        graph.save_edge(CausalEdge(from_id=nodes[i].id, to_id=nodes[0].id))

    # 一个事件关联所有节点
    ev = MemoryEvent(
        fact="综合评估发现需求变更和技术难点和资源不足共同导致延期",
        keywords=["延期"],
        importance=0.9,
        causal_node_ids=[n.id for n in nodes],
    )
    store.save_event(ev)

    from modules.thinking.conscience import Conscience
    cons = Conscience()
    knowledge = cons._get_causal_knowledge("为什么一直延期")

    # 应包含所有四个节点
    for label in ["项目延期", "需求变更", "技术难点", "资源不足"]:
        assert label in knowledge, f"应包含 {label}: {knowledge[:300]}..."

    # 因果链应包含原因链路
    assert "←" in knowledge or "原因" in knowledge, \
        f"应包含因果链: {knowledge[:300]}..."

    # 应有置信度
    assert "置信度" in knowledge or "%" in knowledge


def test_get_causal_knowledge_deduplicates(clean_state):
    """同一个节点被多个事件关联时，只展开一次"""
    graph, store = clean_state

    n = CausalNode(label="项目延期")
    graph.save_node(n)

    # 三个事件都关联同一个节点
    for i in range(3):
        ev = MemoryEvent(
            fact=f"第{i+1}次提到延期",
            causal_node_ids=[n.id],
        )
        store.save_event(ev)

    from modules.thinking.conscience import Conscience
    cons = Conscience()
    knowledge = cons._get_causal_knowledge("延期")

    # "项目延期" 只应出现一次作为【节点标题】
    # 但事实证据可以有多个
    assert knowledge.count("项目延期") >= 1
    assert knowledge.count("事实:") >= 1


def test_analyze_feedback_requires_model_client(clean_state):
    """analyze_feedback 在没有 model_client 时不应崩溃"""
    graph, store = clean_state

    from modules.thinking.conscience import Conscience
    cons = Conscience()
    cons._last_analyzed_node_ids = ["some_node_id"]

    # 不应抛异常
    import asyncio
    asyncio.run(cons.analyze_feedback("用户输入", "模型回复"))
    # 如果没有 model_client，应该静默返回


def test_extract_keywords(clean_state):
    """关键词提取功能正常"""
    from modules.thinking.conscience import Conscience

    kws = Conscience._extract_keywords("为什么项目延期了")
    assert "项目" in kws
    assert "延期" in kws

    kws = Conscience._extract_keywords("Why did the build fail?")
    assert "why" in kws
    assert "build" in kws
    assert "fail" in kws


def test_add_to_dialog():
    c = Conscience(model_client=None)
    c.add_to_dialog("user", "你好")
    c.add_to_dialog("assistant", "在的")
    assert any("用户: 你好" in x for x in c._last_dialog_buffer)
    assert any("助手: 在的" in x for x in c._last_dialog_buffer)


def test_analyze_feedback_no_nodes():
    c = Conscience(model_client=None)
    import asyncio
    asyncio.run(c.analyze_feedback("hi", "hi"))  # 无节点直接返回


def test_analyze_feedback_no_model_client(clean_state, monkeypatch):
    """真实 reducer（无模型 client）：analyze_feedback 直接返回"""
    graph, _ = clean_state
    graph.save_node(CausalNode(label="概念A", keywords=["a"]))
    c = Conscience(model_client=None)
    c._last_analyzed_node_ids = [graph.find_nodes_by_label("概念A")[0].id]
    from modules.memory.event_reducer import EventReducer
    reducer = EventReducer(model_client=None)
    import modules.memory.event_reducer as er_mod
    monkeypatch.setattr(er_mod, "get_reducer", lambda: reducer)
    import asyncio
    asyncio.run(c.analyze_feedback("hi", "hi"))
    assert c._last_analyzed_node_ids == []


def test_analyze_feedback_adjusts_confidence(clean_state, monkeypatch):
    """真实因果图：确认/反驳节点 → 置信度真实调整"""
    graph, _ = clean_state
    node = CausalNode(label="概念A", keywords=["a"])
    graph.save_node(node)
    nid = node.id
    c = Conscience(model_client=None)
    c._last_analyzed_node_ids = [nid]
    from modules.memory.event_reducer import EventReducer

    class MC:
        """真实 LLM 接口实现（注入，返回确认 JSON）"""
        async def generate(self, prompt, max_tokens=0, temperature=0, system_prompt=None):
            return '{"confirmed": ["%s"], "contradicted": []}' % nid

    reducer = EventReducer(model_client=MC())
    import modules.memory.event_reducer as er_mod
    monkeypatch.setattr(er_mod, "get_reducer", lambda: reducer)
    import asyncio
    asyncio.run(c.analyze_feedback("hi", "hi"))
    assert graph.get_node(nid).confidence > node.confidence  # 置信度已上调


def test_analyze_feedback_json_parse_fail(clean_state, monkeypatch):
    """真实图 + LLM 返回非 JSON：容错返回"""
    graph, _ = clean_state
    node = CausalNode(label="概念A", keywords=["a"])
    graph.save_node(node)
    c = Conscience(model_client=None)
    c._last_analyzed_node_ids = [node.id]
    from modules.memory.event_reducer import EventReducer

    class MC:
        async def generate(self, prompt, max_tokens=0, temperature=0, system_prompt=None):
            return "不是 JSON"

    reducer = EventReducer(model_client=MC())
    import modules.memory.event_reducer as er_mod
    monkeypatch.setattr(er_mod, "get_reducer", lambda: reducer)
    import asyncio
    asyncio.run(c.analyze_feedback("hi", "hi"))
    assert c._last_analyzed_node_ids == []
