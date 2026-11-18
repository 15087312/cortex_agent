"""PetMemoryBank：按人设隔离的记忆库 / 因果图存储。"""
import asyncio

import pytest

import modules.desktop_pet.memory_bank as mb
from modules.memory.causal_graph import CausalEdge, CausalNode


@pytest.fixture
def bank(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "_PET_MEMORY_ROOT", tmp_path)
    return mb.PetMemoryBank()


def test_empty_stats_and_persona_dir(bank, tmp_path):
    assert bank.stats(1) == {"memory_events": 0, "causal_nodes": 0, "causal_edges": 0}
    assert (tmp_path / "persona_1").is_dir()


def test_retrieve_empty_short_circuits(bank):
    assert asyncio.run(bank.retrieve(1, "你好")) == []


def test_causal_graph_isolated_per_persona(bank):
    g1 = bank.graph(1)
    a = g1.save_node(CausalNode(label="下雨", keywords=["雨"]))
    b = g1.save_node(CausalNode(label="地面湿", keywords=["湿"]))
    g1.save_edge(CausalEdge(from_id=a, to_id=b, relation="causes"))

    assert bank.stats(1)["causal_nodes"] == 2
    assert bank.stats(1)["causal_edges"] == 1
    assert bank.stats(2)["causal_nodes"] == 0
    assert bank.stats(2)["causal_edges"] == 0


def test_delete_persona_removes_files(bank, tmp_path):
    bank.store(7)
    bank.graph(7)
    assert (tmp_path / "persona_7").is_dir()
    bank.delete_persona(7)
    assert not (tmp_path / "persona_7").exists()
    assert bank.stats(7)["memory_events"] == 0  # 可重新懒创建


def test_get_instance_is_singleton(monkeypatch):
    monkeypatch.setattr(mb.PetMemoryBank, "_instance", None)
    first = mb.PetMemoryBank.get_instance()
    assert mb.PetMemoryBank.get_instance() is first


def test_store_graph_retrieval_are_cached_per_persona(bank):
    assert bank.store(3) is bank.store(3)
    assert bank.graph(3) is bank.graph(3)
    assert bank.retrieval(3) is bank.retrieval(3)
    assert bank.store(3) is not bank.store(4)


def test_retrieve_uses_retriever_with_owner_scope(bank, monkeypatch):
    class FakeRetriever:
        def __init__(self):
            self.calls = []

        async def retrieve(self, query, max_results, owner_id):
            self.calls.append((query, max_results, owner_id))
            return ["event"]

    class FakeStore:
        def _get_conn(self):
            class Conn:
                def execute(self, sql):
                    return self

                def fetchone(self):
                    return (1,)

            return Conn()

    fake = FakeRetriever()
    monkeypatch.setattr(bank, "store", lambda pid: FakeStore())
    monkeypatch.setattr(bank, "retrieval", lambda pid: fake)

    assert asyncio.run(bank.retrieve(2, "", max_results=0)) == ["event"]
    # 空 query 用默认语义，max_results 至少 1，owner 带人设前缀
    assert fake.calls == [("用户与桌宠互动", 1, "pet:2")]


def test_retrieve_swallows_backend_errors(bank, monkeypatch):
    class FakeStore:
        def _get_conn(self):
            raise RuntimeError("db gone")

    monkeypatch.setattr(bank, "store", lambda pid: FakeStore())
    assert asyncio.run(bank.retrieve(1, "你好")) == []


def test_causal_context_renders_one_hop_lines(bank, monkeypatch):
    class Node:
        def __init__(self, id_, label):
            self.id, self.label = id_, label

    class Edge:
        def __init__(self, relation="causes", confidence=0.8):
            self.relation, self.confidence = relation, confidence

    anchor = Node(1, "下雨")
    neighbour = Node(2, "地面湿")

    graph = type("G", (), {})()
    graph.list_nodes = lambda limit=None: [anchor]
    graph.find_anchor_nodes = lambda query, top_k=None: [(anchor, 0.9)]
    graph.get_neighbors = lambda node_id, hops=None: [(neighbour, Edge(), 1)]
    monkeypatch.setattr(bank, "graph", lambda pid: graph)

    lines = bank.causal_context(1, "下雨")
    assert lines == ["下雨 --causes--> 地面湿 (置信度 0.80)"]


def test_causal_context_empty_graph_and_no_anchor(bank, monkeypatch):
    graph = type("G", (), {})()
    graph.list_nodes = lambda limit=None: []
    monkeypatch.setattr(bank, "graph", lambda pid: graph)
    assert bank.causal_context(1, "q") == []

    graph.list_nodes = lambda limit=None: [object()]
    graph.find_anchor_nodes = lambda query, top_k=None: []
    assert bank.causal_context(1, "q") == []


def test_causal_context_dedupes_and_honours_limit(bank, monkeypatch):
    class Node:
        def __init__(self, id_, label):
            self.id, self.label = id_, label

    class Edge:
        relation = "causes"
        confidence = 0.5

    a, shared = Node(1, "A"), Node(2, "共享")
    extra = Node(3, "C")
    graph = type("G", (), {})()
    graph.list_nodes = lambda limit=None: [a]
    graph.find_anchor_nodes = lambda query, top_k=None: [(a, 1.0), (shared, 0.5)]
    graph.get_neighbors = lambda node_id, hops=None: [
        (shared, Edge(), 1),
        (extra, Edge(), 1),
        (shared, Edge(), 1),
    ]
    monkeypatch.setattr(bank, "graph", lambda pid: graph)

    lines = bank.causal_context(1, "q", limit=1)
    assert len(lines) == 1
    assert "共享" in lines[0]


def test_causal_context_swallows_errors(bank, monkeypatch):
    def boom(pid):
        raise RuntimeError("graph broken")

    monkeypatch.setattr(bank, "graph", boom)
    assert bank.causal_context(1, "q") == []


def test_schedule_extract_skips_incomplete_input(bank):
    async def run():
        bank.schedule_extract(1, "", "回复")
        bank.schedule_extract(1, "你好", "  ")
        bank.schedule_extract(1, "你好", "回复", model_client=None)
        return list(bank._pending)

    assert asyncio.run(run()) == []


def test_schedule_extract_outside_event_loop_is_noop(bank):
    bank.schedule_extract(1, "你好", "回复", model_client=object())
    assert bank._pending == set()


def test_schedule_extract_dedupes_per_persona(bank, monkeypatch):
    started = []

    async def fake_extract(pid, conversation, client):
        started.append((pid, conversation))
        await asyncio.sleep(0)

    monkeypatch.setattr(bank, "_extract", fake_extract)

    async def run():
        bank.schedule_extract(1, " 你好 ", " 回复 ", model_client=object())
        bank.schedule_extract(1, "再来", "一条", model_client=object())   # 同一人设：丢弃
        bank.schedule_extract(2, "你好", "回复", model_client=object())
        await asyncio.sleep(0.05)
        return list(started)

    started_calls = asyncio.run(run())
    assert [pid for pid, _ in started_calls] == [1, 2]
    assert started_calls[0][1] == "用户：你好\n桌宠：回复"
    assert bank._pending == set()          # 完成后释放，可再次提炼
    assert bank._tasks == set()


def test_extract_swallows_reducer_errors(bank, monkeypatch):
    class Boom:
        def __init__(self, *a, **kw):
            raise RuntimeError("reducer down")

    monkeypatch.setattr("modules.memory.event_reducer.EventReducer", Boom)
    asyncio.run(bank._extract(1, "用户：x\n桌宠：y", object()))   # 不抛异常

def test_extraction_writes_user_persona_memory_and_causal_nodes(bank, monkeypatch):
    """EventReducer + 独立 EventStore/CausalGraph 串联：事件与因果节点落到该人设。"""
    import json

    # 该用例验证注入 FakeClient 的落库链路；禁用本机用户配置里的专用记忆模型，
    # 否则单测会走真实 OpenRouter 请求。
    monkeypatch.setattr(
        "modules.memory.event_reducer.get_memory_model_client", lambda: None
    )

    class FakeClient:
        async def generate(self, prompt, **kwargs):
            return json.dumps({
                "events": [{
                    "fact": "用户喜欢在雨天喝热咖啡，桌宠记下了这个偏好。",
                    "thought": "这是一个稳定的生活习惯，可以在之后的对话中主动关心。",
                    "lesson": "记住用户的天气与饮品偏好。",
                    "keywords": ["雨天", "咖啡", "偏好"],
                    "importance": "high",
                    "type": "fact",
                }],
                "causal_nodes": [
                    {"label": "下雨", "node_type": "cause", "keywords": ["雨"]},
                    {"label": "想喝热咖啡", "node_type": "effect", "keywords": ["咖啡"]},
                ],
                "causal_edges": [
                    {"from_label": "下雨", "to_label": "想喝热咖啡", "relation": "causes"},
                ],
            })

    conversation = (
        "用户：今天又下雨了，我想喝点热的。\n"
        "桌宠：那来一杯热咖啡吧，记得你上次下雨天也喜欢喝咖啡。\n"
        "用户：对，雨天喝热咖啡很舒服。"
    )
    asyncio.run(bank._extract(1, conversation, FakeClient()))
    stats = bank.stats(1)
    assert stats["memory_events"] >= 1
    assert stats["causal_nodes"] >= 2
    assert stats["causal_edges"] >= 1
    events = bank.store(1).list_events(limit=10)
    assert events and events[0].causal_node_ids  # 事件已挂到因果节点（不是两套孤立数据）
    # 人设 2 完全隔离
    assert bank.stats(2)["memory_events"] == 0
