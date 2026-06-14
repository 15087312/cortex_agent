"""
MCP 工具系统 — 完整单元测试

覆盖全部模块：
- types.py: ToolSpec, ToolCallRequest, ToolCallResult, MCPServerConfig
- ports.py: Protocol 定义
- tool_service.py: MCPToolService, NullToolEventSink, AllowAllToolPermission
- server_registry.py: MCPServerRegistry, parse_mcp_servers
- in_memory.py: InMemoryMCPToolProvider, InMemoryMCPToolExecutor
- combined_provider.py: CombinedToolProvider, CombinedToolExecutor, ToolManagerPermissionAdapter
- factory.py: get_mcp_tool_service, get_server_manager, shutdown_mcp, reset_mcp_tool_service
- server_manager.py: MCPServerManager
- mcp_tools.py: mcp_discover, mcp_call_tool, mcp_server_status
"""
import pytest
import json
from unittest.mock import MagicMock, patch, AsyncMock, PropertyMock
from typing import Dict, List, Optional


# ====================================================================
# types.py — 数据类
# ====================================================================

class TestMCPServerConfig:
    """MCPServerConfig 数据类"""

    def test_defaults(self):
        from infra.mcp.types import MCPServerConfig
        cfg = MCPServerConfig(name="test")
        assert cfg.name == "test"
        assert cfg.command == ""
        assert cfg.args == []
        assert cfg.env == {}
        assert cfg.enabled is True
        assert cfg.timeout_seconds == 30.0

    def test_frozen(self):
        from infra.mcp.types import MCPServerConfig
        cfg = MCPServerConfig(name="test")
        with pytest.raises(Exception):
            cfg.name = "changed"

    def test_with_all_fields(self):
        from infra.mcp.types import MCPServerConfig
        cfg = MCPServerConfig(
            name="filesystem",
            command="npx",
            args=["-y", "@anthropic/mcp-server-filesystem", "/tmp"],
            env={"KEY": "val"},
            enabled=False,
            timeout_seconds=60.0,
        )
        assert cfg.name == "filesystem"
        assert cfg.command == "npx"
        assert cfg.args == ["-y", "@anthropic/mcp-server-filesystem", "/tmp"]
        assert cfg.env == {"KEY": "val"}
        assert cfg.enabled is False
        assert cfg.timeout_seconds == 60.0


class TestToolSpec:
    """ToolSpec 数据类"""

    def test_defaults(self):
        from infra.mcp.types import ToolSpec
        spec = ToolSpec(name="test")
        assert spec.name == "test"
        assert spec.description == ""
        assert spec.source == "mcp"
        assert spec.risk_level == "LOW"
        assert spec.category == "query"

    def test_to_api_tool(self):
        from infra.mcp.types import ToolSpec
        spec = ToolSpec(
            name="search",
            description="Search tool",
            parameters={"type": "object", "properties": {"q": {"type": "string"}}},
        )
        api = spec.to_api_tool()
        assert api["type"] == "function"
        assert api["function"]["name"] == "search"
        assert api["function"]["description"] == "Search tool"
        assert "parameters" in api["function"]

    def test_to_api_tool_minimal(self):
        from infra.mcp.types import ToolSpec
        spec = ToolSpec(name="x")
        api = spec.to_api_tool()
        assert api["type"] == "function"
        assert isinstance(api["function"]["parameters"], dict)

    def test_to_listing(self):
        from infra.mcp.types import ToolSpec
        spec = ToolSpec(name="test", risk_level="HIGH", category="admin",
                        source="builtin", server_name="local")
        listing = spec.to_listing()
        assert listing["risk_level"] == "HIGH"
        assert listing["category"] == "admin"
        assert listing["source"] == "builtin"
        assert listing["server_name"] == "local"


class TestToolCallRequest:
    """ToolCallRequest 数据类"""

    def test_defaults(self):
        from infra.mcp.types import ToolCallRequest
        req = ToolCallRequest(tool_name="test")
        assert req.tool_name == "test"
        assert req.params == {}
        assert req.caller_role == "expert"
        assert req.timeout == 30.0
        assert req.source == "mcp"

    def test_frozen(self):
        from infra.mcp.types import ToolCallRequest
        req = ToolCallRequest(tool_name="t")
        with pytest.raises(Exception):
            req.tool_name = "x"


class TestToolCallResult:
    """ToolCallResult 数据类"""

    def test_defaults(self):
        from infra.mcp.types import ToolCallResult
        r = ToolCallResult(success=True)
        assert r.success is True
        assert r.result is None
        assert r.error is None
        assert r.latency_ms == 0.0

    def test_to_legacy_dict_success(self):
        from infra.mcp.types import ToolCallResult
        r = ToolCallResult(success=True, result="ok", tool_name="test")
        d = r.to_legacy_dict()
        assert d["success"] is True
        assert d["result"] == "ok"
        assert d["error"] is None

    def test_to_legacy_dict_failure(self):
        from infra.mcp.types import ToolCallResult
        r = ToolCallResult(success=False, error="failed", tool_name="test")
        d = r.to_legacy_dict()
        assert d["success"] is False
        assert d["error"] == "failed"

    def test_to_legacy_dict_defaults(self):
        from infra.mcp.types import ToolCallResult
        r = ToolCallResult(success=True)
        d = r.to_legacy_dict()
        assert d["result"] is None
        assert d["error"] is None


# ====================================================================
# tool_service.py — MCPToolService 门面
# ====================================================================

class TestNullToolEventSink:
    """NullToolEventSink"""

    def test_record_returns_none(self):
        from infra.mcp.tool_service import NullToolEventSink
        from infra.mcp.types import ToolCallRequest, ToolCallResult
        sink = NullToolEventSink()
        req = ToolCallRequest(tool_name="test")
        res = ToolCallResult(success=True)
        assert sink.record(req, res) is None


class TestAllowAllToolPermission:
    """AllowAllToolPermission"""

    def test_check_returns_allowed(self):
        from infra.mcp.tool_service import AllowAllToolPermission
        from infra.mcp.types import ToolCallRequest
        perm = AllowAllToolPermission()
        result = perm.check(ToolCallRequest(tool_name="test"), None)
        assert result["allowed"] is True
        assert isinstance(result["reason"], str)


class TestMCPToolService:
    """MCPToolService 门面"""

    @pytest.fixture
    def make_service(self):
        from infra.mcp.tool_service import MCPToolService, AllowAllToolPermission
        from infra.mcp.in_memory import InMemoryMCPToolProvider, InMemoryMCPToolExecutor
        from infra.mcp.types import ToolSpec

        provider = InMemoryMCPToolProvider()
        provider.register(ToolSpec(
            name="ping", description="ping tool",
            parameters={"type": "object", "properties": {}},
        ))
        executor = InMemoryMCPToolExecutor({"ping": lambda: "pong"})
        permission = AllowAllToolPermission()
        return MCPToolService(provider=provider, executor=executor, permission=permission)

    def test_list_tools(self, make_service):
        tools = make_service.list_tools()
        assert "ping" in tools
        assert len(tools) == 1

    def test_get_tool_exists(self, make_service):
        tool = make_service.get_tool("ping")
        assert tool is not None
        assert tool.name == "ping"

    def test_get_tool_not_exists(self, make_service):
        tool = make_service.get_tool("nonexistent")
        assert tool is None

    def test_get_tools_for_api(self, make_service):
        api = make_service.get_tools_for_api()
        assert len(api) == 1
        assert api[0]["function"]["name"] == "ping"

    def test_get_tools_for_api_with_whitelist(self, make_service):
        api = make_service.get_tools_for_api(tool_whitelist=["ping"])
        assert len(api) == 1

    def test_get_tools_for_api_empty_whitelist(self, make_service):
        api = make_service.get_tools_for_api(tool_whitelist=[])
        # 空 whitelist 不过滤，返回所有工具
        assert len(api) == 1

    def test_execute_success(self, make_service):
        from infra.mcp.types import ToolCallRequest
        result = make_service.execute(ToolCallRequest(tool_name="ping"))
        assert result.success is True
        assert result.result == "pong"

    def test_execute_tool_not_found(self, make_service):
        from infra.mcp.types import ToolCallRequest
        result = make_service.execute(ToolCallRequest(tool_name="ghost"))
        assert result.success is False
        assert "不存在" in result.error

    def test_execute_permission_denied(self):
        from infra.mcp.tool_service import MCPToolService
        from infra.mcp.types import ToolSpec, ToolCallRequest, ToolCallResult
        from infra.mcp.ports import ToolPermissionPort

        class DenyAll(ToolPermissionPort):
            def check(self, req, tool):
                return {"allowed": False, "reason": "测试拒绝"}

        provider = MagicMock()
        provider.get_tool.return_value = ToolSpec(name="secret")
        executor = MagicMock()
        executor.execute.return_value = ToolCallResult(success=True, result="data")

        service = MCPToolService(
            provider=provider, executor=executor,
            permission=DenyAll(),
        )
        result = service.execute(ToolCallRequest(tool_name="secret"))
        assert result.success is False
        assert "拒绝" in result.error
        executor.execute.assert_not_called()

    def test_execute_with_latency_tracking(self, make_service):
        from infra.mcp.types import ToolCallRequest
        result = make_service.execute(ToolCallRequest(tool_name="ping"))
        assert result.latency_ms >= 0

    def test_permission_defaults_to_allow_all(self):
        from infra.mcp.tool_service import MCPToolService, AllowAllToolPermission
        service = MCPToolService(provider=MagicMock(), executor=MagicMock())
        assert isinstance(service.permission, AllowAllToolPermission)

    def test_event_sink_defaults_to_null(self):
        from infra.mcp.tool_service import MCPToolService, NullToolEventSink
        service = MCPToolService(provider=MagicMock(), executor=MagicMock())
        assert isinstance(service.event_sink, NullToolEventSink)

    def test_execute_records_event(self, make_service):
        from infra.mcp.types import ToolCallRequest
        from infra.mcp.tool_service import NullToolEventSink

        recorded = []

        class TrackingSink(NullToolEventSink):
            def record(self, req, res):
                recorded.append((req.tool_name, res.success))

        make_service.event_sink = TrackingSink()
        make_service.execute(ToolCallRequest(tool_name="ping"))
        assert len(recorded) == 1
        assert recorded[0] == ("ping", True)


# ====================================================================
# server_registry.py — MCP Server 配置注册
# ====================================================================

class TestMCPServerRegistry:
    """MCPServerRegistry"""

    def test_empty_registry(self):
        from infra.mcp.server_registry import MCPServerRegistry
        r = MCPServerRegistry()
        assert r.list() == []
        assert r.get("nonexistent") is None

    def test_register_and_get(self):
        from infra.mcp.server_registry import MCPServerRegistry
        from infra.mcp.types import MCPServerConfig
        r = MCPServerRegistry()
        cfg = MCPServerConfig(name="my_server", command="python")
        r.register(cfg)
        assert r.get("my_server") is cfg

    def test_register_empty_name_raises(self):
        from infra.mcp.server_registry import MCPServerRegistry
        from infra.mcp.types import MCPServerConfig
        r = MCPServerRegistry()
        with pytest.raises(ValueError, match="MCP server name is required"):
            r.register(MCPServerConfig(name=""))

    def test_register_duplicate_overwrites(self):
        from infra.mcp.server_registry import MCPServerRegistry
        from infra.mcp.types import MCPServerConfig
        r = MCPServerRegistry()
        r.register(MCPServerConfig(name="s", command="v1"))
        r.register(MCPServerConfig(name="s", command="v2"))
        assert r.get("s").command == "v2"

    def test_list_enabled_only(self):
        from infra.mcp.server_registry import MCPServerRegistry
        from infra.mcp.types import MCPServerConfig
        r = MCPServerRegistry([
            MCPServerConfig(name="enabled_server", enabled=True),
            MCPServerConfig(name="disabled_server", enabled=False),
        ])
        all_servers = r.list()
        assert len(all_servers) == 2
        enabled = r.list(enabled_only=True)
        assert len(enabled) == 1
        assert enabled[0].name == "enabled_server"

    def test_status_format(self):
        from infra.mcp.server_registry import MCPServerRegistry
        from infra.mcp.types import MCPServerConfig
        r = MCPServerRegistry([MCPServerConfig(name="s1", command="cmd", args=["a"], timeout_seconds=60.0)])
        status = r.status()
        assert "s1" in status
        assert status["s1"]["command"] == "cmd"
        assert status["s1"]["args"] == ["a"]
        assert status["s1"]["timeout_seconds"] == 60.0


class TestParseMCPServers:
    """parse_mcp_servers"""

    def test_empty_string(self):
        from infra.mcp.server_registry import parse_mcp_servers
        assert parse_mcp_servers("") == []

    def test_invalid_json(self):
        from infra.mcp.server_registry import parse_mcp_servers
        assert parse_mcp_servers("not json") == []

    def test_none(self):
        from infra.mcp.server_registry import parse_mcp_servers
        assert parse_mcp_servers(None) == []  # type: ignore

    def test_valid_single_server(self):
        from infra.mcp.server_registry import parse_mcp_servers
        result = parse_mcp_servers('{"s1": {"command": "echo", "args": ["hi"]}}')
        assert len(result) == 1
        assert result[0].name == "s1"
        assert result[0].command == "echo"
        assert result[0].args == ["hi"]

    def test_valid_multiple_servers(self):
        from infra.mcp.server_registry import parse_mcp_servers
        result = parse_mcp_servers('{"a": {"command": "x"}, "b": {"command": "y"}}')
        assert len(result) == 2

    def test_disabled_server(self):
        from infra.mcp.server_registry import parse_mcp_servers
        result = parse_mcp_servers('{"s": {"command": "echo", "enabled": false}}')
        assert len(result) == 1
        assert result[0].enabled is False

    def test_empty_config_object(self):
        from infra.mcp.server_registry import parse_mcp_servers
        result = parse_mcp_servers("{}")
        assert result == []

    def test_non_dict_value(self):
        from infra.mcp.server_registry import parse_mcp_servers
        result = parse_mcp_servers('{"s": "not a dict"}')
        assert result == []

    def test_default_enabled(self):
        from infra.mcp.server_registry import parse_mcp_servers
        result = parse_mcp_servers('{"s": {"command": "echo"}}')
        assert result[0].enabled is True

    def test_env_vars(self):
        from infra.mcp.server_registry import parse_mcp_servers
        result = parse_mcp_servers('{"s": {"command": "echo", "env": {"KEY": "VAL"}}}')
        assert result[0].env == {"KEY": "VAL"}

    def test_timeout_parsing(self):
        from infra.mcp.server_registry import parse_mcp_servers
        result = parse_mcp_servers('{"s": {"command": "echo", "timeout_seconds": 60}}')
        assert result[0].timeout_seconds == 60.0


# ====================================================================
# in_memory.py — 内存 Adapter（测试用）
# ====================================================================

class TestInMemoryMCPToolProvider:
    """InMemoryMCPToolProvider"""

    @pytest.fixture
    def provider(self):
        from infra.mcp.in_memory import InMemoryMCPToolProvider
        from infra.mcp.types import ToolSpec
        p = InMemoryMCPToolProvider()
        p.register(ToolSpec(name="a", source="builtin"))
        p.register(ToolSpec(name="b", source="builtin"))
        p.register(ToolSpec(name="c", source="plugin"))
        return p

    def test_list_all(self, provider):
        tools = provider.list_tools()
        assert len(tools) == 3

    def test_list_by_source(self, provider):
        builtin = provider.list_tools(source="builtin")
        assert len(builtin) == 2
        plugin = provider.list_tools(source="plugin")
        assert len(plugin) == 1

    def test_get_tool(self, provider):
        t = provider.get_tool("a")
        assert t is not None
        assert t.name == "a"

    def test_get_tool_not_found(self, provider):
        assert provider.get_tool("z") is None

    def test_empty_provider(self):
        from infra.mcp.in_memory import InMemoryMCPToolProvider
        p = InMemoryMCPToolProvider()
        assert p.list_tools() == {}

    def test_get_tools_for_api(self, provider):
        api = provider.get_tools_for_api()
        assert len(api) == 3

    def test_get_tools_for_api_with_whitelist(self, provider):
        api = provider.get_tools_for_api(tool_whitelist=["a"])
        assert len(api) == 1
        assert api[0]["function"]["name"] == "a"

    def test_get_tools_for_api_wildcard(self, provider):
        api = provider.get_tools_for_api(tool_whitelist=["*"])
        assert len(api) == 3


class TestInMemoryMCPToolExecutor:
    """InMemoryMCPToolExecutor"""

    @pytest.fixture
    def executor(self):
        from infra.mcp.in_memory import InMemoryMCPToolExecutor
        e = InMemoryMCPToolExecutor()
        e.register("ping", lambda: "pong")
        e.register("echo", lambda msg: f"echo: {msg}")
        return e

    def test_execute_sync(self, executor):
        from infra.mcp.types import ToolCallRequest
        r = executor.execute(ToolCallRequest(tool_name="ping"))
        assert r.success is True
        assert r.result == "pong"

    def test_execute_with_args(self, executor):
        from infra.mcp.types import ToolCallRequest
        r = executor.execute(ToolCallRequest(tool_name="echo", params={"msg": "hello"}))
        assert r.success is True
        assert r.result == "echo: hello"

    def test_execute_not_found(self, executor):
        from infra.mcp.types import ToolCallRequest
        r = executor.execute(ToolCallRequest(tool_name="ghost"))
        assert r.success is False
        assert "不存在" in r.error

    def test_execute_type_error(self, executor):
        from infra.mcp.types import ToolCallRequest
        r = executor.execute(ToolCallRequest(tool_name="echo", params={"wrong_arg": "x"}))
        assert r.success is False
        assert "参数错误" in r.error

    def test_execute_generic_exception(self, executor):
        from infra.mcp.in_memory import InMemoryMCPToolExecutor
        from infra.mcp.types import ToolCallRequest

        def broken():
            raise ValueError("boom")

        e = InMemoryMCPToolExecutor({"broken": broken})
        r = e.execute(ToolCallRequest(tool_name="broken"))
        assert r.success is False
        assert "boom" in r.error

    def test_execute_tracks_latency(self, executor):
        from infra.mcp.types import ToolCallRequest
        r = executor.execute(ToolCallRequest(tool_name="ping"))
        assert r.latency_ms >= 0

    def test_empty_executor(self):
        from infra.mcp.in_memory import InMemoryMCPToolExecutor
        from infra.mcp.types import ToolCallRequest
        e = InMemoryMCPToolExecutor()
        r = e.execute(ToolCallRequest(tool_name="x"))
        assert r.success is False


# ====================================================================
# combined_provider.py — 合并 Provider/Executor
# ====================================================================

class TestToolManagerPermissionAdapter:
    """AllowAllToolPermission (replaced ToolManagerPermissionAdapter)"""

    def test_check_returns_dict(self):
        from infra.mcp.tool_service import AllowAllToolPermission
        from infra.mcp.types import ToolCallRequest
        adapter = AllowAllToolPermission()
        result = adapter.check(ToolCallRequest(tool_name="calc"), None)
        assert "allowed" in result
        assert isinstance(result["allowed"], bool)
        assert result["allowed"] is True


class TestCombinedToolProvider:
    """CombinedToolProvider"""

    @pytest.fixture
    def provider(self):
        from infra.mcp.combined_provider import CombinedToolProvider
        from infra.mcp.server_manager import MCPServerManager
        return CombinedToolProvider(MCPServerManager([]))

    def test_list_tools_has_local(self, provider):
        tools = provider.list_tools()
        assert len(tools) > 50
        assert "read_file" in tools

    def test_list_tools_source_filter(self, provider):
        builtin = provider.list_tools(source="builtin")
        assert len(builtin) > 50

    def test_get_tool(self, provider):
        t = provider.get_tool("read_file")
        assert t is not None
        assert t.name == "read_file"
        assert t.source == "builtin"

    def test_get_tool_nonexistent(self, provider):
        assert provider.get_tool("zzz_nonexistent_999") is None

    def test_get_tools_for_api_core(self, provider):
        tools = provider.get_tools_for_api(
            tool_whitelist=["read_file", "web_search"],
            core_only=True,
        )
        assert isinstance(tools, list)

    def test_get_tools_for_api_noncore(self, provider):
        from infra.tool_manager.tool_registry import ToolRegistry
        tools = provider.get_tools_for_api(core_only=False)
        assert len(tools) == len(ToolRegistry.list_tools())

    def test_get_tools_for_api_whitelist(self, provider):
        tools = provider.get_tools_for_api(
            tool_whitelist=["read_file", "calc"],
            core_only=False,
        )
        names = [t["function"]["name"] for t in tools]
        assert set(names) == {"read_file", "calc"}

    def test_get_tools_for_api_empty_whitelist(self, provider):
        tools = provider.get_tools_for_api(tool_whitelist=[], core_only=False)
        assert len(tools) > 0  # 空 whitelist 不过滤

    def test_get_tools_for_api_wildcard(self, provider):
        tools = provider.get_tools_for_api(tool_whitelist=["*"], core_only=False)
        assert len(tools) > 50


class TestCombinedToolExecutor:
    """CombinedToolExecutor"""

    @pytest.fixture
    def executor(self):
        from infra.mcp.combined_provider import CombinedToolExecutor
        from infra.mcp.server_manager import MCPServerManager
        return CombinedToolExecutor(MCPServerManager([]))

    def test_execute_mcp_tool_no_connection(self, executor):
        from infra.mcp.types import ToolCallRequest
        from infra.mcp.transport import MCPToolDef
        executor._server_manager._tools_index["remote_tool"] = MCPToolDef(
            name="remote_tool", server_name="nonexistent",
        )
        r = executor.execute(ToolCallRequest(tool_name="remote_tool"))
        assert r.success is False

    def test_execute_nonexistent_local(self, executor):
        from infra.mcp.types import ToolCallRequest
        r = executor.execute(ToolCallRequest(tool_name="does_not_exist_999"))
        assert r.success is False
        assert "不存在" in r.error

    def test_execute_async_tool(self, executor):
        from infra.mcp.types import ToolCallRequest
        r = executor.execute(ToolCallRequest(tool_name="list_learned_tools"))
        assert r.success is True

    def test_execute_sync_tool(self, executor):
        from infra.mcp.types import ToolCallRequest
        r = executor.execute(ToolCallRequest(tool_name="list_learned_tools"))
        assert r.latency_ms >= 0

    def test_execute_tracks_tool_name(self, executor):
        from infra.mcp.types import ToolCallRequest
        r = executor.execute(ToolCallRequest(tool_name="list_learned_tools"))
        assert r.tool_name == "list_learned_tools"


# ====================================================================
# factory.py — 工厂函数
# ====================================================================

class TestMCPFactory:
    """工厂函数"""

    def setup_method(self):
        from infra.mcp.factory import reset_mcp_tool_service
        reset_mcp_tool_service()

    def test_get_mcp_tool_service_singleton(self):
        from infra.mcp.factory import get_mcp_tool_service
        s1 = get_mcp_tool_service()
        s2 = get_mcp_tool_service()
        assert s1 is s2

    def test_get_server_manager_singleton(self):
        from infra.mcp.factory import get_server_manager
        m1 = get_server_manager()
        m2 = get_server_manager()
        assert m1 is m2

    def test_reset_creates_new_service(self):
        from infra.mcp.factory import get_mcp_tool_service, reset_mcp_tool_service
        s1 = get_mcp_tool_service()
        reset_mcp_tool_service()
        s2 = get_mcp_tool_service()
        assert s1 is not s2

    def test_service_is_mcp_tool_service(self):
        from infra.mcp.factory import get_mcp_tool_service
        from infra.mcp.tool_service import MCPToolService
        assert isinstance(get_mcp_tool_service(), MCPToolService)

    def test_service_has_provider_executor_permission(self):
        from infra.mcp.factory import get_mcp_tool_service
        s = get_mcp_tool_service()
        assert s.provider is not None
        assert s.executor is not None
        assert s.permission is not None

    def test_shutdown_mcp_clears_globals(self):
        from infra.mcp.factory import get_mcp_tool_service, shutdown_mcp
        get_mcp_tool_service()
        shutdown_mcp()
        from infra.mcp.factory import get_mcp_tool_service
        # 重置后应创建新实例
        s = get_mcp_tool_service()
        assert s is not None

    def test_shutdown_mcp_twice(self):
        from infra.mcp.factory import shutdown_mcp
        shutdown_mcp()
        shutdown_mcp()  # 不应报错


# ====================================================================
# server_manager.py — MCP Server 管理器
# ====================================================================

class TestMCPServerManager:
    """MCPServerManager"""

    def test_empty_manager(self):
        from infra.mcp.server_manager import MCPServerManager
        mgr = MCPServerManager([])
        assert mgr.get_all_tools() == {}
        assert mgr.get_server_status() == []
        assert mgr.get_tool("any") is None

    def test_get_server_for_tool_none(self):
        from infra.mcp.server_manager import MCPServerManager
        mgr = MCPServerManager([])
        assert mgr.get_server_for_tool("any") is None

    def test_shutdown_empty(self):
        import asyncio
        from infra.mcp.server_manager import MCPServerManager
        mgr = MCPServerManager([])
        asyncio.run(mgr.shutdown())  # 不应报错

    def test_start_all_empty(self):
        import asyncio
        from infra.mcp.server_manager import MCPServerManager
        mgr = MCPServerManager([])
        count = asyncio.run(mgr.start_all())
        assert count == 0

    def test_get_server_status_empty(self):
        from infra.mcp.server_manager import MCPServerManager
        mgr = MCPServerManager([])
        assert mgr.get_server_status() == []

    def test_call_tool_no_server(self):
        import asyncio
        from infra.mcp.server_manager import MCPServerManager
        mgr = MCPServerManager([])
        r = asyncio.run(mgr.call_tool("any_tool"))
        assert r.get("isError") is True


# ====================================================================
# mcp_tools.py — 注册在 ToolRegistry 中的工具
# ====================================================================

class TestMCPDiscoverTool:
    """mcp_discover"""

    def test_discover_no_servers(self):
        from infra.tool_manager.tools.mcp_tools import mcp_discover
        r = mcp_discover()
        assert r["success"] is True
        # 可能为 0（无 MCP 配置）或正数（有 MCP 配置）
        assert r["total_servers"] >= 0

    def test_discover_tool_registered(self):
        from infra.tool_manager.tool_registry import ToolRegistry
        t = ToolRegistry.get_tool("mcp_discover")
        assert t is not None
        assert t.risk_level == "LOW"
        assert t.category == "query"


class TestMCPCallTool:
    """mcp_call_tool"""

    def test_empty_tool_name(self):
        from infra.tool_manager.tools.mcp_tools import mcp_call_tool
        r = mcp_call_tool(tool="")
        assert r["success"] is False

    def test_bad_json_params(self):
        from infra.tool_manager.tools.mcp_tools import mcp_call_tool
        r = mcp_call_tool(tool="test", params="not-json")
        assert r["success"] is False
        assert "JSON" in r["error"]

    def test_tool_registered(self):
        from infra.tool_manager.tool_registry import ToolRegistry
        t = ToolRegistry.get_tool("mcp_call_tool")
        assert t is not None
        assert t.risk_level == "MEDIUM"


class TestMCPServerStatusTool:
    """mcp_server_status"""

    def test_status_no_servers(self):
        from infra.tool_manager.tools.mcp_tools import mcp_server_status
        r = mcp_server_status()
        assert r["success"] is True
        assert r["total"] >= 0

    def test_tool_registered(self):
        from infra.tool_manager.tool_registry import ToolRegistry
        t = ToolRegistry.get_tool("mcp_server_status")
        assert t is not None
        assert t.risk_level == "LOW"


# ====================================================================
# transport.py — 传输层（mock MCP SDK）
# ====================================================================

class TestMCPToolDef:
    """MCPToolDef 数据类"""

    def test_defaults(self):
        from infra.mcp.transport import MCPToolDef
        d = MCPToolDef(name="test")
        assert d.name == "test"
        assert d.description == ""
        assert d.input_schema == {"type": "object", "properties": {}}
        assert d.server_name == ""

    def test_full(self):
        from infra.mcp.transport import MCPToolDef
        d = MCPToolDef(name="x", description="desc", server_name="srv")
        assert d.description == "desc"
        assert d.server_name == "srv"


@pytest.mark.skipif("not __import__('importlib').util.find_spec('mcp')",
                    reason="mcp 包未安装")
@pytest.mark.asyncio
class TestMCPStdioTransportMocked:
    """MCPStdioTransport（mock mcp SDK）"""

    @pytest.fixture
    def transport(self):
        from infra.mcp.transport import MCPStdioTransport
        return MCPStdioTransport(
            server_name="test_server",
            command="echo",
            args=["hi"],
            timeout=5.0,
        )

    async def test_connect_failure_cleanup(self, transport):
        """连接失败应清理资源"""
        with patch("mcp.client.stdio.stdio_client") as mock_sc:
            mock_sc.return_value.__aenter__.side_effect = Exception("conn failed")
            ok = await transport.connect()
            assert ok is False
            assert transport.is_connected is False

    async def test_list_tools_not_connected(self, transport):
        tools = await transport.list_tools()
        assert tools == []

    async def test_call_tool_not_connected(self, transport):
        r = await transport.call_tool("test")
        assert r.get("isError") is True
        assert "未连接" in r.get("content", [{}])[0].get("text", "")

    async def test_close_twice(self, transport):
        await transport.close()
        await transport.close()  # 不应报错

    async def test_is_connected_property(self, transport):
        assert transport.is_connected is False
        transport._connected = True
        assert transport.is_connected is True


@pytest.mark.skipif("not __import__('importlib').util.find_spec('mcp')",
                    reason="mcp 包未安装")
@pytest.mark.asyncio
class TestMCPSseTransportMocked:
    """MCPSseTransport（mock mcp SDK）"""

    @pytest.fixture
    def transport(self):
        from infra.mcp.transport import MCPSseTransport
        return MCPSseTransport(server_name="sse_test", url="http://localhost:9999/sse")

    async def test_connect_failure(self, transport):
        with patch("mcp.client.sse.sse_client") as mock_sse:
            mock_sse.return_value.__aenter__.side_effect = Exception("SSE failed")
            ok = await transport.connect()
            assert ok is False
            assert transport.is_connected is False

    async def test_list_tools_not_connected(self, transport):
        assert await transport.list_tools() == []

    async def test_call_tool_not_connected(self, transport):
        r = await transport.call_tool("test")
        assert r.get("isError") is True

    async def test_close_twice(self, transport):
        await transport.close()
        await transport.close()

    async def test_is_connected_property(self, transport):
        assert transport.is_connected is False
        transport._connected = True
        assert transport.is_connected is True
