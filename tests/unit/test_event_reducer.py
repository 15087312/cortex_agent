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


def test_get_store_instance(tmp_path, monkeypatch):
    """真实 EventStore（临时库）注入 + 懒加载缓存"""
    from modules.memory.event_store import EventStore
    store = EventStore(
        db_path=str(tmp_path / "er.db"),
        faiss_index_path=str(tmp_path / "er.faiss"),
        id_map_path=str(tmp_path / "er_id.json"),
    )
    import modules.memory.event_reducer as mod
    monkeypatch.setattr(mod.EventStore, "get_instance", staticmethod(lambda: store))
    r = EventReducer()
    assert r._get_store() is store
    assert r._get_store() is store  # 二次调用返回缓存


def test_event_reducer_real_init():
    """EventReducer 真实构造（不 mock __init__）"""
    r = EventReducer()  # 真实 __init__
    assert r._model_client is None
    assert r._store is None
    assert r._embedder is None
    # set_model 兼容旧 API
    client = MagicMock()
    r.set_model(client)
    assert r._model_client is client


def test_reduce_uses_dedicated_system_prompt():
    """回归：记忆收纳必须用专用 system prompt，不能用主模型的 agent 人设。

    曾用各模型客户端默认 system prompt（orchestrator/code_writer/code_supervisor）
    与任务冲突；现通过 system_prompt 参数覆盖为 MEMORY_REDUCE_SYSTEM_PROMPT。
    """
    from modules.memory.event_reducer import (
        EventReducer, MEMORY_REDUCE_SYSTEM_PROMPT, REDUCE_PROMPT_TEMPLATE,
    )
    client = MagicMock()
    client.generate = AsyncMock(return_value=json.dumps({"events": []}))
    r = EventReducer(model_client=client)

    async def go():
        return await r._call_llm("这是一段超过五十个字的对话内容，用于测试记忆提炼的提示词构造是否正确。")

    import asyncio
    asyncio.run(go())

    kwargs = client.generate.call_args.kwargs
    assert kwargs.get("system_prompt") == MEMORY_REDUCE_SYSTEM_PROMPT
    assert kwargs.get("max_tokens") == 2048
    # user 提示词仍基于 REDUCE_PROMPT_TEMPLATE 组装
    assert kwargs.get("temperature") == 0.3
    prompt = client.generate.call_args.args[0]
    assert "你是一个记忆分析专家" in prompt
