"""ModelRunner 方法测试（此前 14 个方法零覆盖）：参数验证/guard prompt/prompt 构建/交互等待"""
import asyncio
from unittest.mock import MagicMock, AsyncMock

import pytest

import modules.thinking.core.model_runner as mr_mod
from modules.thinking.core.model_runner import ModelRunner


def _make_runner(monkeypatch, tier="large", tool_names=None):
    r = ModelRunner.__new__(ModelRunner)
    ident = MagicMock()
    ident.name = "总指挥"
    ident.tier = tier
    ident.role = "orchestrator"
    ident.personality = "你是总指挥"
    ident.expertise = ["规划"]
    ident.weaknesses = ["写代码"]
    r.identity = ident
    r.tier = tier
    r._task_description = "测试任务"
    r._active_skill = None
    r.session_id = "s1"
    r.model_id = "m1"
    r._visible_tool_whitelist = lambda: tool_names if tool_names is not None else []
    r.logger = MagicMock()
    return r


# ── _has_required_tool_args / _missing_required_tool_args ───────────────────

def test_has_required_tool_args_all_present():
    r = _make_runner(None)
    assert r._has_required_tool_args("calc", {"a": 1, "op": "+", "b": 2}) is True


def test_has_required_tool_args_missing():
    r = _make_runner(None)
    assert r._has_required_tool_args("calc", {"a": 1}) is False


def test_has_required_tool_args_empty_value():
    r = _make_runner(None)
    assert r._has_required_tool_args("calc", {"a": 1, "op": "", "b": 2}) is False


def test_has_required_tool_args_unknown_tool():
    r = _make_runner(None)
    assert r._has_required_tool_args("不存在工具", {}) is True


def test_missing_required_tool_args():
    r = _make_runner(None)
    missing = r._missing_required_tool_args("calc", {"a": 1})
    assert set(missing) == {"op", "b"}


def test_missing_required_tool_args_none_for_unknown():
    r = _make_runner(None)
    assert r._missing_required_tool_args("不存在工具", {}) == []


# ── _build_tool_guard_prompt ────────────────────────────────────────────────

def test_guard_prompt_simplified_few_tools():
    r = _make_runner(None, tier="expert", tool_names=["calc", "read_file"])
    prompt = r._build_tool_guard_prompt()
    assert "禁止无参调用工具" in prompt
    assert "委托" not in prompt  # 极简版无委托规则


def test_guard_prompt_detailed_many_tools():
    r = _make_runner(None, tier="large", tool_names=[f"tool_{i}" for i in range(15)])
    prompt = r._build_tool_guard_prompt()
    assert "工具调用硬性规则" in prompt
    assert "必填参数" in prompt or "delegate_task" in prompt


def test_guard_prompt_large_tier_detailed_even_few_tools():
    r = _make_runner(None, tier="large", tool_names=["calc"])
    prompt = r._build_tool_guard_prompt()
    assert "工具调用硬性规则" in prompt  # large 层级强制详细


# ── _build_tool_prompt_section ──────────────────────────────────────────────

def test_tool_prompt_section_empty():
    r = _make_runner(None)
    assert r._build_tool_prompt_section() == ""


# ── _build_prompt ───────────────────────────────────────────────────────────

def test_build_prompt_contains_identity():
    r = _make_runner(None)
    r._visible_tool_whitelist = lambda: []
    out = r._build_prompt(guidance="指引", dialog_context="【对话】...", expert_context="")
    assert "测试任务" in out
    assert "总指挥" in out
    assert "擅长: 规划" in out
    assert "指引" in out


def test_build_prompt_with_skill_large():
    r = _make_runner(None, tier="large")
    skill = MagicMock()
    skill.name = "代码专家"
    skill.description = "负责写代码"
    r._active_skill = skill
    out = r._build_prompt(guidance="", dialog_context="", expert_context="")
    assert "代码专家" in out


def test_build_prompt_no_skill_for_expert():
    r = _make_runner(None, tier="expert")
    skill = MagicMock()
    skill.name = "代码专家"
    r._active_skill = skill
    out = r._build_prompt(guidance="", dialog_context="", expert_context="")
    assert "代码专家" not in out  # 技能叠加仅 large 层级


# ── 用户交互等待（future + resolve）────────────────────────────────────────

def test_resolve_user_response_unknown_id_no_error():
    r = _make_runner(None)
    r._pending_user_responses = {}
    r.resolve_user_response("不存在", {"response": "x"})  # 不抛
    assert r._pending_user_responses == {}  # 无 pending 变化


@pytest.mark.asyncio
async def test_wait_for_user_response_and_resolve(monkeypatch):
    r = _make_runner(None)
    r.session_id = "s1"
    import modules.security_system.tool_security_gate as tsg
    monkeypatch.setattr(tsg, "_emit_security_event", lambda *a, **k: None)
    import modules.thinking.communication.message_bus as mb
    bus = MagicMock()
    bus.broadcast = AsyncMock()
    monkeypatch.setattr(mb, "get_message_bus", lambda: bus)
    r._pending_user_responses = {}

    task = asyncio.create_task(
        r._wait_for_user_response("user_intent_request", {"question": "选哪个", "action": "user_intent_request"})
    )
    await asyncio.sleep(0.05)
    rid = next(iter(r._pending_user_responses))
    r.resolve_user_response(rid, {"response": "选项A"})
    result = await asyncio.wait_for(task, timeout=2)
    assert result["response"] == "选项A"
    assert rid not in r._pending_user_responses  # 清理


@pytest.mark.asyncio
async def test_wait_for_user_response_timeout(monkeypatch):
    r = _make_runner(None)
    r.model_id = "m1"
    import modules.security_system.tool_security_gate as tsg
    monkeypatch.setattr(tsg, "_emit_security_event", lambda *a, **k: None)
    import modules.thinking.communication.message_bus as mb
    bus = MagicMock()
    bus.broadcast = AsyncMock()
    monkeypatch.setattr(mb, "get_message_bus", lambda: bus)
    r._pending_user_responses = {}
    result = await r._wait_for_user_response("user_intent_request", {"question": "q"}, timeout=0.1)
    assert result.get("timeout") is True


@pytest.mark.asyncio
async def test_handle_ask_user_intent(monkeypatch):
    r = _make_runner(None)
    r.model_id = "m1"
    import modules.security_system.tool_security_gate as tsg
    monkeypatch.setattr(tsg, "_emit_security_event", lambda *a, **k: None)
    import modules.thinking.communication.message_bus as mb
    bus = MagicMock()
    bus.broadcast = AsyncMock()
    monkeypatch.setattr(mb, "get_message_bus", lambda: bus)
    r._pending_user_responses = {}

    task = asyncio.create_task(r._handle_ask_user_intent("选哪个", ["A", "B"], "上下文"))
    await asyncio.sleep(0.05)
    rid = next(iter(r._pending_user_responses))
    r.resolve_user_response(rid, {"answer": "B"})
    result = await asyncio.wait_for(task, timeout=2)
    assert "B" in result


@pytest.mark.asyncio
async def test_handle_mode_change_approved(monkeypatch):
    from config.settings import settings as cfg
    orig_mode = cfg.EXECUTION_MODE
    try:
        r = _make_runner(None)
        r.model_id = "m1"
        import modules.security_system.tool_security_gate as tsg
        from modules.security_system.tool_security_gate import ToolSecurityGate
        ToolSecurityGate._pending_reviews.clear()
        monkeypatch.setattr(tsg, "_emit_security_event", lambda *a, **k: None)
        monkeypatch.setattr(cfg, "EXECUTION_MODE", "plan")

        task = asyncio.create_task(r._handle_mode_change_request("需要写代码", "edit"))
        await asyncio.sleep(0.05)
        rid = next(iter(ToolSecurityGate._pending_reviews))
        ToolSecurityGate.resolve_review(rid, True, "用户批准")
        result = await asyncio.wait_for(task, timeout=2)
        assert "同意切换到" in result
        assert cfg.EXECUTION_MODE == "edit"
    finally:
        object.__setattr__(cfg, "EXECUTION_MODE", orig_mode)


@pytest.mark.asyncio
async def test_handle_mode_change_rejected(monkeypatch):
    from config.settings import settings as cfg
    orig_mode = cfg.EXECUTION_MODE
    try:
        r = _make_runner(None)
        r.model_id = "m1"
        import modules.security_system.tool_security_gate as tsg
        from modules.security_system.tool_security_gate import ToolSecurityGate
        ToolSecurityGate._pending_reviews.clear()
        monkeypatch.setattr(tsg, "_emit_security_event", lambda *a, **k: None)

        task = asyncio.create_task(r._handle_mode_change_request("原因", "edit"))
        await asyncio.sleep(0.05)
        rid = next(iter(ToolSecurityGate._pending_reviews))
        ToolSecurityGate.resolve_review(rid, False, "用户拒绝")
        result = await asyncio.wait_for(task, timeout=2)
        assert "拒绝" in result
    finally:
        object.__setattr__(cfg, "EXECUTION_MODE", orig_mode)


# ── 上下文格式化 / 引导消费 / 消息检查 / 唤醒 ───────────────────────────────

def test_format_messages_for_context_chatmessage():
    from infra.model.base_model import ChatMessage
    msgs = [ChatMessage(role="user", content="你好"), ChatMessage(role="assistant", content="在的")]
    out = ModelRunner._format_messages_for_context(msgs)
    assert "[user]: 你好" in out
    assert "[assistant]: 在的" in out


def test_format_messages_for_context_dicts():
    msgs = [{"role": "user", "content": "测试"}, {"role": "assistant", "content": {"action": "thinking_result", "result": "结论"}}]
    out = ModelRunner._format_messages_for_context(msgs)
    assert "[user]: 测试" in out
    assert "结论" in out  # thinking_result 提取 result


def test_format_messages_for_context_empty():
    assert ModelRunner._format_messages_for_context([]) == ""


def test_format_messages_for_context_limits():
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(30)]
    out = ModelRunner._format_messages_for_context(msgs)
    assert out.count("[user]") == 20  # 最近 20 条


def test_consume_guidance_forwards_to_thinker():
    r = _make_runner(None)
    r._pending_guidance = ["引导A", "引导B"]
    thinker = MagicMock()
    r._thinker = thinker
    got = r._consume_guidance()
    assert got == ["引导A", "引导B"]
    assert r._pending_guidance == []
    thinker.add_external_prompt.assert_called_once_with("引导A\n\n引导B")


def test_consume_guidance_empty():
    r = _make_runner(None)
    r._pending_guidance = []
    assert r._consume_guidance() == []


@pytest.mark.asyncio
async def test_check_messages(monkeypatch):
    import modules.thinking.communication.interface as iface_mod
    bus = MagicMock()
    msg = MagicMock()
    msg.sender = "expert_001"
    msg.content = "结果"
    msg.msg_type = MagicMock()
    msg.msg_type.value = "expert_result"
    bus.receive = AsyncMock(return_value=[msg])
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)
    r = _make_runner(None)
    r.model_id = "large_001"
    out = await r._check_messages()
    assert out[0]["sender"] == "expert_001"
    assert out[0]["content"] == "结果"
    assert out[0]["msg_type"] == "expert_result"


def test_on_wakeup_message_sets_event():
    r = _make_runner(None)
    import asyncio
    ev = asyncio.Event()
    r._wakeup_event = ev
    r._on_wakeup_message()
    assert ev.is_set()
