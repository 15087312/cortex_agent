"""event_query 工具测试：参数钳制 / 类型过滤 / 能力降级 / 异常路径"""
import json
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import infra.tool_manager.tools.event_query as eq
from infra.tool_manager.service_registry import (
    get_capability,
    register_capability,
    unregister_capability,
)


def _event(**kw):
    base = dict(
        type="fact", importance=0.5, time="2026-07-01T10:00:00",
        fact="发生了一件事", lesson="", keywords=["k"],
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def retrieval(monkeypatch):
    original = get_capability("event_retrieval")
    fake = AsyncMock()
    fake.retrieve.return_value = []
    register_capability("event_retrieval", lambda: fake)
    yield fake
    if original is None:
        unregister_capability("event_retrieval")
    else:
        register_capability("event_retrieval", original)


async def test_query_success_formats_events(retrieval):
    retrieval.retrieve.return_value = [
        _event(
            type="strategy", importance=0.9, time="2026-07-01T10:30:00",
            fact="做了 X", lesson="学到 Y", keywords=["x", "y"],
        ),
        _event(time="", lesson=""),
    ]
    out = json.loads(await eq.event_query(
        query="查什么", top_k="30", min_importance="0.4", types="fact,strategy",
    ))
    assert out["count"] == 2
    assert out["query"] == "查什么"
    first, second = out["events"]
    assert first["type"] == "strategy"
    assert first["importance"] == 0.9
    assert first["time"] == "2026-07-01T10:30"
    assert first["lesson"] == "学到 Y"
    assert first["keywords"] == ["x", "y"]
    assert second["time"] == "" and second["lesson"] == ""

    retrieval.retrieve.assert_awaited_once()
    kwargs = retrieval.retrieve.await_args.kwargs
    assert kwargs["query"] == "查什么"
    assert kwargs["max_results"] == 20
    assert kwargs["min_importance"] == 0.4
    assert kwargs["types"] == ["fact", "strategy"]
    assert kwargs["start_time"] == "" and kwargs["end_time"] == ""


async def test_query_clamps_top_k_and_importance(retrieval):
    await eq.event_query(query="q", top_k="0", min_importance="1.5")
    kwargs = retrieval.retrieve.await_args.kwargs
    assert kwargs["max_results"] == 1
    assert kwargs["min_importance"] == 1.0

    await eq.event_query(query="q", top_k="30", min_importance="-0.3")
    kwargs = retrieval.retrieve.await_args.kwargs
    assert kwargs["max_results"] == 20
    assert kwargs["min_importance"] == 0.0


async def test_query_empty_types_passes_none(retrieval):
    await eq.event_query(query="q", types="", start_time="  2026-07-01  ")
    kwargs = retrieval.retrieve.await_args.kwargs
    assert kwargs["types"] is None
    assert kwargs["start_time"] == "2026-07-01"


async def test_query_capability_missing():
    original = get_capability("event_retrieval")
    unregister_capability("event_retrieval")
    try:
        out = json.loads(await eq.event_query(query="q"))
        assert out["error"] == "事件检索能力未注册"
        assert out["events"] == []
    finally:
        if original is None:
            unregister_capability("event_retrieval")
        else:
            register_capability("event_retrieval", original)


async def test_query_exception_returns_error_json(retrieval):
    retrieval.retrieve.side_effect = RuntimeError("检索失败 boom")
    out = json.loads(await eq.event_query(query="q"))
    assert "boom" in out["error"]
    assert out["count"] == 0
    assert out["events"] == []
