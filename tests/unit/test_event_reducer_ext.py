"""event_reducer 扩展测试：reduce 全流程 / 因果图保存 / 降级摘要 / 单例"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.memory.event_reducer import (
    EventReducer,
    get_reducer,
    _reducer_instance,
    _IMPORTANCE_MAP,
)
from modules.memory.event_store import EventStore, MemoryEvent


def _long_text(n=20):
    return "这是一段用于测试记忆提炼的对话内容。" * n


@pytest.fixture
def store(tmp_path):
    return EventStore(
        db_path=str(tmp_path / "er.db"),
        faiss_index_path=str(tmp_path / "er.faiss"),
        id_map_path=str(tmp_path / "er.json"),
    )


async def test_reduce_happy_path_with_causal_graph(store, monkeypatch):
    client = MagicMock()
    client.generate = AsyncMock(return_value=json.dumps({
        "events": [
            {"fact": "完成了系统重构", "thought": "新架构更清晰", "lesson": "小步重构",
             "keywords": ["重构"], "importance": "high", "type": "strategy"},
            {"fact": "性能提升明显", "importance": "medium", "type": "fact"},
        ],
        "causal_nodes": [{"label": "系统重构", "node_type": "cause", "keywords": ["重构"]}],
        "causal_edges": [{"from_label": "系统重构", "to_label": "系统重构", "relation": "causes"}],
    }))
    r = EventReducer(model_client=client, store=store)
    saved = await r.reduce("s1", _long_text())
    assert len(saved) == 2
    assert saved[0].session_id == "s1"
    assert saved[0].owner_id == "large::large_primary"
    assert store.count_events() == 2


async def test_reduce_short_text_returns_empty():
    r = EventReducer(model_client=MagicMock())
    assert await r.reduce("s1", "太短") == []


async def test_reduce_llm_no_events(store):
    client = MagicMock()
    client.generate = AsyncMock(return_value=json.dumps({"events": [], "causal_nodes": [], "causal_edges": []}))
    r = EventReducer(model_client=client, store=store)
    assert await r.reduce("s1", _long_text()) == []


async def test_reduce_llm_no_client(store):
    r = EventReducer(model_client=None, store=store)
    assert await r.reduce("s1", _long_text()) == []


async def test_reduce_skips_duplicates(store):
    client = MagicMock()
    client.generate = AsyncMock(return_value=json.dumps({
        "events": [{"fact": "完全相同的记忆事件", "importance": "medium"}],
    }))
    r = EventReducer(model_client=client, store=store)
    first = await r.reduce("s1", _long_text())
    assert len(first) == 1
    second = await r.reduce("s2", _long_text())
    assert second == []


async def test_reduce_llm_call_failure(store):
    client = MagicMock()
    client.generate = AsyncMock(side_effect=RuntimeError("LLM 挂了"))
    r = EventReducer(model_client=client, store=store)
    assert await r.reduce("s1", _long_text()) == []


async def test_reduce_dedup_query_failure(store, monkeypatch):
    client = MagicMock()
    client.generate = AsyncMock(return_value=json.dumps({
        "events": [{"fact": "新事件", "importance": "low"}],
    }))
    r = EventReducer(model_client=client, store=store)
    monkeypatch.setattr(store, "list_events", MagicMock(side_effect=RuntimeError("boom")))
    saved = await r.reduce("s1", _long_text())
    assert len(saved) == 1  # 去重失败不阻塞保存


def test_call_llm_no_client():
    r = EventReducer(model_client=None)
    assert asyncio.run(r._call_llm("对话")) == {"events": [], "causal_nodes": [], "causal_edges": []}


def test_call_llm_success():
    client = MagicMock()
    client.generate = AsyncMock(return_value='{"events": []}')
    r = EventReducer(model_client=client)
    out = asyncio.run(r._call_llm("对话"))
    assert out == {"events": [], "causal_nodes": [], "causal_edges": []}


def test_parse_response_extract_json_from_text():
    r = EventReducer.__new__(EventReducer)
    data = r._parse_response("前缀文本" + json.dumps({"events": [{"fact": "A"}]}) + "后缀")
    assert len(data["events"]) == 1


def test_parse_response_list_format():
    r = EventReducer.__new__(EventReducer)
    data = r._parse_response(json.dumps([{"fact": "A", "type": "thought"}]))
    assert data["events"][0].type == "thought"


def test_parse_events_list_type_normalization():
    r = EventReducer.__new__(EventReducer)
    events = r._parse_events_list([
        {"fact": "X", "type": "EMOTION"},
        {"fact": "Y", "type": "unknown"},
        {"fact": "Z", "importance": 0.0},
    ])
    assert events[0].type == "emotion"
    assert events[1].type == "fact"
    assert events[2].importance == 0.0


def test_parse_importance_clamps():
    assert _IMPORTANCE_MAP["critical"] == 1.0


def test_save_causal_graph_nodes_and_edges(store, monkeypatch):
    from modules.memory.causal_graph import CausalGraph
    graph = CausalGraph(db_path=str(store._db_path).replace("er.db", "cg.db"))
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: graph))
    r = EventReducer(model_client=MagicMock(), store=store)
    r._save_causal_graph(
        [{"label": "性能问题", "node_type": "cause", "keywords": "性能"}],
        [{"from_label": "性能问题", "to_label": "性能问题", "relation": "causes"}],
    )
    assert len(graph.list_nodes()) == 1
    # 再次保存同 label → 提升置信度而非新增
    r._save_causal_graph(
        [{"label": "性能问题", "node_type": "cause", "keywords": ["性能"]}], [],
    )
    assert len(graph.list_nodes()) == 1


def test_save_causal_graph_creates_edge(store, monkeypatch):
    from modules.memory.causal_graph import CausalGraph
    graph = CausalGraph(db_path=str(store._db_path).replace("er.db", "cg2.db"))
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: graph))
    r = EventReducer(model_client=MagicMock(), store=store)
    r._save_causal_graph(
        [{"label": "原因", "node_type": "cause"}, {"label": "结果", "node_type": "effect"}],
        [{"from_label": "原因", "to_label": "结果", "relation": "causes"}],
    )
    assert graph.list_all_edges()


def test_save_causal_graph_invalid_relation():
    from modules.memory.causal_graph import CausalGraph
    r = EventReducer.__new__(EventReducer)
    r._save_causal_graph(
        [{"label": "原因"}, {"label": "结果"}],
        [{"from_label": "原因", "to_label": "结果", "relation": "bogus"}],
    )
    # 关系非法时回退 causes；节点和边仍被保存
    graph = CausalGraph.get_instance()
    edges = graph.list_all_edges()
    assert edges  # 边已创建（关系回退为 causes）


def test_save_causal_graph_short_label_skipped(store, monkeypatch):
    from modules.memory.causal_graph import CausalGraph
    graph = CausalGraph(db_path=str(store._db_path).replace("er.db", "cg3.db"))
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: graph))
    r = EventReducer(model_client=MagicMock(), store=store)
    r._save_causal_graph([{"label": "单"}], [])
    assert len(graph.list_nodes()) == 0


def test_fallback_summary_causal_sentences():
    r = EventReducer.__new__(EventReducer)
    events = r._fallback_summary("系统出现故障导致服务崩溃，随后我们优化了性能并修复了问题。")
    assert events
    assert all(isinstance(e, MemoryEvent) for e in events)
    types = {e.type for e in events}
    assert "strategy" in types or "fact" in types


def test_fallback_summary_truncated():
    r = EventReducer.__new__(EventReducer)
    events = r._fallback_summary("普通闲聊内容没有任何因果结构")
    assert len(events) == 1
    assert "对话摘要" in events[0].fact


def test_get_reducer_singleton(monkeypatch):
    import modules.memory.event_reducer as mod
    if _reducer_instance is not None:
        monkeypatch.setattr(mod, "_reducer_instance", None)
    first = get_reducer()
    second = get_reducer()
    assert first is second


def test_get_reducer_creates(monkeypatch):
    import modules.memory.event_reducer as mod
    monkeypatch.setattr(mod, "_reducer_instance", None)
    inst = get_reducer()
    assert inst is not None
    assert mod._reducer_instance is inst
