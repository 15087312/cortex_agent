"""Agent 模式联网搜索：结果只进本轮 system 上下文，不污染用户消息。"""
import pytest

from modules.thinking import api_stream
from modules.thinking.core import model_runner


@pytest.mark.asyncio
async def test_augment_agent_web_search_sets_extra_context(monkeypatch):
    sent = []

    async def fake_send(session_id, data):
        sent.append((session_id, data))

    async def fake_search(query, **kwargs):
        assert query == "测试查询"
        return {"results": [
            {"title": "结果一", "snippet": "摘要一", "url": "https://example.com/1"},
            {"title": "结果二", "snippet": "摘要二", "url": "https://example.com/2"},
        ]}

    monkeypatch.setattr(api_stream.connection_manager, "send_json", fake_send)
    monkeypatch.setattr("infra.tool_manager.tools.web_search.web_search", fake_search)
    model_runner._session_extra_context.pop("s1", None)
    try:
        await api_stream._augment_agent_web_search("s1", "测试查询")
        ctx = model_runner._session_extra_context["s1"]
        assert "结果一" in ctx and "https://example.com/2" in ctx
        assert sent and sent[0][0] == "s1"
        assert "联网搜索" in str(sent[0][1])
    finally:
        model_runner._session_extra_context.pop("s1", None)


@pytest.mark.asyncio
async def test_augment_agent_web_search_empty_results(monkeypatch):
    async def fake_send(session_id, data):
        return None

    async def fake_search(query, **kwargs):
        return {"results": []}

    monkeypatch.setattr(api_stream.connection_manager, "send_json", fake_send)
    monkeypatch.setattr("infra.tool_manager.tools.web_search.web_search", fake_search)
    model_runner._session_extra_context.pop("s2", None)
    try:
        await api_stream._augment_agent_web_search("s2", "无结果")
        assert "未找到" in model_runner._session_extra_context["s2"]
    finally:
        model_runner._session_extra_context.pop("s2", None)
