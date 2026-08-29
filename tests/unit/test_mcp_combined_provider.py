"""combined_provider.py 单元测试

合并 Provider/Executor：本地 ToolRegistry + 远程 MCP 工具。
覆盖：
- _get_async_pool / _run_async_in_thread（独立事件循环、清理异常非致命）
- CombinedToolProvider: list_tools / get_tool / get_tools_for_api
- CombinedToolExecutor: 本地路由（sync/async/异常）、MCP 路由（成功/错误解析/超时/异常）
"""
import asyncio
import concurrent.futures
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from infra.mcp.combined_provider import CombinedToolExecutor, CombinedToolProvider
from infra.mcp.transport import MCPToolDef
from infra.mcp.types import ToolCallRequest


@pytest.fixture(autouse=True)
def _reset_async_pool():
    """每个测试后清理全局共享线程池，避免跨测试泄漏"""
    import infra.mcp.combined_provider as cp
    prev = cp._ASYNC_TOOL_POOL
    cp._ASYNC_TOOL_POOL = None
    yield
    cur = cp._ASYNC_TOOL_POOL
    cp._ASYNC_TOOL_POOL = None
    for pool in (prev, cur):
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)


def _mcp(name, desc="mcp desc", schema=None, server="mcp_srv"):
    return MCPToolDef(name=name, description=desc,
                      input_schema=schema or {"type": "object"}, server_name=server)


def _manager(tools=None):
    from infra.mcp.server_manager import MCPServerManager
    mgr = MCPServerManager([])
    for name, tool in (tools or {}).items():
        mgr._tools_index[name] = tool
    return mgr


def _patch_get_func(monkeypatch, fn):
    from infra.tool_manager.tool_registry import ToolRegistry
    monkeypatch.setattr(ToolRegistry, "get_func", staticmethod(fn))


def _patch_list_tools(monkeypatch, data):
    from infra.tool_manager.tool_registry import ToolRegistry
    monkeypatch.setattr(ToolRegistry, "list_tools", staticmethod(lambda source=None: data))


# ====================================================================
# _get_async_pool / _run_async_in_thread
# ====================================================================

class TestAsyncPool:
    def test_get_async_pool_creates_and_reuses(self):
        import infra.mcp.combined_provider as cp
        pool = cp._get_async_pool()
        assert cp._ASYNC_TOOL_POOL is pool
        assert cp._get_async_pool() is pool  # 已存在 → 复用同一池

    def test_run_async_in_thread_basic(self):
        from infra.mcp.combined_provider import _run_async_in_thread

        async def fn(**kw):
            return kw

        assert _run_async_in_thread(fn, {"a": 1}) == {"a": 1}

    def test_run_async_in_thread_propagates_error(self):
        from infra.mcp.combined_provider import _run_async_in_thread

        async def bad(**kw):
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            _run_async_in_thread(bad, {})

    def test_run_async_in_thread_cleanup_errors_non_fatal(self, monkeypatch):
        import infra.mcp.combined_provider as cp

        class FakeLoop:
            def run_until_complete(self, coro):
                coro.close()  # 释放协程，避免 never-awaited 警告
                return "fake"

            def shutdown_asyncgens(self):
                raise RuntimeError("gens boom")

            def shutdown_default_executor(self, timeout=5.0):
                raise RuntimeError("exec boom")

            def close(self):
                raise RuntimeError("close boom")

        async def noop(**kw):
            return "ok"

        monkeypatch.setattr(cp.asyncio, "new_event_loop", lambda: FakeLoop())
        monkeypatch.setattr(cp.asyncio, "set_event_loop", lambda *a: None)
        # 三个清理步骤均抛异常，但都应为非致命，结果照常返回
        assert cp._run_async_in_thread(noop, {}) == "fake"


# ====================================================================
# CombinedToolProvider
# ====================================================================

class TestCombinedToolProvider:
    def test_list_tools_builds_specs(self, monkeypatch):
        data = {
            "local1": {"name": "l1", "description": "d", "params": {"p": {}},
                       "source": "builtin", "plugin_name": "pl", "risk_level": "HIGH",
                       "category": "admin", "registered_at": "t"},
            "minimal": {},  # 全部走默认值
        }
        _patch_list_tools(monkeypatch, data)
        tools = CombinedToolProvider(_manager()).list_tools()

        t = tools["local1"]
        assert t.name == "l1"
        assert t.description == "d"
        assert t.source == "builtin"
        assert t.server_name == "legacy"
        assert t.native_name == "local1"
        assert t.plugin_name == "pl"
        assert t.risk_level == "HIGH"
        assert t.category == "admin"
        assert t.registered_at == "t"

        m = tools["minimal"]
        assert m.name == "minimal"
        assert m.description == ""
        assert m.source == "builtin"
        assert m.parameters == {"type": "object", "properties": {}}

    def test_list_tools_with_mcp_tools(self, monkeypatch):
        _patch_list_tools(monkeypatch, {"local1": {"source": "builtin"}})
        mgr = _manager({"zz_remote": _mcp("zz_remote", desc="remote desc",
                                          schema={"type": "object", "properties": {"q": {}}})})
        tools = CombinedToolProvider(mgr).list_tools()

        t = tools["zz_remote"]
        assert t.source == "mcp"
        assert t.server_name == "mcp_srv"
        assert t.native_name == "zz_remote"
        assert t.description == "remote desc"
        assert t.risk_level == "MEDIUM"
        assert t.category == "mcp"

    def test_list_tools_source_filter(self, monkeypatch):
        _patch_list_tools(monkeypatch, {"l": {"source": "builtin"}, "p": {"source": "plugin"}})
        tools = CombinedToolProvider(_manager()).list_tools(source="plugin")
        assert list(tools) == ["p"]

    def test_get_tool(self, monkeypatch):
        _patch_list_tools(monkeypatch, {"abc": {"source": "builtin"}})
        p = CombinedToolProvider(_manager())
        assert p.get_tool("abc") is not None
        assert p.get_tool("zzz_nope") is None

    def test_get_tools_for_api_schema_building(self, monkeypatch):
        data = {
            "local1": {"description": "d1",
                       "params": {"a": {"type": "string", "required": True},
                                  "b": {"type": "integer"}}},
            "local2": {"description": "d2", "params": {"c": "plain hint"}},
            "local3": {"description": "d3"},  # 无 params
        }
        _patch_list_tools(monkeypatch, data)
        mgr = _manager({
            "zz_remote": _mcp("zz_remote", schema={"type": "object"}),
            "local1": _mcp("local1"),  # 与本地同名 → 跳过
        })
        tools = CombinedToolProvider(mgr).get_tools_for_api(core_only=False)
        names = [t["function"]["name"] for t in tools]
        assert names == ["local1", "local2", "local3", "zz_remote"]

        s1 = next(t for t in tools if t["function"]["name"] == "local1")
        assert s1["function"]["parameters"]["required"] == ["a"]
        assert set(s1["function"]["parameters"]["properties"]) == {"a", "b"}

        s2 = next(t for t in tools if t["function"]["name"] == "local2")
        assert s2["function"]["parameters"]["properties"]["c"] == \
            {"type": "string", "description": "plain hint"}

        s3 = next(t for t in tools if t["function"]["name"] == "local3")
        assert s3["function"]["parameters"] == {"type": "object", "properties": {}}

        sm = next(t for t in tools if t["function"]["name"] == "zz_remote")
        assert sm["function"]["description"] == "mcp desc"

    def test_get_tools_for_api_whitelist_filter(self, monkeypatch):
        _patch_list_tools(monkeypatch, {"local1": {"params": {}}, "local2": {"params": {}}})
        mgr = _manager({"zz_remote": _mcp("zz_remote")})
        tools = CombinedToolProvider(mgr).get_tools_for_api(
            tool_whitelist=["local1", "zz_remote"], core_only=False)
        assert {t["function"]["name"] for t in tools} == {"local1", "zz_remote"}

    def test_get_tools_for_api_whitelist_skips_mcp(self, monkeypatch):
        # 白名单中的 MCP 工具被跳过（不在名单 → continue）
        _patch_list_tools(monkeypatch, {"local1": {"params": {}}})
        mgr = _manager({"zz_a": _mcp("zz_a"), "zz_b": _mcp("zz_b")})
        tools = CombinedToolProvider(mgr).get_tools_for_api(
            tool_whitelist=["local1", "zz_a"], core_only=False)
        assert {t["function"]["name"] for t in tools} == {"local1", "zz_a"}

    def test_get_tools_for_api_wildcard(self, monkeypatch):
        _patch_list_tools(monkeypatch, {"local1": {"params": {}}})
        mgr = _manager({"zz_remote": _mcp("zz_remote")})
        tools = CombinedToolProvider(mgr).get_tools_for_api(
            tool_whitelist=["*"], core_only=False)
        assert {t["function"]["name"] for t in tools} == {"local1", "zz_remote"}

    def test_get_tools_for_api_core_only(self, monkeypatch):
        from infra.tool_manager.tool_registry import ToolRegistry
        monkeypatch.setattr(
            ToolRegistry, "get_core_tools_for_api",
            staticmethod(lambda wl: [{"type": "function", "function": {"name": "core1"}}]))
        tools = CombinedToolProvider(_manager()).get_tools_for_api(
            tool_whitelist=["x"], core_only=True)
        assert tools == [{"type": "function", "function": {"name": "core1"}}]


# ====================================================================
# CombinedToolExecutor
# ====================================================================

class TestCombinedToolExecutor:
    @pytest.fixture
    def executor(self):
        return CombinedToolExecutor(_manager())

    # ---------- 本地工具路由 ----------
    def test_execute_local_sync_success(self, executor, monkeypatch):
        def _func(a=1):
            return a * 2
        _patch_get_func(monkeypatch, lambda n: _func)
        r = executor.execute(ToolCallRequest(tool_name="t", params={"a": 4}))
        assert r.success is True
        assert r.result == 8
        assert r.latency_ms >= 0

    def test_execute_local_sync_exception(self, executor, monkeypatch):
        def _bad():
            raise ValueError("local boom")
        _patch_get_func(monkeypatch, lambda n: _bad)
        r = executor.execute(ToolCallRequest(tool_name="t"))
        assert r.success is False
        assert "local boom" in r.error

    def test_execute_local_async_success(self, executor, monkeypatch):
        async def _afunc(x=1):
            await asyncio.sleep(0)
            return x + 1
        _patch_get_func(monkeypatch, lambda n: _afunc)
        r = executor.execute(ToolCallRequest(tool_name="t", params={"x": 1}))
        assert r.success is True
        assert r.result == 2

    def test_execute_local_async_exception(self, executor, monkeypatch):
        async def _abad():
            raise RuntimeError("async boom")
        _patch_get_func(monkeypatch, lambda n: _abad)
        r = executor.execute(ToolCallRequest(tool_name="t"))
        assert r.success is False
        assert "async boom" in r.error

    def test_execute_local_timeout(self, executor, monkeypatch):
        # 本地工具 future 超时（concurrent.futures.TimeoutError 的 str 为空串，
        # 此前会打出 "本地工具执行失败 t: " 空消息错误）
        import infra.mcp.combined_provider as cp

        def _slow():
            return 1

        class _Fut:
            def result(self, timeout=None):
                raise concurrent.futures.TimeoutError()

        class _Pool:
            def submit(self, *a, **k):
                return _Fut()

        _patch_get_func(monkeypatch, lambda n: _slow)
        monkeypatch.setattr(cp, "_get_async_pool", lambda: _Pool())
        r = executor.execute(ToolCallRequest(tool_name="t"))
        assert r.success is False
        assert "工具执行超时" in r.error
        assert r.error != ""

    def test_execute_local_cancelled(self, executor, monkeypatch):
        # 本地工具 future 被取消（上层思考循环 stop/新消息打断）→ 非空、明确的错误
        import infra.mcp.combined_provider as cp

        def _slow():
            return 1

        class _Fut:
            def result(self, timeout=None):
                raise concurrent.futures.CancelledError()

        class _Pool:
            def submit(self, *a, **k):
                return _Fut()

        _patch_get_func(monkeypatch, lambda n: _slow)
        monkeypatch.setattr(cp, "_get_async_pool", lambda: _Pool())
        r = executor.execute(ToolCallRequest(tool_name="t"))
        assert r.success is False
        assert "被取消" in r.error
        assert r.error != ""

    def test_execute_local_exception_empty_str(self, executor, monkeypatch):
        # 异常 str 为空 → 回退到异常类型名，避免空错误信息
        class _Empty(Exception):
            def __str__(self):
                return ""

        def _boom():
            raise _Empty()

        _patch_get_func(monkeypatch, lambda n: _boom)
        r = executor.execute(ToolCallRequest(tool_name="t"))
        assert r.success is False
        assert r.error == "_Empty"

    def test_execute_local_func_disappeared(self, executor, monkeypatch):
        # execute() 已确认存在，但 _execute_local 里二次取 func 为 None（防御分支）
        _patch_get_func(monkeypatch, lambda n: None)
        r = executor._execute_local(ToolCallRequest(tool_name="t"), 0.0)
        assert r.success is False
        assert "工具不存在" in r.error

    # ---------- 不存在 ----------
    def test_execute_not_found(self, executor):
        r = executor.execute(ToolCallRequest(tool_name="zz_nope_123"))
        assert r.success is False
        assert "工具不存在" in r.error

    # ---------- MCP 工具路由 ----------
    def _mcp_executor(self, result):
        mgr = _manager({"zz_remote": _mcp("zz_remote")})
        mgr.call_tool = AsyncMock(return_value=result)
        return CombinedToolExecutor(mgr)

    def test_execute_mcp_success_dict_text(self):
        r = self._mcp_executor({
            "isError": False,
            "content": [{"type": "text", "text": "hello"}, {"type": "image", "data": "x"}],
        }).execute(ToolCallRequest(tool_name="zz_remote"))
        assert r.success is True
        assert r.result == "hello"
        assert r.source == "mcp"
        assert r.error is None

    def test_execute_mcp_success_textcontent_objects(self):
        # content 项为 mcp SDK 对象（非 dict）时走 getattr 分支
        r = self._mcp_executor({
            "isError": False,
            "content": [
                SimpleNamespace(type="text", text="obj-txt"),
                SimpleNamespace(type="resource", text="skip-me"),
            ],
        }).execute(ToolCallRequest(tool_name="zz_remote"))
        assert r.success is True
        assert r.result == "obj-txt"

    def test_execute_mcp_error_from_content_text(self):
        r = self._mcp_executor({
            "isError": True,
            "content": [{"type": "text", "text": "  bad thing  "}],
        }).execute(ToolCallRequest(tool_name="zz_remote"))
        assert r.success is False
        assert r.error == "bad thing"

    def test_execute_mcp_error_field_str(self):
        r = self._mcp_executor({
            "isError": True, "content": [], "error": "err field",
        }).execute(ToolCallRequest(tool_name="zz_remote"))
        assert r.success is False
        assert r.error == "err field"

    def test_execute_mcp_error_field_bytes(self):
        # error 字段为 bytes → strip 后非 str → str() 兜底
        r = self._mcp_executor({
            "isError": True, "content": [], "error": b"raw error",
        }).execute(ToolCallRequest(tool_name="zz_remote"))
        assert r.success is False
        assert r.error == "b'raw error'"

    def test_execute_mcp_error_message_fallback(self):
        r = self._mcp_executor({
            "isError": True, "content": [], "message": "msg fallback",
        }).execute(ToolCallRequest(tool_name="zz_remote"))
        assert r.success is False
        assert r.error == "msg fallback"

    def test_execute_mcp_error_str_result_fallback(self):
        r = self._mcp_executor({
            "isError": True, "content": [],
        }).execute(ToolCallRequest(tool_name="zz_remote"))
        assert r.success is False
        assert r.error is not None

    def test_execute_mcp_timeout(self, monkeypatch):
        import infra.mcp.combined_provider as cp

        class _Fut:
            def result(self, timeout=None):
                raise concurrent.futures.TimeoutError()

        class _Pool:
            def submit(self, *a, **k):
                return _Fut()

        monkeypatch.setattr(cp, "_get_async_pool", lambda: _Pool())
        r = CombinedToolExecutor(_manager({"zz_remote": _mcp("zz_remote")})) \
            .execute(ToolCallRequest(tool_name="zz_remote"))
        assert r.success is False
        assert "超时" in r.error
        assert r.source == "mcp"

    def test_execute_mcp_exception(self, monkeypatch):
        import infra.mcp.combined_provider as cp

        class _Fut:
            def result(self, timeout=None):
                raise ValueError("mcp boom")

        class _Pool:
            def submit(self, *a, **k):
                return _Fut()

        monkeypatch.setattr(cp, "_get_async_pool", lambda: _Pool())
        r = CombinedToolExecutor(_manager({"zz_remote": _mcp("zz_remote")})) \
            .execute(ToolCallRequest(tool_name="zz_remote"))
        assert r.success is False
        assert "mcp boom" in r.error

    def test_execute_mcp_exception_empty_str(self, monkeypatch):
        import infra.mcp.combined_provider as cp

        class _Empty(Exception):
            def __str__(self):
                return ""

        class _Fut:
            def result(self, timeout=None):
                raise _Empty()

        class _Pool:
            def submit(self, *a, **k):
                return _Fut()

        monkeypatch.setattr(cp, "_get_async_pool", lambda: _Pool())
        r = CombinedToolExecutor(_manager({"zz_remote": _mcp("zz_remote")})) \
            .execute(ToolCallRequest(tool_name="zz_remote"))
        assert r.success is False
        assert r.error == f"MCP 工具执行异常: zz_remote"
