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


def test_extraction_writes_user_persona_memory_and_causal_nodes(bank):
    """EventReducer + 独立 EventStore/CausalGraph 串联：事件与因果节点落到该人设。"""
    import json

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
