"""tool_manager 测试：JSON 提取 / MCP 路由 / 事件记录与统计 / 工具发现"""
import sys
import time
import json as _json
import pytest
from unittest.mock import MagicMock

from infra.tool_manager.tool_manager import ToolManager, extract_json, _get_blackbox
from infra.tool_manager.tool_registry import ToolRegistry, ToolInfo
from infra.mcp.types import ToolCallResult, ToolSpec

TM_MOD = "infra.tool_manager.tool_manager"


@pytest.fixture
def manager():
    return ToolManager()


@pytest.fixture
def mcp_service(manager, monkeypatch):
    svc = MagicMock()
    svc.execute.return_value = ToolCallResult(success=True, result="ok", latency_ms=0.5)
    svc.list_tools.return_value = {}
    svc.get_tool.return_value = None
    monkeypatch.setattr(ToolManager, "_get_mcp_service", lambda self: svc)
    return svc


# ── extract_json ────────────────────────────────────────────────────────────

def test_extract_json_empty():
    assert extract_json("") == {"tool": "none", "params": {}}


def test_extract_json_none_input():
    assert extract_json(None) == {"tool": "none", "params": {}}


def test_extract_json_simple_object():
    out = extract_json('{"tool": "add", "params": {"a": 1}}')
    assert out == {"tool": "add", "params": {"a": 1}}


def test_extract_json_with_prefix_text():
    out = extract_json('思考过程… {"tool": "add", "params": {}} 结束')
    assert out["tool"] == "add"


def test_extract_json_nested():
    out = extract_json('{"params": {"a": {"b": [1, 2]}}}')
    assert out["params"]["a"]["b"] == [1, 2]


def test_extract_json_array():
    assert extract_json('[1, 2]') == [1, 2]


def test_extract_json_no_braces():
    assert extract_json("纯文本，没有 JSON") == {"tool": "none", "params": {}}


def test_extract_json_broken_braces():
    assert extract_json("前 { 后") == {"tool": "none", "params": {}}


def test_extract_json_regex_fallback_success(monkeypatch):
    """raw_decode 失败时回退到正则提取（raw_decode 已覆盖一般情况，回退为纵深防御）。"""
    tm_mod = sys.modules[TM_MOD]

    class _BrokenDecoder(_json.JSONDecoder):
        def raw_decode(self, s, *a, **k):
            raise _json.JSONDecodeError("forced", s, 0)

    monkeypatch.setattr(_json, "JSONDecoder", _BrokenDecoder)
    out = tm_mod.extract_json('text {"a": 1} tail')
    assert out == {"a": 1}


def test_extract_json_regex_fallback_fail(monkeypatch):
    """回退正则也解析失败 → none。"""
    tm_mod = sys.modules[TM_MOD]

    class _BrokenDecoder(_json.JSONDecoder):
        def raw_decode(self, s, *a, **k):
            raise _json.JSONDecodeError("forced", s, 0)

    monkeypatch.setattr(_json, "JSONDecoder", _BrokenDecoder)
    out = tm_mod.extract_json('text {bad json} tail')
    assert out == {"tool": "none", "params": {}}


# ── _get_blackbox ───────────────────────────────────────────────────────────

def test_get_blackbox_returns_none():
    assert _get_blackbox() is None


def test_get_blackbox_returns_cached(monkeypatch):
    tm_mod = sys.modules[TM_MOD]
    sentinel = object()
    monkeypatch.setattr(tm_mod, "_blackbox", sentinel)
    assert tm_mod._get_blackbox() is sentinel


# ── 生命周期 / MCP 服务 ─────────────────────────────────────────────────────

def test_init_state(manager):
    assert manager._mcp_service is None
    assert manager._tool_events is not None


def test_use_mcp_for_lookup_is_true():
    assert ToolManager()._use_mcp_for_lookup() is True


def test_list_available_tools_without_mcp(manager, mcp_service, monkeypatch):
    monkeypatch.setattr(ToolRegistry, "list_tools", classmethod(
        lambda cls, source=None: {"local": {"source": "builtin"}}
    ))
    manager._use_mcp_for_lookup = lambda: False
    assert manager.list_available_tools() == {"local": {"source": "builtin"}}
    mcp_service.list_tools.assert_not_called()


def test_list_by_source_without_mcp(manager, mcp_service, monkeypatch):
    monkeypatch.setattr(ToolRegistry, "list_by_source", classmethod(
        lambda cls: {"builtin": ["a"], "plugin": [], "dynamic": []}
    ))
    manager._use_mcp_for_lookup = lambda: False
    assert manager.list_by_source() == {"builtin": ["a"], "plugin": [], "dynamic": []}
    mcp_service.list_tools.assert_not_called()


def test_get_mcp_service_lazy_caches(manager, monkeypatch):
    import infra.mcp.factory as mcp_factory
    fake = MagicMock()
    monkeypatch.setattr(mcp_factory, "get_mcp_tool_service", lambda: fake)
    assert manager._get_mcp_service() is fake
    assert manager._mcp_service is fake
    assert manager._get_mcp_service() is fake


# ── MCP 路由执行 ────────────────────────────────────────────────────────────

def test_call_mcp_sync_routes_and_records(manager, mcp_service):
    result = ToolCallResult(success=True, result="输出", error=None, latency_ms=1.5, tool_name="echo")
    mcp_service.execute.return_value = result
    out = manager._call_mcp_sync("echo", {"x": 1}, caller_role="expert", source="sync", timeout=10)
    assert out == result.to_legacy_dict()

    req = mcp_service.execute.call_args.args[0]
    assert req.tool_name == "echo"
    assert req.params == {"x": 1}
    assert req.timeout == 10
    assert req.source == "sync"

    events = manager.get_tool_events()
    assert events[-1]["tool"] == "echo"
    assert events[-1]["success"] is True
    assert events[-1]["result_preview"] == "输出"
    assert events[-1]["source"] == "sync"


def test_call_mcp_sync_records_failure(manager, mcp_service):
    mcp_service.execute.return_value = ToolCallResult(success=False, error="err", latency_ms=0.1)
    manager._call_mcp_sync("fail", {}, caller_role="expert")
    ev = manager.get_tool_events()[-1]
    assert ev["success"] is False
    assert ev["error"] == "err"


async def test_call_tool_async_delegates(manager, mcp_service):
    mcp_service.execute.return_value = ToolCallResult(success=True, result="r")
    out = await manager.call_tool("x", {"a": 1}, caller_role="supervisor", caller_model_id="m1")
    assert out["success"] is True
    req = mcp_service.execute.call_args.args[0]
    assert req.source == "async"
    assert req.caller_model_id == "m1"


def test_call_tool_sync_delegates(manager, mcp_service):
    mcp_service.execute.return_value = ToolCallResult(success=True, result="r")
    out = manager.call_tool_sync("x", {"a": 1}, caller_role="expert")
    assert out["result"] == "r"
    req = mcp_service.execute.call_args.args[0]
    assert req.source == "sync"


# ── call_from_json ──────────────────────────────────────────────────────────

def test_call_from_json_routes(manager, monkeypatch):
    fake = MagicMock(return_value={"success": True, "result": "ok", "error": None})
    monkeypatch.setattr(manager, "call_tool_sync", fake)
    out = manager.call_from_json('{"tool": "add", "params": {"a": 1}}', caller_role="expert")
    assert out["tool"] == "add"
    assert out["source"] == "json"
    fake.assert_called_once_with("add", {"a": 1}, caller_role="expert")


def test_call_from_json_no_tool(manager):
    out = manager.call_from_json("no tool here")
    assert out == {"success": True, "tool": "none", "result": None, "error": None}


# ── 事件历史 ────────────────────────────────────────────────────────────────

def test_get_tool_events_filters(manager):
    manager._record_tool_event("a", {}, True, result="r1")
    manager._record_tool_event("b", {}, False, error="e")
    manager._record_tool_event("a", {}, True, result="r2")

    assert [e["tool"] for e in manager.get_tool_events(tool_name="a")] == ["a", "a"]
    assert len(manager.get_tool_events(success=True)) == 2
    assert len(manager.get_tool_events(success=False)) == 1
    assert len(manager.get_tool_events(limit=1)) == 1
    assert manager.get_tool_events(limit=0) == list(manager._tool_events)
    assert len(manager.get_tool_events(since=time.time() + 10000)) == 0


def test_get_tool_event_stats(manager):
    manager._record_tool_event("a", {}, True, result="r")
    manager._record_tool_event("a", {}, False, error="e")
    manager._record_tool_event("b", {}, True, result="r")
    s = manager.get_tool_event_stats()
    assert s["total"] == 3
    assert s["success"] == 2
    assert s["failed"] == 1
    assert s["by_tool"]["a"] == {"total": 2, "success": 1, "failed": 1}
    assert s["by_tool"]["b"] == {"total": 1, "success": 1, "failed": 0}
    assert s["latest"]["tool"] == "b"


def test_get_tool_event_stats_empty(manager):
    s = manager.get_tool_event_stats()
    assert s["total"] == 0 and s["success"] == 0 and s["failed"] == 0
    assert s["latest"] is None
    assert s["by_tool"] == {}


def test_clear_tool_events(manager):
    manager._record_tool_event("a", {}, True)
    manager._record_tool_event("b", {}, False)
    assert manager.clear_tool_events() == 2
    assert manager.get_tool_events() == []


def test_record_tool_event_blackbox_logs(manager, monkeypatch):
    tm_mod = sys.modules[TM_MOD]
    bb = MagicMock()
    monkeypatch.setattr(tm_mod, "_get_blackbox", lambda: bb)
    manager._record_tool_event("t", {"a": 1}, False, error="e", latency_ms=2.5, source="sync")
    bb.log_module_call.assert_called_once()
    assert bb.log_module_call.call_args.kwargs["action"] == "call_failed"


def test_record_tool_event_blackbox_error_nonfatal(manager, monkeypatch):
    tm_mod = sys.modules[TM_MOD]
    bb = MagicMock()
    bb.log_module_call.side_effect = RuntimeError("blackbox down")
    monkeypatch.setattr(tm_mod, "_get_blackbox", lambda: bb)
    ev = manager._record_tool_event("t", {}, True, result="r")
    assert ev["tool"] == "t"
    assert ev["result_preview"] == "r"


# ── 工具发现 ────────────────────────────────────────────────────────────────

def test_list_available_tools_merges_mcp(manager, mcp_service, monkeypatch):
    monkeypatch.setattr(ToolRegistry, "list_tools", classmethod(
        lambda cls, source=None: {"local": {"source": "builtin", "description": "本地"}}
    ))
    spec = ToolSpec(name="remote_tool", description="远程")
    mcp_service.list_tools.return_value = {
        "remote_tool": spec,
        "local": ToolSpec(name="local"),
    }
    out = manager.list_available_tools(source="builtin")
    assert out["remote_tool"] == spec.to_listing()
    assert out["local"] == {"source": "builtin", "description": "本地"}
    mcp_service.list_tools.assert_called_once_with(source="builtin")


def test_list_available_tools_mcp_error(manager, mcp_service, monkeypatch):
    monkeypatch.setattr(ToolRegistry, "list_tools", classmethod(
        lambda cls, source=None: {"local": {"source": "builtin"}}
    ))
    mcp_service.list_tools.side_effect = RuntimeError("mcp down")
    assert manager.list_available_tools() == {"local": {"source": "builtin"}}


def test_list_by_source_adds_mcp(manager, mcp_service, monkeypatch):
    monkeypatch.setattr(ToolRegistry, "list_by_source", classmethod(
        lambda cls: {"builtin": ["a"], "plugin": [], "dynamic": []}
    ))
    mcp_service.list_tools.return_value = {
        "mcp_tool": ToolSpec(name="mcp_tool", source="mcp"),
        "remote": ToolSpec(name="remote", source="remote"),
    }
    out = manager.list_by_source()
    assert out["mcp"] == ["mcp_tool"]
    assert out["builtin"] == ["a"]


def test_list_by_source_extends_existing_mcp(manager, mcp_service, monkeypatch):
    monkeypatch.setattr(ToolRegistry, "list_by_source", classmethod(
        lambda cls: {"builtin": [], "plugin": [], "dynamic": [], "mcp": ["x"]}
    ))
    mcp_service.list_tools.return_value = {
        "mcp_tool": ToolSpec(name="mcp_tool", source="mcp"),
    }
    out = manager.list_by_source()
    assert out["mcp"] == ["x", "mcp_tool"]


def test_list_by_source_mcp_error(manager, mcp_service, monkeypatch):
    monkeypatch.setattr(ToolRegistry, "list_by_source", classmethod(
        lambda cls: {"builtin": ["a"], "plugin": [], "dynamic": []}
    ))
    mcp_service.list_tools.side_effect = RuntimeError("mcp down")
    assert manager.list_by_source() == {"builtin": ["a"], "plugin": [], "dynamic": []}


def test_get_tool_info_from_registry(manager, monkeypatch):
    info = ToolInfo(
        name="my_tool", func=lambda: 1, description="d", params={"x": "y"},
        source="dynamic", plugin_name="p", registered_at="ts",
    )
    monkeypatch.setattr(ToolRegistry, "get_tool", lambda n: info if n == "my_tool" else None)
    out = manager.get_tool_info("my_tool")
    assert out["name"] == "my_tool"
    assert out["description"] == "d"
    assert out["params"] == {"x": "y"}
    assert out["source"] == "dynamic"
    assert out["plugin_name"] == "p"
    assert out["registered_at"] == "ts"


def test_get_tool_info_from_mcp(manager, mcp_service, monkeypatch):
    monkeypatch.setattr(ToolRegistry, "get_tool", lambda n: None)
    spec = ToolSpec(name="remote", description="d")
    mcp_service.get_tool.return_value = spec
    out = manager.get_tool_info("remote")
    assert out == spec.to_listing()


def test_get_tool_info_missing(manager, mcp_service, monkeypatch):
    monkeypatch.setattr(ToolRegistry, "get_tool", lambda n: None)
    mcp_service.get_tool.return_value = None
    assert manager.get_tool_info("ghost") is None


def test_get_tool_info_mcp_error(manager, mcp_service, monkeypatch):
    monkeypatch.setattr(ToolRegistry, "get_tool", lambda n: None)
    mcp_service.get_tool.side_effect = RuntimeError("mcp down")
    assert manager.get_tool_info("ghost") is None


def test_get_status(manager, mcp_service, monkeypatch):
    manager._record_tool_event("a", {}, True, result="r")
    monkeypatch.setattr(ToolRegistry, "list_by_source", classmethod(
        lambda cls: {"builtin": ["a"], "plugin": [], "dynamic": []}
    ))
    monkeypatch.setattr(ToolRegistry, "list_tools", classmethod(
        lambda cls, source=None: {"a": {}, "b": {}}
    ))
    mcp_service.list_tools.return_value = {"mcp1": ToolSpec(name="mcp1")}
    s = manager.get_status()
    assert s["total_tools"] == 3
    assert s["builtin_count"] == 1
    assert s["mcp_count"] == 1
    assert "mcp1" in s["all_tools"]
    assert s["tool_backend"] == "mcp"
    assert s["event_stats"] == {"total": 1, "success": 1, "failed": 0}


def test_get_status_without_mcp(manager, mcp_service, monkeypatch):
    monkeypatch.setattr(ToolRegistry, "list_by_source", classmethod(
        lambda cls: {"builtin": ["a"], "plugin": [], "dynamic": []}
    ))
    monkeypatch.setattr(ToolRegistry, "list_tools", classmethod(
        lambda cls, source=None: {"a": {}, "b": {}}
    ))
    manager._use_mcp_for_lookup = lambda: False
    s = manager.get_status()
    assert s["mcp_count"] == 0
    assert s["total_tools"] == 2
    mcp_service.list_tools.assert_not_called()


def test_get_status_mcp_error(manager, mcp_service, monkeypatch):
    monkeypatch.setattr(ToolRegistry, "list_by_source", classmethod(
        lambda cls: {"builtin": [], "plugin": [], "dynamic": []}
    ))
    monkeypatch.setattr(ToolRegistry, "list_tools", classmethod(
        lambda cls, source=None: {}
    ))
    mcp_service.list_tools.side_effect = RuntimeError("mcp down")
    s = manager.get_status()
    assert s["mcp_count"] == 0
    assert s["total_tools"] == 0
