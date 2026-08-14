"""server_manager.py（MCP 生命周期管理器）单元测试

mock transport 边界（不发起真实连接），覆盖：
- __init__: 空/禁用/stdio/SSE(env url)/无 command 无 url 过滤
- start_all: 成功建索引 / connect 失败 / 无 transport
- add_server: 重复跳过 / stdio 成功 / SSE 成功 / connect 失败回滚 / 参数缺失
- get_all_tools / get_tool / get_server_for_tool / call_tool / get_server_status / shutdown
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from infra.mcp.server_manager import MCPServerManager
from infra.mcp.transport import MCPToolDef
from infra.mcp.types import MCPServerConfig


def _tool(name, server="srv"):
    return MCPToolDef(name=name, description=f"{name} desc", server_name=server)


def _fake_transport(name="srv", tools=None, connected=False, connect_ok=True):
    t = AsyncMock()
    t.connect.return_value = connect_ok
    t.list_tools.return_value = tools or []
    t.call_tool.return_value = {"isError": False, "content": [{"type": "text", "text": "ok"}]}
    t.close.return_value = None
    t.is_connected = connected
    t.server_name = name
    return t


def _cfg(name, **kw):
    kw.setdefault("command", "")
    kw.setdefault("args", [])
    kw.setdefault("env", {})
    kw.setdefault("enabled", True)
    kw.setdefault("timeout_seconds", 30.0)
    return MCPServerConfig(name=name, **kw)


class TestInit:
    def test_empty_servers(self):
        mgr = MCPServerManager([])
        assert mgr._transports == {}

    def test_skips_disabled_server(self):
        mgr = MCPServerManager([_cfg("off", command="x", enabled=False)])
        assert mgr._transports == {}

    def test_stdio_transport_created(self):
        with patch("infra.mcp.server_manager.MCPStdioTransport") as m:
            m.return_value = _fake_transport("srv")
            mgr = MCPServerManager(
                [_cfg("srv", command="python", args=["a"], env={"K": "V"}, timeout_seconds=7.0)]
            )
        m.assert_called_once_with(
            server_name="srv", command="python", args=["a"], env={"K": "V"}, timeout=7.0
        )
        assert "srv" in mgr._transports

    def test_sse_transport_from_env_url(self):
        with patch("infra.mcp.server_manager.MCPSseTransport") as m:
            m.return_value = _fake_transport("sse")
            mgr = MCPServerManager([_cfg("sse", env={"url": "http://x/sse"})])
        m.assert_called_once_with(server_name="sse", url="http://x/sse", timeout=30.0)
        assert "sse" in mgr._transports

    def test_no_command_no_url_dropped(self):
        mgr = MCPServerManager([_cfg("none")])
        assert mgr._transports == {}


class TestStartAll:
    async def test_connects_and_indexes_tools(self):
        t = _fake_transport("srv", tools=[_tool("a"), _tool("b")])
        mgr = MCPServerManager([])
        mgr._transports = {"srv": t}
        count = await mgr.start_all()
        assert count == 1
        t.connect.assert_awaited_once()
        t.list_tools.assert_awaited_once()
        assert set(mgr._tools_index) == {"a", "b"}
        assert mgr._tool_to_server["a"] == "srv"
        assert mgr._tool_to_server["b"] == "srv"

    async def test_connect_failure_not_indexed(self):
        t = _fake_transport("srv", connect_ok=False)
        mgr = MCPServerManager([])
        mgr._transports = {"srv": t}
        count = await mgr.start_all()
        assert count == 0
        t.list_tools.assert_not_awaited()
        assert mgr._tools_index == {}

    async def test_no_transports(self):
        assert await MCPServerManager([]).start_all() == 0


class TestAddServer:
    async def test_duplicate_name_skipped(self):
        mgr = MCPServerManager([])
        mgr._transports = {"srv": _fake_transport("srv")}
        with patch("infra.mcp.transport.MCPStdioTransport") as m:
            ok = await mgr.add_server("srv", command="x")
        assert ok is False
        m.assert_not_called()

    async def test_stdio_success(self):
        t = _fake_transport("new", tools=[_tool("nt", server="new")])
        with patch("infra.mcp.transport.MCPStdioTransport", return_value=t):
            mgr = MCPServerManager([])
            ok = await mgr.add_server("new", command="py", args=["a"], env={"E": "1"})
        assert ok is True
        assert "new" in mgr._transports
        assert mgr._tools_index["nt"].server_name == "new"
        assert mgr._tool_to_server["nt"] == "new"

    async def test_sse_success(self):
        t = _fake_transport("sse", tools=[_tool("st", server="sse")])
        with patch("infra.mcp.transport.MCPSseTransport", return_value=t):
            mgr = MCPServerManager([])
            ok = await mgr.add_server("sse", command="", url="http://x/sse")
        assert ok is True
        assert mgr._tool_to_server["st"] == "sse"

    async def test_connect_failure_rolls_back(self):
        t = _fake_transport("bad", connect_ok=False)
        with patch("infra.mcp.transport.MCPStdioTransport", return_value=t):
            mgr = MCPServerManager([])
            ok = await mgr.add_server("bad", command="py")
        assert ok is False
        assert "bad" not in mgr._transports
        assert mgr._tools_index == {}

    async def test_neither_command_nor_url(self):
        mgr = MCPServerManager([])
        ok = await mgr.add_server("x", command="")
        assert ok is False
        assert "x" not in mgr._transports


class TestGetters:
    def test_get_all_tools_returns_copy(self):
        mgr = MCPServerManager([])
        mgr._tools_index = {"a": _tool("a")}
        out = mgr.get_all_tools()
        assert out == {"a": mgr._tools_index["a"]}
        assert out is not mgr._tools_index

    def test_get_tool_found_and_missing(self):
        mgr = MCPServerManager([])
        mgr._tools_index = {"a": _tool("a")}
        assert mgr.get_tool("a") is not None
        assert mgr.get_tool("zzz") is None

    def test_get_server_for_tool(self):
        mgr = MCPServerManager([])
        mgr._tool_to_server = {"a": "s1"}
        assert mgr.get_server_for_tool("a") == "s1"
        assert mgr.get_server_for_tool("zzz") is None


class TestCallTool:
    async def test_tool_not_mapped(self):
        mgr = MCPServerManager([])
        r = await mgr.call_tool("nope")
        assert r["isError"] is True
        assert "不属于任何 MCP server" in r["content"][0]["text"]

    async def test_server_not_running(self):
        mgr = MCPServerManager([])
        mgr._tools_index = {"tool": _tool("tool")}
        mgr._tool_to_server["tool"] = "gone"
        r = await mgr.call_tool("tool")
        assert r["isError"] is True
        assert "不在运行" in r["content"][0]["text"]

    async def test_success_delegates_to_transport(self):
        t = _fake_transport("srv")
        mgr = MCPServerManager([])
        mgr._transports = {"srv": t}
        mgr._tool_to_server["tool"] = "srv"
        r = await mgr.call_tool("tool", {"a": 1})
        t.call_tool.assert_awaited_once_with("tool", {"a": 1})
        assert r["isError"] is False


class TestStatus:
    def test_status_report_counts(self):
        t1 = _fake_transport("s1", connected=True)
        t2 = _fake_transport("s2", connected=False)
        mgr = MCPServerManager([])
        mgr._transports = {"s1": t1, "s2": t2}
        mgr._tools_index = {
            "a": _tool("a", server="s1"),
            "b": _tool("b", server="s1"),
            "c": _tool("c", server="s2"),
        }
        mgr._tool_to_server = {"a": "s1", "b": "s1", "c": "s2"}
        status = {s["name"]: s for s in mgr.get_server_status()}
        assert status["s1"]["connected"] is True
        assert status["s1"]["tools_count"] == 2
        assert status["s2"]["connected"] is False
        assert status["s2"]["tools_count"] == 1


class TestShutdown:
    async def test_shutdown_closes_and_clears(self):
        t1 = _fake_transport("s1")
        t2 = _fake_transport("s2")
        mgr = MCPServerManager([])
        mgr._transports = {"s1": t1, "s2": t2}
        mgr._tools_index = {"a": _tool("a")}
        mgr._tool_to_server = {"a": "s1"}
        await mgr.shutdown()
        t1.close.assert_awaited_once()
        t2.close.assert_awaited_once()
        assert mgr._transports == {}
        assert mgr._tools_index == {}
        assert mgr._tool_to_server == {}


class TestRemoveServer:
    """独立热卸载：逆序清理（摘工具 → 关连接 → 清索引）"""

    async def test_remove_detaches_tools_and_closes(self):
        t = _fake_transport("srv", tools=[])
        mgr = MCPServerManager([])
        mgr._transports = {"srv": t}
        mgr._tools_index = {"a": _tool("a", "srv"), "b": _tool("b", "srv"), "c": _tool("c", "other")}
        mgr._tool_to_server = {"a": "srv", "b": "srv", "c": "other"}
        ok = await mgr.remove_server("srv")
        assert ok is True
        t.close.assert_awaited_once()
        assert "srv" not in mgr._transports
        assert "a" not in mgr._tools_index and "b" not in mgr._tools_index
        assert "c" in mgr._tools_index  # 其他 server 的工具不受影响
        assert mgr._tool_to_server == {"c": "other"}

    async def test_remove_unknown_returns_false(self):
        mgr = MCPServerManager([])
        mgr._transports = {"s1": _fake_transport("s1")}
        ok = await mgr.remove_server("nope")
        assert ok is False
        assert "s1" in mgr._transports  # 不影响已有 server

    async def test_remove_cleanup_uses_original_tool_names(self):
        t = _fake_transport("srv", tools=[])
        mgr = MCPServerManager([])
        mgr._transports = {"srv": t}
        # 工具名与 server 映射（含无 server 的工具）
        mgr._tools_index = {"a": _tool("a", "srv"), "orphan": _tool("orphan", "gone")}
        mgr._tool_to_server = {"a": "srv", "orphan": "gone"}
        await mgr.remove_server("srv")
        assert "orphan" in mgr._tools_index  # 不属于 srv，不误删

    async def test_remove_then_call_tool_fails_gracefully(self):
        t = _fake_transport("srv", tools=[])
        mgr = MCPServerManager([])
        mgr._transports = {"srv": t}
        mgr._tools_index = {"a": _tool("a", "srv")}
        mgr._tool_to_server = {"a": "srv"}
        await mgr.remove_server("srv")
        result = await mgr.call_tool("a", {})
        assert result["isError"] is True
        assert "不属于任何 MCP server" in result["content"][0]["text"]


class TestReplaceServer:
    """热替换：remove 旧（摘工具+断开）→ add 新配置"""

    async def test_replace_with_new_command(self):
        old = _fake_transport("srv", tools=[_tool("a")])
        new = _fake_transport("srv", tools=[_tool("b")], connect_ok=True)
        mgr = MCPServerManager([])
        mgr._transports = {"srv": old}
        mgr._tools_index = {"a": _tool("a", "srv")}
        mgr._tool_to_server = {"a": "srv"}
        with patch("infra.mcp.transport.MCPStdioTransport") as m:
            m.return_value = new
            ok = await mgr.replace_server("srv", command="new-cmd", args=["x"])
        assert ok is True
        old.close.assert_awaited_once()  # 旧连接已断开
        new.connect.assert_awaited_once()
        assert "a" not in mgr._tools_index  # 旧工具已摘
        assert mgr._tool_to_server.get("b") == "srv"  # 新工具已注册

    async def test_replace_missing_server_still_connects(self):
        t = _fake_transport("new", tools=[_tool("x")], connect_ok=True)
        mgr = MCPServerManager([])
        with patch("infra.mcp.transport.MCPStdioTransport") as m:
            m.return_value = t
            ok = await mgr.replace_server("new", command="cmd")
        assert ok is True
        assert mgr._tool_to_server.get("x") == "new"


class TestAutoReconnect:
    """断线自动重连：指数退避 + 重连后刷新工具索引"""

    async def test_reconnect_with_backoff_success(self):
        t = _fake_transport("srv", connected=False, connect_ok=True)
        mgr = MCPServerManager([])
        mgr._transports = {"srv": t}
        cfg = MCPServerConfig(name="srv", command="x", reconnect=True,
                              reconnect_max_retries=3, reconnect_base_delay=0.1)
        ok = await mgr._reconnect_with_backoff("srv", cfg)
        assert ok is True
        assert t.connect.await_count >= 1

    async def test_reconnect_with_backoff_gives_up(self):
        t = _fake_transport("srv", connected=False, connect_ok=False)
        mgr = MCPServerManager([])
        mgr._transports = {"srv": t}
        cfg = MCPServerConfig(name="srv", command="x", reconnect=True,
                              reconnect_max_retries=3, reconnect_base_delay=0.0)
        ok = await mgr._reconnect_with_backoff("srv", cfg)
        assert ok is False
        assert t.connect.await_count == 3  # 重试耗尽

    async def test_reconnect_stops_when_stop_event_set(self):
        t = _fake_transport("srv", connected=False, connect_ok=False)
        mgr = MCPServerManager([])
        mgr._transports = {"srv": t}
        mgr._stop_events["srv"] = asyncio.Event()
        mgr._stop_events["srv"].set()  # 已停止 → 不重试
        cfg = MCPServerConfig(name="srv", command="x", reconnect=True,
                              reconnect_max_retries=5, reconnect_base_delay=0.0)
        ok = await mgr._reconnect_with_backoff("srv", cfg)
        assert ok is False
        assert t.connect.await_count == 0

    async def test_refresh_tools_replaces_index(self):
        t = _fake_transport("srv", tools=[_tool("new_a"), _tool("new_b")])
        mgr = MCPServerManager([])
        mgr._transports = {"srv": t}
        mgr._tools_index = {"old": _tool("old", "srv"), "keep": _tool("keep", "other")}
        mgr._tool_to_server = {"old": "srv", "keep": "other"}
        await mgr._refresh_tools("srv")
        t.list_tools.assert_awaited_once()
        assert "old" not in mgr._tools_index      # 旧工具已摘
        assert "keep" in mgr._tools_index          # 其他 server 不受影响
        assert mgr._tool_to_server.get("new_a") == "srv"
        assert mgr._tool_to_server.get("new_b") == "srv"

    async def test_watch_connection_reconnects_and_refreshes(self):
        t = _fake_transport("srv", tools=[_tool("fresh")], connected=False, connect_ok=True)
        mgr = MCPServerManager([])
        mgr._transports = {"srv": t}
        mgr._tools_index = {"stale": _tool("stale", "srv")}
        mgr._tool_to_server = {"stale": "srv"}
        cfg = MCPServerConfig(name="srv", command="x", reconnect=True,
                              reconnect_interval=0.01,
                              reconnect_max_retries=2, reconnect_base_delay=0.0)
        mgr._configs["srv"] = cfg
        mgr._start_reconnect_watch("srv", cfg)
        assert "srv" in mgr._watch_tasks
        await asyncio.sleep(0.1)  # 让监控任务触发重连
        assert t.connect.await_count >= 1
        assert "fresh" in mgr._tools_index  # 重连后工具已刷新
        assert "stale" not in mgr._tools_index
        await mgr._stop_reconnect_watch("srv")  # 防任务遗留
        assert "srv" not in mgr._watch_tasks

    async def test_start_all_launches_watch_when_reconnect(self):
        t = _fake_transport("srv", tools=[_tool("a")], connect_ok=True)
        mgr = MCPServerManager([])
        mgr._transports = {"srv": t}
        mgr._configs["srv"] = MCPServerConfig(name="srv", command="x", reconnect=True,
                                              reconnect_interval=60.0)
        await mgr.start_all()
        assert "srv" in mgr._watch_tasks
        await mgr._stop_all_reconnect_watches()

    async def test_start_all_no_watch_when_disabled(self):
        t = _fake_transport("srv", tools=[_tool("a")], connect_ok=True)
        mgr = MCPServerManager([])
        mgr._transports = {"srv": t}
        mgr._configs["srv"] = MCPServerConfig(name="srv", command="x", reconnect=False)
        await mgr.start_all()
        assert mgr._watch_tasks == {}

    async def test_remove_server_stops_watch(self):
        t = _fake_transport("srv", connected=False, connect_ok=True)
        mgr = MCPServerManager([])
        mgr._transports = {"srv": t}
        cfg = MCPServerConfig(name="srv", command="x", reconnect=True,
                              reconnect_interval=0.01,
                              reconnect_max_retries=2, reconnect_base_delay=0.0)
        mgr._configs["srv"] = cfg
        mgr._start_reconnect_watch("srv", cfg)
        assert "srv" in mgr._watch_tasks
        await mgr.remove_server("srv")
        assert "srv" not in mgr._watch_tasks
        assert "srv" not in mgr._transports

    async def test_shutdown_stops_all_watches(self):
        t1 = _fake_transport("s1", connected=False, connect_ok=True)
        t2 = _fake_transport("s2", connected=False, connect_ok=True)
        mgr = MCPServerManager([])
        mgr._transports = {"s1": t1, "s2": t2}
        for n, t in (("s1", t1), ("s2", t2)):
            mgr._configs[n] = MCPServerConfig(name=n, command="x", reconnect=True,
                                              reconnect_interval=0.01,
                                              reconnect_max_retries=2, reconnect_base_delay=0.0)
            mgr._start_reconnect_watch(n, mgr._configs[n])
        assert len(mgr._watch_tasks) == 2
        await mgr.shutdown()
        assert mgr._watch_tasks == {}
        assert mgr._stop_events == {}
