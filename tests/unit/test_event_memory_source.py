"""modules.thinking.context.sources.event_memory_source — 事件记忆 → prompt 片段"""
from types import SimpleNamespace

import pytest

from modules.thinking.context.sources import event_memory_source as src


class _FakeRetrieval:
    def __init__(self, events=None, exc=None):
        self._events = events or []
        self._exc = exc
        self.calls = []

    async def retrieve(self, query, max_results=3, threshold=0.10):
        self.calls.append({"query": query, "max_results": max_results, "threshold": threshold})
        if self._exc:
            raise self._exc
        return self._events


@pytest.fixture
def patch_retrieval(monkeypatch):
    def _install(fake):
        import modules.memory.event_retrieval as mod

        monkeypatch.setattr(mod, "get_event_retrieval", lambda: fake)
        return fake

    return _install


@pytest.mark.asyncio
async def test_no_events_returns_empty(patch_retrieval):
    fake = patch_retrieval(_FakeRetrieval([]))
    out = await src.collect_event_memory("用户喜欢什么")
    assert out == ""
    assert fake.calls == [{"query": "用户喜欢什么", "max_results": 3, "threshold": 0.10}]


@pytest.mark.asyncio
async def test_formats_events_with_index_and_date(patch_retrieval):
    events = [
        SimpleNamespace(time="2026-01-02T10:00:00", fact="用户偏好 Python"),
        SimpleNamespace(time="2026-02-03T10:00:00", fact="用户在北京"),
    ]
    patch_retrieval(_FakeRetrieval(events))
    out = await src.collect_event_memory("用户信息")
    lines = out.splitlines()
    assert lines[0] == "【曾经发生的事】"
    assert "不要把过去任务当作当前任务执行" in lines[1]
    assert lines[2] == "  [1] (日期=2026-01-02) 用户偏好 Python"
    assert lines[3] == "  [2] (日期=2026-02-03) 用户在北京"


@pytest.mark.asyncio
async def test_missing_or_empty_time_falls_back_to_unknown_date(patch_retrieval):
    events = [SimpleNamespace(fact="没有时间字段"), SimpleNamespace(time="", fact="空时间")]
    patch_retrieval(_FakeRetrieval(events))
    out = await src.collect_event_memory("x")
    assert "(日期=未知日期) 没有时间字段" in out
    assert "(日期=未知日期) 空时间" in out


@pytest.mark.asyncio
async def test_missing_fact_renders_empty_string(patch_retrieval):
    patch_retrieval(_FakeRetrieval([SimpleNamespace(time="2026-01-01")]))
    out = await src.collect_event_memory("x")
    assert "[1] (日期=2026-01-01) " in out


@pytest.mark.asyncio
async def test_long_fact_is_truncated_to_150_chars(patch_retrieval):
    patch_retrieval(_FakeRetrieval([SimpleNamespace(time="2026-01-01", fact="长" * 400)]))
    out = await src.collect_event_memory("x")
    assert "长" * 150 in out
    assert "长" * 151 not in out


@pytest.mark.asyncio
async def test_retrieval_exception_degrades_to_empty(patch_retrieval):
    patch_retrieval(_FakeRetrieval(exc=RuntimeError("memory subsystem down")))
    assert await src.collect_event_memory("x") == ""


@pytest.mark.asyncio
async def test_custom_max_results_and_threshold_passed_through(patch_retrieval):
    fake = patch_retrieval(_FakeRetrieval([]))
    await src.collect_event_memory("q", max_results=7, threshold=0.42)
    assert fake.calls[0]["max_results"] == 7
    assert fake.calls[0]["threshold"] == 0.42
