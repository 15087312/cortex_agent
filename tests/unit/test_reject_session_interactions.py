"""前端断开 → 取消会话待交互 future（MEDIUM-1 泄漏/全局冻结修复回归）。

ask_user_intent 等交互经 _wait_for_user_response 创建 future，若无人 resolve
会永久挂起且 Suspension 计时冻结；WS 断开时 reject_session_user_responses
批量返回取消结果，只影响该会话。
"""
import asyncio
import types

import pytest

import modules.thinking.core.model_runner as mr


class _FakeRunner:
    def __init__(self, loop):
        self._pending_user_responses = {}
        self._fut = loop.create_future()
        self._pending_user_responses["user_intent_request_abc123"] = self._fut


def test_reject_session_user_responses(monkeypatch):
    loop = asyncio.new_event_loop()
    runner = _FakeRunner(loop)
    mgr = types.SimpleNamespace(_runners={"large_primary": runner})
    monkeypatch.setitem(mr._runner_managers, "ses_target", mgr)
    try:
        # 只清理目标会话
        assert mr.reject_session_user_responses("ses_target") == 1
        assert runner._fut.done()
        result = runner._fut.result()
        assert result.get("cancelled") is True

        # 其他会话不受影响 / 无会话无操作
        assert mr.reject_session_user_responses("ses_other") == 0
        assert mr.reject_session_user_responses("") == 0
    finally:
        loop.close()
        mr._runner_managers.pop("ses_target", None)


def test_reject_skips_already_done_future(monkeypatch):
    loop = asyncio.new_event_loop()
    runner = _FakeRunner(loop)
    runner._fut.set_result({"answer": "提前返回"})  # 已完成的 future 不应重复 set_result
    mgr = types.SimpleNamespace(_runners={"large_primary": runner})
    monkeypatch.setitem(mr._runner_managers, "ses_done", mgr)
    try:
        assert mr.reject_session_user_responses("ses_done") == 0
        assert runner._fut.done()
        assert runner._fut.result()["answer"] == "提前返回"
    finally:
        loop.close()
        mr._runner_managers.pop("ses_done", None)


@pytest.mark.asyncio
async def test_ask_user_intent_handles_cancelled():
    """_handle_ask_user_intent 对 cancelled 结果给出明确提示（不抛、不空答）"""
    from modules.thinking.core.model_runner import ModelRunner
    from unittest.mock import MagicMock

    inst = MagicMock()
    inst.identity.model_id = "large_primary"
    inst.identity.tier = "large"
    inst.identity.role = "orchestrator"
    runner = ModelRunner.__new__(ModelRunner)
    runner.model_id = "large_primary"
    runner.tier = "large"
    runner.session_id = "ses_c"
    runner.identity = inst.identity
    runner.blackboard = None
    runner._task_description = ""

    async def fake_wait(event_type, event_data, timeout=None):
        return {"response": "前端连接已断开，交互已自动取消", "cancelled": True}

    runner._wait_for_user_response = fake_wait
    out = await runner._handle_ask_user_intent("继续吗?", ["是", "否"], "")
    assert "连接已断开" in out
    assert "继续吗?" in out
