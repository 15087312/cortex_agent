"""thinking/core/delegation_port 测试（此前 44% 覆盖）：委托分发适配器"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from modules.thinking.core.delegation_port import (
    DelegationRequest,
    DelegationResult,
    ProbeDelegationAdapter,
    _resolve_role,
)


def _req(**kw):
    d = dict(role="expert", task="分析代码", session_id="s1", caller_tier="large")
    d.update(kw)
    return DelegationRequest(**d)


def _adapter():
    return ProbeDelegationAdapter.__new__(ProbeDelegationAdapter)


def test_dataclass_defaults():
    r = DelegationResult(success=True)
    assert r.probe_id == ""
    assert r.metadata == {}
    req = DelegationRequest(role="expert", task="t")
    assert req.caller_tier == "large"


def test_resolve_role():
    assert _resolve_role("code_writer") is not None
    assert _resolve_role("代码实现专家") is not None
    assert _resolve_role("不存在的角色") is None
    assert _resolve_role("") is None


def test_delegate_unknown_role():
    a = _adapter()
    result = asyncio.run(a.delegate(_req(role="ghost")))
    assert result.success is False
    assert "未知" in result.error


def test_delegate_permission_denied(monkeypatch):
    import modules.thinking.probes.probe_permission as pp
    ppm = MagicMock()
    ppm.validate_probe_start.return_value = "无权限"
    monkeypatch.setattr(pp, "get_probe_permission_manager", lambda: ppm)
    a = _adapter()
    result = asyncio.run(a.delegate(_req(role="expert_code_writer")))
    assert result.success is False
    assert "无权限" in result.error


def test_delegate_success(monkeypatch):
    import modules.thinking.probes.probe_permission as pp
    import modules.thinking.identity as ident_mod
    import modules.thinking.communication.message_bus as mb

    ppm = MagicMock()
    ppm.validate_probe_start.return_value = None
    monkeypatch.setattr(pp, "get_probe_permission_manager", lambda: ppm)
    monkeypatch.setattr(ident_mod, "get_identities", lambda: {"code_writer": {}})
    bus = MagicMock()
    bus.send = AsyncMock()
    monkeypatch.setattr(mb, "get_message_bus", lambda: bus)

    a = _adapter()
    result = asyncio.run(a.delegate(_req(role="expert_code_writer", task_id="t1")))
    assert result.success is True
    assert result.probe_id.startswith("probe_expert")
    assert bus.send.called


def test_delegate_think_timeout_passed(monkeypatch):
    """委托时 wait_seconds 作为 think_timeout 传入 probe_started 消息"""
    import modules.thinking.probes.probe_permission as pp
    import modules.thinking.identity as ident_mod
    import modules.thinking.communication.message_bus as mb

    ppm = MagicMock()
    ppm.validate_probe_start.return_value = None
    monkeypatch.setattr(pp, "get_probe_permission_manager", lambda: ppm)
    monkeypatch.setattr(ident_mod, "get_identities", lambda: {"code_writer": {}})
    bus = MagicMock()
    bus.send = AsyncMock()
    monkeypatch.setattr(mb, "get_message_bus", lambda: bus)

    a = _adapter()
    result = asyncio.run(a.delegate(_req(role="expert_code_writer", task_id="t1", wait_seconds=150)))
    assert result.success is True
    content = bus.send.call_args.args[0].content
    assert content["think_timeout"] == 150


def test_delegate_think_timeout_fallback(monkeypatch):
    """委托未指定 wait_seconds → 使用兜底超时并告警"""
    import modules.thinking.probes.probe_permission as pp
    import modules.thinking.identity as ident_mod
    import modules.thinking.communication.message_bus as mb
    import modules.thinking.core.delegation_port as dp

    ppm = MagicMock()
    ppm.validate_probe_start.return_value = None
    monkeypatch.setattr(pp, "get_probe_permission_manager", lambda: ppm)
    monkeypatch.setattr(ident_mod, "get_identities", lambda: {"code_writer": {}})
    bus = MagicMock()
    bus.send = AsyncMock()
    monkeypatch.setattr(mb, "get_message_bus", lambda: bus)

    a = _adapter()
    result = asyncio.run(a.delegate(_req(role="expert_code_writer", task_id="t1", wait_seconds=None)))
    assert result.success is True
    content = bus.send.call_args.args[0].content
    assert content["think_timeout"] == dp.DEFAULT_DELEGATE_THINK_TIMEOUT
