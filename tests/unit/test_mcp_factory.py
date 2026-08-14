"""factory.py（MCP 单例工厂）单元测试

mock parse_mcp_servers / MCPServerManager / Combined* / asyncio 边界，覆盖：
- get_server_manager: 创建单例（含锁内二次检查）、复用
- get_mcp_tool_service: 创建单例 / create_task 路径 / RuntimeError 回退 asyncio.run /
  外层启动异常非致命
- shutdown_mcp: 正常关闭 / 异常非致命 / 无 manager
- reset_mcp_tool_service
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import infra.mcp.factory as factory
from infra.mcp.factory import (
    get_mcp_tool_service,
    get_server_manager,
    reset_mcp_tool_service,
    shutdown_mcp,
)


@pytest.fixture(autouse=True)
def _reset_factory_globals():
    factory._service = None
    factory._manager = None
    yield
    factory._service = None
    factory._manager = None


def _patch_service_deps(monkeypatch, fake_mgr):
    """patch 掉 get_mcp_tool_service 内部构造的依赖

    AllowAllToolPermission 是函数内 import（非模块级），不 patch，真实实例无害。
    """
    monkeypatch.setattr(factory, "get_server_manager", lambda: fake_mgr)
    monkeypatch.setattr(factory, "CombinedToolProvider", MagicMock())
    monkeypatch.setattr(factory, "CombinedToolExecutor", MagicMock())
    monkeypatch.setattr(factory, "MCPToolService", MagicMock())


class TestGetServerManager:
    def test_creates_singleton(self, monkeypatch):
        fake_mgr = MagicMock()
        with patch("infra.mcp.server_registry.parse_mcp_servers", return_value=["cfg1"]) as mp, \
             patch.object(factory, "MCPServerManager", return_value=fake_mgr) as mc:
            mgr1 = get_server_manager()
            mgr2 = get_server_manager()
        assert mgr1 is mgr2 is fake_mgr
        mp.assert_called_once_with(factory.settings.MCP_SERVERS)
        mc.assert_called_once_with(["cfg1"])

    def test_lock_recheck_sees_existing_manager(self, monkeypatch):
        """锁内二次检查命中（模拟并发下外层检查通过后已被其它线程初始化）"""
        fake_mgr = MagicMock()

        class _Lock:
            def __enter__(self):
                factory._manager = fake_mgr  # 进入锁后已被其它线程初始化
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(factory, "_manager_lock", _Lock())
        with patch("infra.mcp.server_registry.parse_mcp_servers") as mp:
            assert get_server_manager() is fake_mgr
        mp.assert_not_called()


class TestGetMCPToolService:
    def test_creates_singleton_and_reuses(self, monkeypatch):
        fake_mgr = MagicMock()
        fake_mgr.start_all = AsyncMock(return_value=0)
        _patch_service_deps(monkeypatch, fake_mgr)
        captured = []

        def fake_create_task(coro):
            captured.append(coro)
            coro.close()
            return MagicMock()

        monkeypatch.setattr(asyncio, "create_task", fake_create_task)
        s1 = get_mcp_tool_service()
        s2 = get_mcp_tool_service()
        assert s1 is s2
        assert len(captured) == 1
        factory.CombinedToolProvider.assert_called_once_with(fake_mgr)
        factory.CombinedToolExecutor.assert_called_once_with(fake_mgr)
        factory.MCPToolService.assert_called_once()

    def test_create_task_runtime_error_falls_back_to_run(self, monkeypatch):
        fake_mgr = MagicMock()
        fake_mgr.start_all = AsyncMock(return_value=0)
        _patch_service_deps(monkeypatch, fake_mgr)
        ran = []

        def fake_create_task(coro):
            coro.close()
            raise RuntimeError("no running loop")

        def fake_run(coro):
            ran.append(coro)
            coro.close()
            return None

        monkeypatch.setattr(asyncio, "create_task", fake_create_task)
        monkeypatch.setattr(asyncio, "run", fake_run)
        get_mcp_tool_service()
        assert len(ran) == 1

    def test_start_exception_non_fatal(self, monkeypatch):
        fake_mgr = MagicMock()
        fake_mgr.start_all = AsyncMock(return_value=0)
        _patch_service_deps(monkeypatch, fake_mgr)

        def fake_create_task(coro):
            coro.close()
            raise RuntimeError("no running loop")

        def fake_run(coro):
            coro.close()
            raise ValueError("run boom")

        monkeypatch.setattr(asyncio, "create_task", fake_create_task)
        monkeypatch.setattr(asyncio, "run", fake_run)
        svc = get_mcp_tool_service()  # 外层 except 吞掉异常，不抛出
        assert svc is not None

    def test_lock_recheck_sees_existing_service(self, monkeypatch):
        """锁内二次检查命中（模拟并发下外层检查通过后已被其它线程初始化）"""
        existing = MagicMock()

        class _Lock:
            def __enter__(self):
                factory._service = existing  # 进入锁后已被其它线程初始化
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(factory, "_service_lock", _Lock())
        assert get_mcp_tool_service() is existing


class TestShutdownMCP:
    def test_shutdown_success(self, monkeypatch):
        fake_mgr = MagicMock()
        fake_mgr.shutdown = AsyncMock(return_value=None)

        class _FakeLoop:
            def __init__(self):
                self.ran = []
                self.closed = False

            def run_until_complete(self, coro):
                coro.close()
                self.ran.append(coro)
                return None

            def close(self):
                self.closed = True

        loop = _FakeLoop()
        monkeypatch.setattr(asyncio, "new_event_loop", lambda: loop)
        factory._manager = fake_mgr
        factory._service = object()
        shutdown_mcp()
        assert len(loop.ran) == 1
        fake_mgr.shutdown.assert_called_once()
        assert loop.closed
        assert factory._manager is None
        assert factory._service is None

    def test_shutdown_exception_non_fatal(self, monkeypatch):
        class _FakeLoop:
            def run_until_complete(self, coro):
                coro.close()
                raise RuntimeError("loop boom")

            def close(self):
                pass

        monkeypatch.setattr(asyncio, "new_event_loop", lambda: _FakeLoop())
        factory._manager = MagicMock(shutdown=AsyncMock(return_value=None))
        factory._service = object()
        shutdown_mcp()  # 不抛出
        assert factory._manager is None
        assert factory._service is None

    def test_shutdown_no_manager(self):
        shutdown_mcp()
        assert factory._manager is None
        assert factory._service is None


class TestReset:
    def test_reset_mcp_tool_service(self):
        factory._service = object()
        reset_mcp_tool_service()
        assert factory._service is None
