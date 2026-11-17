"""event_reducer 补测：因果-only / 共现异常 / JSON 边界 / 降级摘要分支 / 默认注入"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from config.settings import settings
from modules.memory.event_reducer import (
    EventReducer,
    get_memory_model_client,
    get_reducer,
    reset_memory_model_client,
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


def test_get_memory_model_client_uses_dedicated_settings(monkeypatch):
    """记忆提取模型必须与主聊天模型解耦，避免主供应商波动影响记忆写入。"""
    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.max_tokens = 0
            self._provider = SimpleNamespace(openrouter_supplier="main-supplier")

    monkeypatch.setattr("infra.model.large_model_client.LargeModelClient", FakeClient)
    monkeypatch.setattr(settings, "MEMORY_MODEL_API_KEY", "mem-key", raising=False)
    monkeypatch.setattr(settings, "MEMORY_MODEL_API_URL", "https://openrouter.ai/api/v1/chat/completions", raising=False)
    monkeypatch.setattr(settings, "MEMORY_MODEL_NAME", "dots-studio/dots-3-note-preview:free", raising=False)
    monkeypatch.setattr(settings, "MEMORY_MODEL_API_FORMAT", "", raising=False)
    monkeypatch.setattr(settings, "MEMORY_MODEL_PROVIDER", "", raising=False)
    monkeypatch.setattr(settings, "MEMORY_MODEL_OPENROUTER_SUPPLIER", "", raising=False)
    monkeypatch.setattr(settings, "MEMORY_MODEL_MAX_TOKENS", 8192, raising=False)
    monkeypatch.setattr(settings, "MEMORY_MODEL_TIMEOUT", 180, raising=False)
    reset_memory_model_client()

    client = get_memory_model_client()
    assert client is not None
    assert client.kwargs["api_key"] == "mem-key"
    assert client.kwargs["model_name"] == "dots-studio/dots-3-note-preview:free"
    assert client.kwargs["api_provider"] == "openrouter"
    assert client.kwargs["enable_thinking"] is False
    assert client.max_tokens == 8192
    assert client._provider.openrouter_supplier == ""

    reset_memory_model_client()


def test_get_memory_model_client_none_when_unconfigured(monkeypatch):
    for key in (
        "MEMORY_MODEL_API_KEY", "MEMORY_MODEL_API_URL", "MEMORY_MODEL_NAME",
        "VISION_API_KEY", "VISION_API_URL",
    ):
        monkeypatch.setattr(settings, key, "", raising=False)
    reset_memory_model_client()
    assert get_memory_model_client() is None
    reset_memory_model_client()


# ── reduce：仅有因果节点 / 共现失败 ───────────────────────────────────

async def test_reduce_causal_only(store, monkeypatch):
    from modules.memory.causal_graph import CausalGraph
    graph = CausalGraph(db_path=str(store._db_path).replace("er.db", "cgx.db"))
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: graph))
    client = MagicMock()
    client.generate = AsyncMock(return_value=json.dumps({
        "events": [], "causal_nodes": [{"label": "根因"}], "causal_edges": [],
    }))
    r = EventReducer(model_client=client, store=store)
    assert await r.reduce("s1", _long_text()) == []  # 保存因果图后无事件 → []
    assert len(graph.list_nodes()) == 1


async def test_reduce_cooccurrence_failure(store, monkeypatch):
    from modules.memory.causal_graph import CausalGraph
    monkeypatch.setattr(CausalGraph, "get_instance",
                        staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("graph down"))))
    client = MagicMock()
    client.generate = AsyncMock(return_value=json.dumps({
        "events": [{"fact": "一个普通的记忆事件内容", "importance": "medium"}],
    }))
    r = EventReducer(model_client=client, store=store)
    saved = await r.reduce("s1", _long_text())
    assert len(saved) == 1  # 共现统计失败静默


# ── _parse_response：markdown 边界 / 坏 JSON ──────────────────────────

def test_parse_response_open_fence_only():
    r = EventReducer.__new__(EventReducer)
    data = r._parse_response("```json\n" + json.dumps({"events": [{"fact": "A"}]}))
    assert len(data["events"]) == 1


def test_parse_response_invalid_json_with_braces():
    r = EventReducer.__new__(EventReducer)
    data = r._parse_response("前缀 {not valid json} 后缀")
    assert data == {"events": [], "causal_nodes": [], "causal_edges": []}


# ── _save_causal_graph 防御分支 ───────────────────────────────────────

def test_save_causal_graph_empty(store):
    r = EventReducer(model_client=MagicMock(), store=store)
    r._save_causal_graph([], [])  # 直接返回


def test_save_causal_graph_invalid_node_type(store, monkeypatch):
    from modules.memory.causal_graph import CausalGraph
    graph = CausalGraph(db_path=str(store._db_path).replace("er.db", "cgnt.db"))
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: graph))
    r = EventReducer(model_client=MagicMock(), store=store)
    r._save_causal_graph([{"label": "奇怪类型", "node_type": "weird"}], [])
    node = graph.list_nodes()[0]
    assert node.node_type == "cause"


def test_save_causal_graph_exception(store, monkeypatch):
    from modules.memory.causal_graph import CausalGraph
    monkeypatch.setattr(CausalGraph, "get_instance",
                        staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("graph down"))))
    r = EventReducer(model_client=MagicMock(), store=store)
    r._save_causal_graph([{"label": "节点"}], [])  # 静默降级


# ── _fallback_summary：问题类 / 兜底 else ─────────────────────────────

def test_fallback_summary_problem_and_plain():
    r = EventReducer.__new__(EventReducer)
    events = r._fallback_summary("排查了网络连接的问题并重启了服务。重复的重试机制导致系统恢复完成。")
    assert events
    facts = {e.fact for e in events}
    assert any("问题并重启" in f for f in facts)  # 367-369：fact/0.6
    assert any("导致" in f for f in facts)         # 370-372：else → fact/0.5


# ── 默认注入 ──────────────────────────────────────────────────────────

def test_get_embedder_default(monkeypatch, take_over):
    fake = MagicMock()
    take_over("embedding_engine", lambda: fake)
    r = EventReducer()
    assert r._get_embedder() is fake


def test_get_reducer_inner_check(monkeypatch):
    import modules.memory.event_reducer as mod
    monkeypatch.setattr(mod, "_reducer_instance", None)
    fake = EventReducer(model_client=MagicMock())

    class RacingLock:
        def __enter__(self):
            mod._reducer_instance = fake
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod, "_reducer_lock", RacingLock())
    assert get_reducer() is fake
