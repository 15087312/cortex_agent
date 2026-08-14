"""ports.py（抽象端口 Protocol）单元测试

Protocol 方法体是 `...`，import 时不会执行；通过继承且不覆写的方式实例化，
调用父类方法使 `...` 体执行（返回 None），从而覆盖全部协议方法行。
"""
from infra.mcp.ports import (
    ToolEventSinkPort,
    ToolExecutorPort,
    ToolPermissionPort,
    ToolProviderPort,
)


class _Provider(ToolProviderPort):
    pass


class _Executor(ToolExecutorPort):
    pass


class _Permission(ToolPermissionPort):
    pass


class _EventSink(ToolEventSinkPort):
    pass


class TestToolProviderPort:
    def test_list_tools_body_runs(self):
        assert _Provider().list_tools() is None

    def test_get_tool_body_runs(self):
        assert _Provider().get_tool("x") is None

    def test_get_tools_for_api_body_runs(self):
        assert _Provider().get_tools_for_api() is None


class TestToolExecutorPort:
    def test_execute_body_runs(self):
        assert _Executor().execute(None) is None


class TestToolPermissionPort:
    def test_check_body_runs(self):
        assert _Permission().check(None, None) is None


class TestToolEventSinkPort:
    def test_record_body_runs(self):
        assert _EventSink().record(None, None) is None
