"""mcp_tools 测试 — discover/call_tool/server_status/register_server 全路径覆盖"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import infra.mcp.factory as factory_mod
from infra.tool_manager.tools import mcp_tools


@pytest.fixture
def mock_mgr(monkeypatch):
    mgr = MagicMock()
    monkeypatch.setattr(factory_mod, "get_server_manager", lambda: mgr)
    return mgr


def test_discover_success(mock_mgr):
    t1 = SimpleNamespace(server_name="s1")
    t2 = SimpleNamespace(server_name="other")
    mock_mgr.get_server_status.return_value = [
        {"name": "s1", "connected": True},
        {"name": "s2", "connected": False},
    ]
    mock_mgr.get_all_tools.return_value = {"t1": t1, "t2": t2}
    r = mcp_tools.mcp_discover()
    assert r["success"] is True
    assert r["total_servers"] == 2
    assert r["total_tools"] == 2
    assert r["servers"][0]["name"] == "s1"
    assert r["servers"][0]["tools"] == ["t1"]
    assert r["servers"][0]["count"] == 1


def test_discover_exception_falls_back(mock_mgr):
    mock_mgr.get_server_status.side_effect = RuntimeError("boom")
    r = mcp_tools.mcp_discover()
    assert r == {"success": True, "servers": [], "total_servers": 0, "total_tools": 0}


def test_call_tool_empty_name(mock_mgr):
    r = mcp_tools.mcp_call_tool("")
    assert r["success"] is False
    assert "工具名不能为空" in r["error"]


def test_call_tool_invalid_params(mock_mgr):
    r = mcp_tools.mcp_call_tool("f", params="not-json")
    assert r["success"] is False
    assert "JSON" in r["error"]


def test_call_tool_success_text(mock_mgr):
    text_obj = SimpleNamespace(type="text", text="from-object")
    mock_mgr.call_tool.return_value = {
        "content": [{"type": "text", "text": "hello"}, {"type": "image", "text": "ignored"}, text_obj],
        "isError": False,
    }
    r = mcp_tools.mcp_call_tool("f", params='{"a":1}')
    assert r["success"] is True
    assert r["result"] == "hellofrom-object"
    mock_mgr.call_tool.assert_called_once_with("f", {"a": 1})


def test_call_tool_is_error(mock_mgr):
    mock_mgr.call_tool.return_value = {"content": [{"type": "text", "text": "fail"}], "isError": True}
    r = mcp_tools.mcp_call_tool("f")
    assert r["success"] is False
    assert r["error"] == "fail"


def test_call_tool_exception(mock_mgr):
    mock_mgr.call_tool.side_effect = RuntimeError("boom")
    r = mcp_tools.mcp_call_tool("f")
    assert r["success"] is False
    assert "MCP 调用失败" in r["error"]


def test_server_status_success(mock_mgr):
    mock_mgr.get_server_status.return_value = [{"name": "s1", "connected": True}]
    r = mcp_tools.mcp_server_status()
    assert r["success"] is True
    assert r["total"] == 1
    assert r["servers"][0]["name"] == "s1"


def test_server_status_exception(mock_mgr):
    mock_mgr.get_server_status.side_effect = RuntimeError("boom")
    r = mcp_tools.mcp_server_status()
    assert r["success"] is False
    assert "boom" in r["error"]


def test_register_server_empty_command(mock_mgr):
    r = asyncio.run(mcp_tools.mcp_register_server("srv", "   "))
    assert r["success"] is False
    assert "command 不能为空" in r["error"]


def test_register_server_success(mock_mgr):
    async def add_server(name, command, args):
        return True

    mock_mgr.add_server = add_server
    mock_mgr.get_all_tools.return_value = {"t1": SimpleNamespace(), "t2": SimpleNamespace()}
    mock_mgr.get_server_for_tool.side_effect = lambda t: "srv" if t == "t1" else "other"
    r = asyncio.run(mcp_tools.mcp_register_server("srv", "npx -y @modelcontextprotocol/server-filesystem ./"))
    assert r["success"] is True
    assert r["tools_count"] == 1
    assert "已启动并连接" in r["message"]


def test_register_server_connect_failed(mock_mgr):
    async def add_server(name, command, args):
        return False

    mock_mgr.add_server = add_server
    r = asyncio.run(mcp_tools.mcp_register_server("srv", "bad-cmd"))
    assert r["success"] is False
    assert "连接失败" in r["error"]


def test_register_server_exception(mock_mgr):
    async def add_server(name, command, args):
        raise RuntimeError("boom")

    mock_mgr.add_server = add_server
    r = asyncio.run(mcp_tools.mcp_register_server("srv", "cmd"))
    assert r["success"] is False
    assert "boom" in r["error"]
