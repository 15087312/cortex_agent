"""event_reducer 测试（此前 30% 覆盖）：记忆提炼解析"""
import json
from unittest.mock import AsyncMock, MagicMock

from modules.memory.event_reducer import EventReducer, _parse_importance


def test_parse_importance():
    assert _parse_importance("critical") > _parse_importance("medium")
    assert _parse_importance("medium") == 0.40
    assert _parse_importance("trivial") == 0.03
    assert _parse_importance(0.8) == 0.8
    assert _parse_importance(1.5) == 1.0  # clamp
    assert _parse_importance(None) == 0.40
    assert _parse_importance("未知") == 0.40


def _reducer():
    r = EventReducer.__new__(EventReducer)
    r._model_client = None
    return r


def test_parse_response_json():
    r = _reducer()
    text = json.dumps({
        "events": [{"fact": "完成重构", "thought": "用了新方案", "lesson": "复用", "keywords": ["重构"], "importance": "high", "type": "fact"}],
        "causal_nodes": [{"label": "重构"}],
        "causal_edges": [],
    })
    data = r._parse_response(text)
    assert len(data["events"]) == 1
    assert data["events"][0].fact == "完成重构"
    assert data["events"][0].importance > 0.5


def test_parse_response_markdown_and_list():
    r = _reducer()
    text = "```json\n" + json.dumps([{"fact": "旧格式"}]) + "\n```"
    data = r._parse_response(text)
    assert len(data["events"]) == 1
    assert data["causal_nodes"] == []


def test_parse_response_invalid():
    r = _reducer()
    data = r._parse_response("不是 json")
    assert data == {"events": [], "causal_nodes": [], "causal_edges": []}


def test_parse_events_list_skips_invalid():
    r = _reducer()
    events = r._parse_events_list([{"fact": "有效"}, {"type": "bad"}, "x", {"fact": "", "type": "emotion"}])
    assert len(events) == 1
    assert events[0].type == "fact"  # 未知类型回退


def test_reduce_short_skipped():
    r = _reducer()
    async def go():
        return await r.reduce("s1", "太短")
    import asyncio
    assert asyncio.run(go()) == []


def test_reduce_llm_success(monkeypatch):
    r = EventReducer.__new__(EventReducer)
    r._store = MagicMock()
    r._store.list_events.return_value = []
    r._store.save_event.return_value = "ev1"
    r._embedder = MagicMock()
    r._model_client = MagicMock()
    r._call_llm = AsyncMock(return_value={"events": r._parse_events_list([{"fact": "测试事件内容", "importance": "medium", "type": "fact"}]), "causal_nodes": [], "causal_edges": []})
    long_text = "这是一个足够长的对话内容，用来触发记忆提炼流程。" * 4
    async def go():
        return await r.reduce("s1", long_text, owner_id="large")
    import asyncio
    events = asyncio.run(go())
    assert len(events) == 1


def test_get_store_instance(monkeypatch):
    import modules.memory.event_reducer as mod
    fake = MagicMock()
    monkeypatch.setattr(mod.EventStore, "get_instance", staticmethod(lambda: fake))
    r = EventReducer()
    assert r._get_store() is fake
    assert r._get_store() is fake  # 二次调用返回缓存
