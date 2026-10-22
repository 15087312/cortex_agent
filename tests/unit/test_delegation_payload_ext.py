"""新增行为覆盖：载荷全量透传 / 自限交集 / 黑板摘要入库。

文档要求：新逻辑必须有测试；异常兜底优先补测试确无法测才豁免。
本文件覆盖本次改动的四处新逻辑，不碰存量文件。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from modules.thinking.core.delegation_port import (
    build_delegation_payload,
    DELEGATE_TASK_SOFT_CAP,
)


def test_payload_passthrough_under_cap():
    task = "x" * 1000
    assert build_delegation_payload(task) == task


def test_payload_no_500_cut():
    """此前 500 字硬切已去掉：2000 字任务必须全量透传"""
    task = "y" * 2000
    assert build_delegation_payload(task) == task


def test_payload_truncates_head_tail_over_cap():
    task = "a" * (DELEGATE_TASK_SOFT_CAP + 500)
    out = build_delegation_payload(task)
    assert len(out) < len(task)
    assert "下级可通过" in out
    assert out.startswith("a" * 100)


def test_delegate_sends_full_task(monkeypatch):
    """派单总线载荷不再是 [:500]：2000 字任务全量发出"""
    from config.settings import Settings
    monkeypatch.setattr(Settings, "get_agent_active", lambda self, role: True)
    import modules.thinking.probes.probe_permission as pp
    import modules.thinking.identity as ident_mod
    import modules.thinking.communication.message_bus as mb
    from modules.thinking.core.delegation_port import (
        DelegationRequest,
        ProbeDelegationAdapter,
    )

    ppm = MagicMock()
    ppm.validate_probe_start.return_value = None
    monkeypatch.setattr(pp, "get_probe_permission_manager", lambda: ppm)
    monkeypatch.setattr(ident_mod, "get_identities", lambda: {"code_writer": {"tier": "expert"}})
    bus = MagicMock()
    bus.send = AsyncMock()
    monkeypatch.setattr(mb, "get_message_bus", lambda: bus)

    task = "z" * 2000
    req = DelegationRequest(
        role="code_writer", task=task, session_id="s12345678",
        caller_tier="large", task_id="t1", wait_seconds=60,
    )
    result = asyncio.run(ProbeDelegationAdapter().delegate(req))
    assert result.success is True
    content = bus.send.call_args.args[0].content
    assert content["task_description"] == task
    assert content["task_full_length"] == 2000


def test_blackboard_write_delegation_stores_summary():
    """入库只存摘要：超 2000 字截断并记录全文长度"""
    from modules.thinking.cognition.blackboard import CognitiveBlackboard

    bb = CognitiveBlackboard(session_id="s", turn_id="t", blackboard_id="b")
    did = bb.write_delegation(role="code_writer", task="w" * 5000)
    d = bb.get_delegation(did)
    assert d is not None
    assert len(d["task"]) < 5000
    assert "全文5000字符" in d["task"]
    assert d["metadata"]["task_full_length"] == 5000


def test_blackboard_write_delegation_short_passthrough():
    from modules.thinking.cognition.blackboard import CognitiveBlackboard

    bb = CognitiveBlackboard(session_id="s", turn_id="t", blackboard_id="b")
    did = bb.write_delegation(role="code_writer", task="short task")
    d = bb.get_delegation(did)
    assert d["task"] == "short task"
    assert d["metadata"]["task_full_length"] == len("short task")


def test_resolve_role_field_and_alias_paths(monkeypatch):
    """role 字段命中与旧别名归一化命中分支"""
    from modules.thinking.core.delegation_port import _resolve_role

    fake = {"x1": {"tier": "expert", "name": "其他", "role": "my_role"}}
    monkeypatch.setattr("modules.thinking.identity.get_identities", lambda: fake)
    assert _resolve_role("my_role") == ("expert", "x1")

    fake2 = {"code_supervisor": {"tier": "supervisor", "name": "其他"}}
    monkeypatch.setattr("modules.thinking.identity.get_identities", lambda: fake2)
    assert _resolve_role("代码主管") == ("supervisor", "code_supervisor")


def test_resolve_role_identities_error_logs(monkeypatch, caplog):
    """身份表读取异常不静默吞：记日志并返回空"""
    import logging

    from modules.thinking.core.delegation_port import _resolve_role

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("modules.thinking.identity.get_identities", _boom)
    with caplog.at_level(logging.WARNING):
        assert _resolve_role("code_writer") is None


def test_rebuild_map_error_logs(monkeypatch, caplog):
    """兼容映射重建失败记日志"""
    import logging

    from modules.thinking.core import delegation_port as dp

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("modules.thinking.identity.get_identities", _boom)
    with caplog.at_level(logging.WARNING):
        dp._rebuild_legacy_map()


def test_delegate_agent_active_check_error_continues(monkeypatch):
    """编排页启停检查异常按启用继续，不阻断派单"""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from config.settings import Settings
    from modules.thinking.core.delegation_port import DelegationRequest, ProbeDelegationAdapter

    def _boom(self, role):
        raise RuntimeError("yaml broken")

    monkeypatch.setattr(Settings, "get_agent_active", _boom)
    import modules.thinking.probes.probe_permission as pp
    import modules.thinking.identity as ident_mod
    import modules.thinking.communication.message_bus as mb

    ppm = MagicMock()
    ppm.validate_probe_start.return_value = None
    monkeypatch.setattr(pp, "get_probe_permission_manager", lambda: ppm)
    monkeypatch.setattr(ident_mod, "get_identities", lambda: {"code_writer": {"tier": "expert"}})
    bus = MagicMock()
    bus.send = AsyncMock()
    monkeypatch.setattr(mb, "get_message_bus", lambda: bus)

    req = DelegationRequest(
        role="code_writer", task="t", session_id="s12345678",
        caller_tier="large", task_id="t1", wait_seconds=60,
    )
    result = asyncio.run(ProbeDelegationAdapter().delegate(req))
    assert result.success is True


def test_delegate_bus_error_returns_failure(monkeypatch):
    """总线发送异常转为失败结果，不抛异常"""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from config.settings import Settings
    from modules.thinking.core.delegation_port import DelegationRequest, ProbeDelegationAdapter

    monkeypatch.setattr(Settings, "get_agent_active", lambda self, role: True)
    import modules.thinking.probes.probe_permission as pp
    import modules.thinking.identity as ident_mod
    import modules.thinking.communication.message_bus as mb

    ppm = MagicMock()
    ppm.validate_probe_start.return_value = None
    monkeypatch.setattr(pp, "get_probe_permission_manager", lambda: ppm)
    monkeypatch.setattr(ident_mod, "get_identities", lambda: {"code_writer": {"tier": "expert"}})
    bus = MagicMock()

    async def _boom(msg):
        raise RuntimeError("bus down")

    bus.send = _boom
    monkeypatch.setattr(mb, "get_message_bus", lambda: bus)

    req = DelegationRequest(
        role="code_writer", task="t", session_id="s12345678",
        caller_tier="large", task_id="t1", wait_seconds=60,
    )
    result = asyncio.run(ProbeDelegationAdapter().delegate(req))
    assert result.success is False
    assert "bus down" in result.error
