"""server_manager.py（MCP 生命周期管理器）单元测试

mock transport 边界（不发起真实连接），覆盖：
- __init__: 空/禁用/stdio/SSE(env url)/无 command 无 url 过滤
- start_all: 成功建索引 / connect 失败 / 无 transport
- add_server: 重复跳过 / stdio 成功 / SSE 成功 / connect 失败回滚 / 参数缺失
- get_all_tools / get_tool / get_server_for_tool / call_tool / get_server_status / shutdown
"""
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
