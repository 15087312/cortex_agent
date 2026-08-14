"""blackboard_history 测试（此前 22% 覆盖）：黑板协作历史查询工具

经能力端口 blackboard_query 注入 fake，验证 JSON 字符串返回值与参数解析。
"""
import json
from unittest.mock import MagicMock

import pytest

from infra.tool_manager.service_registry import (
    get_capability,
    register_capability,
    unregister_capability,
)
from infra.tool_manager.tools import blackboard_history as bh


@pytest.fixture
def query_cap():
    prev = get_capability("blackboard_query")
    q = MagicMock()
    register_capability("blackboard_query", lambda: q)
    yield q
    if prev is None:
        unregister_capability("blackboard_query")
    else:
        register_capability("blackboard_query", prev)


def _row(created_at="2026-07-01T10:20:30.123456", session_id="sess-0123456789012345",
         tier="supervisor", content="委托引导"):
    return {"created_at": created_at, "tier": tier, "content": content, "session_id": session_id}


async def test_factory_missing():
    prev = get_capability("blackboard_query")
    unregister_capability("blackboard_query")
    try:
        r = json.loads(await bh.blackboard_history())
    finally:
        if prev is not None:
            register_capability("blackboard_query", prev)
    assert r["observations"] == []
    assert r["count"] == 0
    assert "未注册" in r["error"]


async def test_success_with_rows(query_cap):
    query_cap.return_value = [_row()]
    r = json.loads(await bh.blackboard_history(query="委托", session_id="sess-1", tier="supervisor"))
    assert r["count"] == 1
    obs = r["observations"][0]
    assert obs["time"] == "2026-07-01T10:20"
    assert obs["session_id"] == "sess-0123456"
    assert obs["tier"] == "supervisor"
    assert obs["content"] == "委托引导"
    assert r["query"] == "委托"
    query_cap.assert_called_once_with(
        session_id="sess-1", query="委托", start="", end="", limit=10, tier="supervisor"
    )


async def test_empty_rows(query_cap):
    query_cap.return_value = []
    r = json.loads(await bh.blackboard_history(query="nothing"))
    assert r == {"observations": [], "count": 0, "query": "nothing"}


async def test_created_at_none(query_cap):
    query_cap.return_value = [_row(created_at=None)]
    r = json.loads(await bh.blackboard_history())
    assert r["observations"][0]["time"] == ""


async def test_limit_clamped(query_cap):
    query_cap.return_value = []
    await bh.blackboard_history(limit="0")
    assert query_cap.call_args.kwargs["limit"] == 1
    await bh.blackboard_history(limit="100")
    assert query_cap.call_args.kwargs["limit"] == 50
    await bh.blackboard_history(limit="25")
    assert query_cap.call_args.kwargs["limit"] == 25


async def test_time_range_parse(query_cap):
    query_cap.return_value = []
    await bh.blackboard_history(time_range="2026-07-01~2026-07-31")
    assert query_cap.call_args.kwargs["start"] == "2026-07-01"
    assert query_cap.call_args.kwargs["end"] == "2026-07-31"


async def test_time_range_partial_boundaries(query_cap):
    query_cap.return_value = []
    await bh.blackboard_history(time_range="~2026-07-31")
    assert query_cap.call_args.kwargs["start"] == ""
    assert query_cap.call_args.kwargs["end"] == "2026-07-31"
    await bh.blackboard_history(time_range="2026-07-01~")
    assert query_cap.call_args.kwargs["start"] == "2026-07-01"
    assert query_cap.call_args.kwargs["end"] == ""


async def test_time_range_no_tilde(query_cap):
    query_cap.return_value = []
    await bh.blackboard_history(time_range="no-tilde")
    assert query_cap.call_args.kwargs["start"] == ""
    assert query_cap.call_args.kwargs["end"] == ""


async def test_args_stripped(query_cap):
    query_cap.return_value = []
    await bh.blackboard_history(query=" 委托 ", session_id=" s1 ", tier=" expert ")
    assert query_cap.call_args.kwargs["query"] == "委托"
    assert query_cap.call_args.kwargs["session_id"] == "s1"
    assert query_cap.call_args.kwargs["tier"] == "expert"


async def test_invalid_limit(query_cap):
    r = json.loads(await bh.blackboard_history(limit="abc"))
    assert "error" in r
    assert r["observations"] == []
    assert r["count"] == 0


async def test_query_exception(query_cap):
    query_cap.side_effect = RuntimeError("db down")
    r = json.loads(await bh.blackboard_history())
    assert r["error"] == "db down"
    assert r["observations"] == []
    assert r["count"] == 0
