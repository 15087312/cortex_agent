"""transport.py（MCP 传输层）单元测试

mock mcp SDK 边界，覆盖成功/失败/超时/异常/跨事件循环调用等路径：
- MCPStdioTransport: _submit_on_loop / connect / list_tools / call_tool / close
- MCPSseTransport: 同上（SSE 版）
- MCPToolDef 数据类
"""
import asyncio
import concurrent.futures
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from infra.mcp.transport import MCPSseTransport, MCPStdioTransport, MCPToolDef


class _LoopRunner:
    """在后台线程中运行真实事件循环，用于测试跨线程 _submit_on_loop"""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)
        self.loop.close()


async def _acoro():
    return "done"


def _tools_result(*names):
    return SimpleNamespace(tools=[
        SimpleNamespace(name=n, description=f"{n} desc", inputSchema={"type": "object"})
        for n in names
    ])


def _closing_coro():
    """创建协程后立即 close，避免 run_coroutine_threadsafe 被 mock 时 never-awaited 警告"""
    coro = _acoro()
    coro.close()
    return coro


def _fake_ctx():
    """可 await 的异步上下文 mock（__aenter__/__aexit__ 均为 AsyncMock）"""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


class TestMCPToolDef:
    def test_full(self):
        d = MCPToolDef(name="x", description="desc", input_schema={"a": 1}, server_name="s")
        assert d.name == "x"
        assert d.description == "desc"
        assert d.input_schema == {"a": 1}
        assert d.server_name == "s"


class TestMCPStdioTransport:
    def make(self, **kw):
        kw.setdefault("server_name", "srv")
        kw.setdefault("command", "echo")
        return MCPStdioTransport(**kw)

    # ---------- __init__ ----------
    def test_init_defaults(self):
        t = MCPStdioTransport("srv", "echo", args=None, env=None)
        assert t._args == []
        assert t._env == {}
        assert t._timeout == 30.0
        assert t.is_connected is False
        assert t._session is None and t._stdio_ctx is None and t._session_ctx is None

    def test_init_custom(self):
        t = MCPStdioTransport("srv", "cmd", args=["a"], env={"K": "V"}, timeout=7.5)
        assert t._args == ["a"]
        assert t._env == {"K": "V"}
        assert t._timeout == 7.5

    # ---------- _submit_on_loop ----------
    def test_submit_on_loop_no_loop(self):
        t = self.make()
        with pytest.raises(RuntimeError, match="未记录事件循环"):
            t._submit_on_loop(lambda: _acoro())

    def test_submit_on_loop_closed_loop(self):
        t = self.make()
        dead = asyncio.new_event_loop()
        dead.close()
        t._loop = dead
        with pytest.raises(RuntimeError, match="事件循环已关闭"):
            t._submit_on_loop(lambda: _acoro())

    def test_submit_on_loop_success(self):
        t = self.make(timeout=10)
        with _LoopRunner() as runner:
            t._loop = runner.loop
            assert t._submit_on_loop(lambda: _acoro()) == "done"

    def test_submit_on_loop_timeout(self):
        t = self.make(timeout=0.001)
        t._loop = MagicMock()
        t._loop.is_closed.return_value = False

        class _SlowFuture:
            def result(self, timeout=None):
                raise concurrent.futures.TimeoutError()

        with patch("asyncio.run_coroutine_threadsafe", return_value=_SlowFuture()):
            with pytest.raises(concurrent.futures.TimeoutError):
                t._submit_on_loop(_closing_coro)

    # ---------- connect ----------
    async def test_connect_success(self):
        t = self.make(timeout=10)
        session = AsyncMock()
        session.initialize = AsyncMock()
        session.list_tools = AsyncMock(return_value=_tools_result("alpha", "beta"))
        with patch("mcp.client.stdio.stdio_client") as mock_sc, patch("mcp.ClientSession") as mock_cs:
            mock_sc.return_value.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=session)
            ok = await t.connect()
        assert ok is True
        assert t.is_connected is True
        assert [x.name for x in t._tools_cache] == ["alpha", "beta"]
        assert all(x.server_name == "srv" for x in t._tools_cache)
        session.initialize.assert_awaited_once()
        session.list_tools.assert_awaited_once()

    async def test_connect_initialize_failure(self):
        t = self.make(timeout=10)
        session = AsyncMock()
        session.initialize = AsyncMock(side_effect=RuntimeError("init boom"))
        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session)
        stdio_ctx = _fake_ctx()
        with patch("mcp.client.stdio.stdio_client", return_value=stdio_ctx) as mock_sc, \
             patch("mcp.ClientSession", return_value=session_ctx):
            ok = await t.connect()
        assert ok is False
        assert t.is_connected is False
        assert t._session is None
        stdio_ctx.__aexit__.assert_awaited_once()

    async def test_connect_list_tools_failure(self):
        t = self.make(timeout=10)
        session = AsyncMock()
        session.initialize = AsyncMock()
        session.list_tools = AsyncMock(side_effect=Exception("list boom"))
        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session)
        stdio_ctx = _fake_ctx()
        with patch("mcp.client.stdio.stdio_client", return_value=stdio_ctx), \
             patch("mcp.ClientSession", return_value=session_ctx):
            ok = await t.connect()
        assert ok is False
        assert t._tools_cache == []

    # ---------- list_tools ----------
    async def test_list_tools_cached(self):
        t = self.make()
        t._tools_cache = [MCPToolDef(name="x")]
        assert await t.list_tools() == t._tools_cache

    async def test_list_tools_not_connected(self):
        t = self.make()
        assert await t.list_tools() == []

    async def test_list_tools_same_loop(self):
        t = self.make(timeout=10)
        t._loop = asyncio.get_running_loop()
        session = AsyncMock()
        session.list_tools = AsyncMock(return_value=_tools_result("only"))
        t._session = session
        tools = await t.list_tools()
        assert [x.name for x in tools] == ["only"]
        session.list_tools.assert_awaited_once()

    async def test_list_tools_other_loop(self):
        t = self.make(timeout=10)
        t._loop = object()  # 非当前事件循环 → 走 asyncio.to_thread 分支
        t._session = AsyncMock()
        with patch.object(t, "_submit_on_loop", return_value=_tools_result("rem")) as m:
            tools = await t.list_tools()
        m.assert_called_once()
        assert [x.name for x in tools] == ["rem"]

    async def test_list_tools_exception_returns_cache(self):
        t = self.make(timeout=10)
        t._loop = object()
        t._session = AsyncMock()
        with patch.object(t, "_submit_on_loop", side_effect=RuntimeError("boom")):
            assert await t.list_tools() == []

    # ---------- call_tool ----------
    async def test_call_tool_not_connected(self):
        t = self.make()
        r = await t.call_tool("foo")
        assert r["isError"] is True
        assert "未连接" in r["content"][0]["text"]

    async def test_call_tool_same_loop(self):
        t = self.make(timeout=10)
        t._loop = asyncio.get_running_loop()
        session = AsyncMock()
        session.call_tool = AsyncMock(return_value=SimpleNamespace(
            isError=False, content=[{"type": "text", "text": "hi"}]))
        t._session = session
        r = await t.call_tool("foo", {"q": 1})
        assert r["isError"] is False
        assert r["content"][0]["text"] == "hi"
        session.call_tool.assert_awaited_once_with("foo", {"q": 1})

    async def test_call_tool_same_loop_missing_attrs(self):
        t = self.make(timeout=10)
        t._loop = asyncio.get_running_loop()
        session = AsyncMock()
        session.call_tool = AsyncMock(return_value=SimpleNamespace(isError=True))
        t._session = session
        r = await t.call_tool("foo")  # arguments=None → {}
        assert r["isError"] is True
        assert r["content"] == []
        session.call_tool.assert_awaited_once_with("foo", {})

    async def test_call_tool_other_loop(self):
        t = self.make(timeout=10)
        t._loop = object()
        t._session = AsyncMock()
        with patch.object(t, "_submit_on_loop", return_value=SimpleNamespace(
                isError=False, content=[])) as m:
            r = await t.call_tool("foo", {"a": 1})
        m.assert_called_once()
        assert r["isError"] is False

    async def test_call_tool_timeout(self):
        t = self.make(timeout=5)
        t._loop = object()
        t._session = AsyncMock()
        with patch.object(t, "_submit_on_loop",
                          side_effect=concurrent.futures.TimeoutError()):
            r = await t.call_tool("foo")
        assert r["isError"] is True
        assert "超时" in r["content"][0]["text"]

    async def test_call_tool_exception(self):
        t = self.make(timeout=5)
        t._loop = object()
        t._session = AsyncMock()
        with patch.object(t, "_submit_on_loop", side_effect=ValueError("boom")):
            r = await t.call_tool("foo")
        assert r["isError"] is True
        assert r["content"][0]["text"] == "boom"

    # ---------- close ----------
    async def test_close_same_loop(self):
        t = self.make(timeout=10)
        t._loop = asyncio.get_running_loop()
        t._session = AsyncMock()
        session_ctx = AsyncMock()
        stdio_ctx = AsyncMock()
        t._session_ctx = session_ctx
        t._stdio_ctx = stdio_ctx
        await t.close()
        session_ctx.__aexit__.assert_awaited_once_with(None, None, None)
        stdio_ctx.__aexit__.assert_awaited_once_with(None, None, None)
        assert t._session is None
        assert t._session_ctx is None
        assert t._stdio_ctx is None
        assert t.is_connected is False

    async def test_close_no_loop_recorded(self):
        t = self.make(timeout=10)  # 从未 connect → _loop 为 None
        session_ctx = AsyncMock()
        stdio_ctx = AsyncMock()
        t._session_ctx = session_ctx
        t._stdio_ctx = stdio_ctx
        await t.close()
        session_ctx.__aexit__.assert_awaited_once_with(None, None, None)
        stdio_ctx.__aexit__.assert_awaited_once_with(None, None, None)
        assert t._session_ctx is None
        assert t._stdio_ctx is None

    async def test_close_empty_transport(self):
        t = self.make(timeout=10)  # 无任何 context → 全部为 False 分支
        await t.close()
        assert t._session_ctx is None
        assert t._stdio_ctx is None
        assert t.is_connected is False

    async def test_close_other_loop(self):
        t = self.make(timeout=10)
        session_ctx = AsyncMock()
        stdio_ctx = AsyncMock()
        with _LoopRunner() as runner:
            t._loop = runner.loop
            t._session = AsyncMock()
            t._session_ctx = session_ctx
            t._stdio_ctx = stdio_ctx
            await t.close()
        session_ctx.__aexit__.assert_awaited_once_with(None, None, None)
        stdio_ctx.__aexit__.assert_awaited_once_with(None, None, None)
        assert t._session is None

    async def test_close_session_error_non_fatal(self):
        t = self.make(timeout=10)
        t._loop = asyncio.get_running_loop()
        session_ctx = AsyncMock()
        session_ctx.__aexit__ = AsyncMock(side_effect=RuntimeError("sess"))
        stdio_ctx = AsyncMock()
        t._session_ctx = session_ctx
        t._stdio_ctx = stdio_ctx
        await t.close()
        stdio_ctx.__aexit__.assert_awaited_once()
        assert t._session_ctx is None

    async def test_close_stdio_error_non_fatal(self):
        t = self.make(timeout=10)
        t._loop = asyncio.get_running_loop()
        session_ctx = AsyncMock()
        stdio_ctx = AsyncMock()
        stdio_ctx.__aexit__ = AsyncMock(side_effect=RuntimeError("stdio"))
        t._session_ctx = session_ctx
        t._stdio_ctx = stdio_ctx
        await t.close()
        session_ctx.__aexit__.assert_awaited_once()
        assert t._stdio_ctx is None

    async def test_close_other_loop_submit_raises(self):
        t = self.make(timeout=10)
        dead = asyncio.new_event_loop()
        dead.close()
        t._loop = dead
        t._session = AsyncMock()
        t._session_ctx = AsyncMock()
        t._stdio_ctx = AsyncMock()
        await t.close()  # _submit_on_loop 抛 RuntimeError → 非致命
        assert t._session_ctx is None
        assert t._stdio_ctx is None

    # ---------- property ----------
    def test_is_connected_property(self):
        t = self.make()
        assert t.is_connected is False
        t._connected = True
        assert t.is_connected is True


class TestMCPSseTransport:
    def make(self, **kw):
        kw.setdefault("server_name", "sse_srv")
        kw.setdefault("url", "http://localhost:9999/sse")
        t = MCPSseTransport(**kw)
        t._loop = None  # SSE __init__ 未初始化 _loop，测试里显式设置
        return t

    # ---------- _submit_on_loop ----------
    def test_submit_on_loop_no_loop(self):
        t = self.make()
        with pytest.raises(RuntimeError, match="未记录事件循环"):
            t._submit_on_loop(lambda: _acoro())

    def test_submit_on_loop_closed_loop(self):
        t = self.make()
        dead = asyncio.new_event_loop()
        dead.close()
        t._loop = dead
        with pytest.raises(RuntimeError, match="事件循环已关闭"):
            t._submit_on_loop(lambda: _acoro())

    def test_submit_on_loop_success(self):
        t = self.make(timeout=10)
        with _LoopRunner() as runner:
            t._loop = runner.loop
            assert t._submit_on_loop(lambda: _acoro()) == "done"

    # ---------- connect ----------
    async def test_connect_success(self):
        t = self.make(timeout=10)
        session = AsyncMock()
        session.initialize = AsyncMock()
        session.list_tools = AsyncMock(return_value=_tools_result("s1", "s2"))
        with patch("mcp.client.sse.sse_client") as mock_sse, patch("mcp.ClientSession") as mock_cs:
            mock_sse.return_value.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=session)
            ok = await t.connect()
        assert ok is True
        assert t.is_connected is True
        assert [x.name for x in t._tools_cache] == ["s1", "s2"]
        mock_sse.assert_called_once_with(url=t._url)

    async def test_connect_initialize_failure(self):
        t = self.make(timeout=10)
        session = AsyncMock()
        session.initialize = AsyncMock(side_effect=RuntimeError("sse init boom"))
        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session)
        sse_ctx = _fake_ctx()
        with patch("mcp.client.sse.sse_client", return_value=sse_ctx), \
             patch("mcp.ClientSession", return_value=session_ctx):
            ok = await t.connect()
        assert ok is False
        assert t.is_connected is False
        assert t._session is None
        sse_ctx.__aexit__.assert_awaited_once()

    async def test_connect_list_tools_failure(self):
        t = self.make(timeout=10)
        session = AsyncMock()
        session.initialize = AsyncMock()
        session.list_tools = AsyncMock(side_effect=Exception("sse list boom"))
        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session)
        with patch("mcp.client.sse.sse_client", return_value=_fake_ctx()), \
             patch("mcp.ClientSession", return_value=session_ctx):
            ok = await t.connect()
        assert ok is False
        assert t._tools_cache == []

    # ---------- list_tools ----------
    async def test_list_tools_cached(self):
        t = self.make()
        t._tools_cache = [MCPToolDef(name="x")]
        assert await t.list_tools() == t._tools_cache

    async def test_list_tools_not_connected(self):
        t = self.make()
        assert await t.list_tools() == []

    async def test_list_tools_same_loop(self):
        t = self.make(timeout=10)
        t._loop = asyncio.get_running_loop()
        session = AsyncMock()
        session.list_tools = AsyncMock(return_value=_tools_result("only"))
        t._session = session
        tools = await t.list_tools()
        assert [x.name for x in tools] == ["only"]

    async def test_list_tools_other_loop_and_exception(self):
        t = self.make(timeout=10)
        t._loop = object()
        t._session = AsyncMock()
        with patch.object(t, "_submit_on_loop", return_value=_tools_result("rem")):
            assert [x.name for x in await t.list_tools()] == ["rem"]
        t._tools_cache = []  # 清空缓存，使第二次调用走异常路径
        with patch.object(t, "_submit_on_loop", side_effect=RuntimeError("boom")):
            assert await t.list_tools() == []

    # ---------- call_tool ----------
    async def test_call_tool_not_connected(self):
        t = self.make()
        r = await t.call_tool("foo")
        assert r["isError"] is True
        assert "未连接" in r["content"][0]["text"]

    async def test_call_tool_same_loop(self):
        t = self.make(timeout=10)
        t._loop = asyncio.get_running_loop()
        session = AsyncMock()
        session.call_tool = AsyncMock(return_value=SimpleNamespace(
            isError=True, content=[{"type": "text", "text": "e"}]))
        t._session = session
        r = await t.call_tool("foo", {"a": 1})
        assert r["isError"] is True
        session.call_tool.assert_awaited_once_with("foo", {"a": 1})

    async def test_call_tool_other_loop(self):
        t = self.make(timeout=10)
        t._loop = object()
        t._session = AsyncMock()
        with patch.object(t, "_submit_on_loop",
                          return_value=SimpleNamespace(isError=False, content=[])):
            assert (await t.call_tool("foo"))["isError"] is False

    async def test_call_tool_timeout(self):
        t = self.make(timeout=5)
        t._loop = object()
        t._session = AsyncMock()
        with patch.object(t, "_submit_on_loop",
                          side_effect=concurrent.futures.TimeoutError()):
            r = await t.call_tool("foo")
        assert "超时" in r["content"][0]["text"]

    async def test_call_tool_exception(self):
        t = self.make(timeout=5)
        t._loop = object()
        t._session = AsyncMock()
        with patch.object(t, "_submit_on_loop", side_effect=ValueError("sse boom")):
            r = await t.call_tool("foo")
        assert r["content"][0]["text"] == "sse boom"

    # ---------- close ----------
    async def test_close_same_loop(self):
        t = self.make(timeout=10)
        t._loop = asyncio.get_running_loop()
        t._session = AsyncMock()
        session_ctx = AsyncMock()
        sse_ctx = AsyncMock()
        t._session_ctx = session_ctx
        t._sse_ctx = sse_ctx
        await t.close()
        session_ctx.__aexit__.assert_awaited_once_with(None, None, None)
        sse_ctx.__aexit__.assert_awaited_once_with(None, None, None)
        assert t._session is None
        assert t._session_ctx is None
        assert t._sse_ctx is None
        assert t.is_connected is False

    async def test_close_no_loop_recorded(self):
        t = self.make(timeout=10)  # 从未 connect → _loop 为 None
        session_ctx = AsyncMock()
        sse_ctx = AsyncMock()
        t._session_ctx = session_ctx
        t._sse_ctx = sse_ctx
        await t.close()
        session_ctx.__aexit__.assert_awaited_once_with(None, None, None)
        sse_ctx.__aexit__.assert_awaited_once_with(None, None, None)
        assert t._session_ctx is None
        assert t._sse_ctx is None

    async def test_close_empty_transport(self):
        t = self.make(timeout=10)  # 无任何 context → 全部为 False 分支
        await t.close()
        assert t._session_ctx is None
        assert t._sse_ctx is None
        assert t.is_connected is False

    async def test_close_other_loop(self):
        t = self.make(timeout=10)
        session_ctx = AsyncMock()
        sse_ctx = AsyncMock()
        with _LoopRunner() as runner:
            t._loop = runner.loop
            t._session = AsyncMock()
            t._session_ctx = session_ctx
            t._sse_ctx = sse_ctx
            await t.close()
        session_ctx.__aexit__.assert_awaited_once_with(None, None, None)
        sse_ctx.__aexit__.assert_awaited_once_with(None, None, None)

    async def test_close_session_error_non_fatal(self):
        t = self.make(timeout=10)
        t._loop = asyncio.get_running_loop()
        session_ctx = AsyncMock()
        session_ctx.__aexit__ = AsyncMock(side_effect=RuntimeError("sess"))
        sse_ctx = AsyncMock()
        t._session_ctx = session_ctx
        t._sse_ctx = sse_ctx
        await t.close()
        sse_ctx.__aexit__.assert_awaited_once()
        assert t._session_ctx is None

    async def test_close_sse_error_non_fatal(self):
        t = self.make(timeout=10)
        t._loop = asyncio.get_running_loop()
        session_ctx = AsyncMock()
        sse_ctx = AsyncMock()
        sse_ctx.__aexit__ = AsyncMock(side_effect=RuntimeError("sse"))
        t._session_ctx = session_ctx
        t._sse_ctx = sse_ctx
        await t.close()
        session_ctx.__aexit__.assert_awaited_once()
        assert t._sse_ctx is None

    async def test_close_other_loop_submit_raises(self):
        t = self.make(timeout=10)
        dead = asyncio.new_event_loop()
        dead.close()
        t._loop = dead
        t._session = AsyncMock()
        t._session_ctx = AsyncMock()
        t._sse_ctx = AsyncMock()
        await t.close()
        assert t._session_ctx is None
        assert t._sse_ctx is None

    # ---------- property ----------
    def test_is_connected_property(self):
        t = self.make()
        assert t.is_connected is False
        t._connected = True
        assert t.is_connected is True
