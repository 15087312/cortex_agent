"""model_runner 深度补测：覆盖 60%→75%+

未覆盖段（coverage 定位）：_wait_for_wakeup_event / _collect_expert_progress /
_build_runner_prompt / 系统提示词构建 / 时间上下文 / guard prompt / 参数验证异常 /
_generate_with_tools 的流式、模式变更、控制工具、委托、create_supervisor、安全门控、
工具摘要、web 包裹、最大轮次、重试；Manager 启动细节（禁用/容量/技能注入/异常路径）、
_listen_loop、drain JSON 解析等。

mock 边界：LLM client（chat / chat_stream）、MCP、ToolPermissionController、
ToolSecurityGate、MessageBus、ContinuousThinker、delegation_port —— 绝不真实调用 LLM。
"""
import asyncio
import sys
import time as _time
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

import modules.thinking.core.model_runner as mr_mod
from infra.mcp.types import ToolCallResult
from infra.model.base_model import ChatMessage, ChatResponse, ToolCall
from modules.thinking.core.model_runner import (
    ModelRunner,
    ModelRunnerManager,
    get_runner_manager,
    remove_runner_manager,
    reject_session_user_responses,
)


# ── 通用构造 ───────────────────────────────────────────────────────────────

def _runner(**kw):
    inst = MagicMock()
    ident = MagicMock()
    ident.model_id = kw.get("model_id", "large_primary")
    ident.tier = kw.get("tier", "large")
    ident.name = "总指挥"
    ident.role = "orchestrator"
    ident.personality = "你是总指挥"
    ident.expertise = ["规划"]
    ident.weaknesses = ["写代码"]
    ident.default_skill = ""
    ident.metadata = {}
    inst.identity = ident
    r = ModelRunner.__new__(ModelRunner)
    r.instance = inst
    r.identity = ident
    r.model_id = ident.model_id
    r.tier = ident.tier
    r.blackboard = kw.get("blackboard") if "blackboard" in kw else MagicMock()
    r.turn_context = kw.get("turn_context", None)
    r.session_id = kw.get("session_id", "s1")
    r.manager = kw.get("manager", None)
    r._running = False
    r._task = None
    r._task_description = kw.get("task_description", "任务")
    r._task_id = "task_t1"
    r._return_to_model_id = ""
    r._return_to_session_id = "s1"
    r._started_at = 0.0
    r._status = "idle"
    r._status_detail = ""
    r._react_loop = None
    r._think_loop_state = None
    r._pending_guidance = []
    r._thinker = None
    r._active_skill = None
    r._active_skill_tool_rules = None
    r._wakeup_event = None
    r._last_known_mode = ""
    r._current_streaming_content = ""
    r._pending_user_responses = {}
    r.MAX_CHAT_TOOL_TURNS = 25
    r.GENERATE_RETRIES = 2
    r.GENERATE_RETRY_DELAY = 0.01
    r.THINK_TIMEOUT = 60.0
    r.logger = MagicMock()
    return r


def _mgr(monkeypatch=None):
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    bus.receive = AsyncMock(return_value=[])
    if monkeypatch is not None:
        import modules.thinking.communication.interface as iface_mod
        monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)
    m = ModelRunnerManager.__new__(ModelRunnerManager)
    m.blackboard = MagicMock()
    m.turn_context = None
    m.session_id = "s1"
    m._channel = "model_runner_manager_s1"
    m._runners = {}
    m._count_by_tier = {"large": 0, "supervisor": 0, "expert": 0}
    m._lock = MagicMock()
    m._lock.__enter__ = MagicMock(return_value=None)
    m._lock.__exit__ = MagicMock(return_value=False)
    m._probe_map = {}
    m._bus = bus
    m._listen_task = None
    m._running = False
    m._message_event = asyncio.Event()
    m._orphan_event = asyncio.Event()
    return m


class FakeEvent:
    """同步 event：wait 立即返回，供 _wait_for_wakeup_event 的 run_in_executor 使用"""
    def __init__(self, signaled=False):
        self._signaled = signaled
    def wait(self, timeout=None):
        return self._signaled
    def set(self):
        self._signaled = True
    def clear(self):
        self._signaled = False


def _fake_bus_port(monkeypatch, receive_return=None):
    import modules.thinking.communication.interface as iface_mod
    bus = MagicMock()
    bus.receive = AsyncMock(return_value=[] if receive_return is None else receive_return)
    bus.send = AsyncMock()
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)
    return bus


def _fake_mcp(monkeypatch, tools=None, control_tools=None):
    import infra.mcp.factory as mcp_mod
    mcp = MagicMock()
    mcp.get_tools_for_api.return_value = tools if tools is not None else [
        {"function": {"name": "calc", "description": "计算"}}
    ]
    mcp.execute.return_value = ToolCallResult(success=True, result="2")
    monkeypatch.setattr(mcp_mod, "get_mcp_tool_service", lambda: mcp)
    import modules.security_system.tool_permission_controller as tpc
    ctrl = MagicMock()
    ctrl.get_control_tools.return_value = control_tools if control_tools is not None else []
    monkeypatch.setattr(tpc, "get_tool_permission_controller", lambda: ctrl)
    return mcp


def _fake_compression(monkeypatch, engine=None):
    if engine is None:
        engine = MagicMock()
        engine.estimate_tokens.return_value = 123
    fake_mod = types.ModuleType("modules.thinking.context.compression")
    fake_mod.get_compression_engine = lambda: engine
    fake_mod.CompressionEngine = type("CompressionEngine", (), {})  # context/__init__ 导入需要
    monkeypatch.setitem(sys.modules, "modules.thinking.context.compression", fake_mod)
    return engine


def _fake_gate(monkeypatch, allowed=(True, "")):
    gate = MagicMock()
    gate.check = AsyncMock(return_value=allowed)
    monkeypatch.setattr(mr_mod, "get_tool_security_gate", lambda: gate)
    return gate


def _tool_runner(monkeypatch, tier="large", tools=None, control_tools=None, gate_allowed=(True, "")):
    r = _runner(tier=tier)
    r._thinker = MagicMock()
    r._visible_tool_whitelist = lambda: [t["function"]["name"] for t in (tools or [])] or ["calc"]
    mcp = _fake_mcp(monkeypatch, tools=tools, control_tools=control_tools)
    _fake_gate(monkeypatch, gate_allowed)
    _fake_bus_port(monkeypatch)
    _fake_compression(monkeypatch)
    return r, mcp


def _tc(name, arguments="{}", tid="tc1"):
    return ToolCall(id=tid, name=name, arguments=arguments)


def _resp(content=None, calls=None):
    return ChatResponse(
        message=ChatMessage(content=content, role="assistant", tool_calls=calls),
        finish_reason="tool_calls" if calls else "stop",
    )


def _chat_client(*responses):
    """非流式 client（无 chat_stream 属性）"""
    client = MagicMock()
    client.supports_native_tools = True
    delattr(client, 'chat_stream')
    client.chat = AsyncMock(side_effect=list(responses))
    return client


def _stream_client(*responses):
    """流式 client：chat_stream 每轮先喂 on_token chunk 再返回响应"""
    client = MagicMock()
    client.supports_native_tools = True
    client._chunks = ["x" * 100, "余量\n下一行"]
    _responses = list(responses)

    async def chat_stream(on_token=None, **kwargs):
        if on_token:
            for c in client._chunks:
                on_token(c)
        return _responses.pop(0)

    client.chat_stream = chat_stream
    return client


# ── _wait_for_wakeup_event ────────────────────────────────────────────────

async def test_wait_wakeup_event_none():
    r = _runner()
    r._wakeup_event = None
    assert await r._wait_for_wakeup_event(timeout=5, progress_interval=10) is None


async def test_wait_wakeup_thinking_result(monkeypatch):
    r = _runner()
    r._running = True
    r._wakeup_event = FakeEvent(signaled=True)
    r._thinker = MagicMock()
    r._pending_user_responses = {}
    _fake_bus_port(monkeypatch, receive_return=[{
        "content": {
            "action": "thinking_result",
            "result": "专家结论",
            "source_model_id": "expert_1",
            "source_tier": "expert",
            "source_role": "code_writer",
            "delegation_id": "d1",
        },
    }])
    out = await r._wait_for_wakeup_event(timeout=30, progress_interval=30)
    assert "专家结论" in out
    assert "专家" in out
    r._thinker._process_delegation_response.assert_called_once_with("专家结论", "d1")


async def test_wait_wakeup_user_input(monkeypatch):
    r = _runner()
    r._running = True
    r._wakeup_event = FakeEvent(signaled=True)
    _fake_bus_port(monkeypatch, receive_return=[{
        "content": {"action": "user_input", "content": "新的用户消息"},
    }])
    out = await r._wait_for_wakeup_event(timeout=30, progress_interval=30)
    assert out == "新的用户消息"


async def test_wait_wakeup_progress_report(monkeypatch):
    r = _runner()
    r._running = True
    r._wakeup_event = FakeEvent(signaled=True)
    _fake_bus_port(monkeypatch, receive_return=[{
        "content": {"action": "progress_report", "report": "专家运行中"},
    }])
    out = await r._wait_for_wakeup_event(timeout=30, progress_interval=30)
    assert "专家运行中" in out


async def test_wait_wakeup_string_content(monkeypatch):
    r = _runner()
    r._running = True
    r._wakeup_event = FakeEvent(signaled=True)
    _fake_bus_port(monkeypatch, receive_return=[{"content": "纯文本唤醒"}])
    out = await r._wait_for_wakeup_event(timeout=30, progress_interval=30)
    assert out == "纯文本唤醒"


async def test_wait_wakeup_unparsed_then_timeout(monkeypatch):
    r = _runner()
    r._running = True
    r._wakeup_event = FakeEvent(signaled=True)
    r._thinker = None
    _fake_bus_port(monkeypatch, receive_return=[{"content": {"action": "bogus_action"}}])
    # 第一轮事件触发但无法解析 → 重置计时继续；随后 wait 返回 False（已 clear）→ 超时退出
    out = await r._wait_for_wakeup_event(timeout=0.01, progress_interval=300)
    assert out is None


async def test_wait_wakeup_progress_interval(monkeypatch):
    r = _runner()
    r._running = True
    r._wakeup_event = FakeEvent(signaled=False)
    r._collect_expert_progress = AsyncMock(return_value="专家A: 已运行 1s")
    out = await r._wait_for_wakeup_event(timeout=30, progress_interval=0)
    assert "专家A" in out


async def test_wait_wakeup_timeout_with_pending(monkeypatch):
    r = _runner()
    r._running = True
    r._wakeup_event = FakeEvent(signaled=False)
    r._thinker = MagicMock()
    r._thinker._pending_delegations = {"d1": {"status": "pending"}}
    r._collect_expert_progress = AsyncMock(return_value="")
    out = await r._wait_for_wakeup_event(timeout=0.01, progress_interval=300)
    assert "等待超时" in out


async def test_wait_wakeup_timeout_with_progress(monkeypatch):
    r = _runner()
    r._running = True
    r._wakeup_event = FakeEvent(signaled=False)
    r._thinker = MagicMock()
    r._thinker._pending_delegations = {"d1": {"status": "pending"}}
    r._collect_expert_progress = AsyncMock(return_value="专家仍在运行")
    out = await r._wait_for_wakeup_event(timeout=0.01, progress_interval=300)
    assert "等待超时-专家仍在运行" in out


# ── _collect_expert_progress ──────────────────────────────────────────────

def test_collect_expert_progress_with_runners(monkeypatch):
    r = _runner()
    mgr = MagicMock()
    mgr.list_runners.return_value = [
        {"tier": "expert", "running": True, "started_at": _time.time() - 5,
         "name": "专家A", "task": "写代码"},
        {"tier": "expert", "running": True, "started_at": _time.time() - 122,
         "name": "专家B", "task": "查资料"},
        {"tier": "expert", "running": False, "name": "专家C", "task": ""},
        {"tier": "supervisor", "running": True, "name": "主管", "task": ""},
    ]
    monkeypatch.setattr(mr_mod, "_runner_managers", {"s1": mgr})
    out = asyncio.run(r._collect_expert_progress())
    assert "专家A" in out
    assert "5s" in out
    assert "2m2s" in out
    assert "专家C" not in out


async def test_collect_expert_progress_exception(monkeypatch):
    r = _runner()
    mgr = MagicMock()
    mgr.list_runners.side_effect = RuntimeError("boom")
    monkeypatch.setattr(mr_mod, "_runner_managers", {"s1": mgr})
    assert await r._collect_expert_progress() == ""


# ── _build_runner_prompt ──────────────────────────────────────────────────

def _stub_slicer(monkeypatch):
    import modules.thinking.cognition as cog
    slicer = MagicMock()
    slicer.slice_for_large.return_value = "【大模型上下文】"
    slicer.slice_for_supervisor.return_value = "【主管上下文】"
    slicer.slice_for_expert.return_value = "【专家上下文】"
    monkeypatch.setattr(cog, "ContextSlicer", lambda: slicer)
    return slicer


async def test_build_runner_prompt_large(monkeypatch):
    r = _runner(tier="large")
    _stub_slicer(monkeypatch)
    _fake_bus_port(monkeypatch, receive_return=[{
        "sender": "expert_1",
        "content": {"action": "thinking_result", "result": "结果X", "source_model_id": "expert_1"},
    }])
    r._consume_guidance = MagicMock()
    r._build_prompt = MagicMock(return_value="prompt")
    r._task_description = "任务"
    out = await r._build_runner_prompt(1)
    assert out == "prompt"
    args = r._build_prompt.call_args.kwargs
    assert "大模型上下文" in args["dialog_context"]
    assert "结果X" in args["expert_context"]


async def test_build_runner_prompt_supervisor(monkeypatch):
    r = _runner(tier="supervisor")
    _stub_slicer(monkeypatch)
    _fake_bus_port(monkeypatch, receive_return=[{"sender": "s2", "content": "普通消息"}])
    r._consume_guidance = MagicMock()
    r._build_prompt = MagicMock(return_value="p")
    await r._build_runner_prompt(1)
    assert "主管上下文" in r._build_prompt.call_args.kwargs["dialog_context"]
    assert "[s2]" in r._build_prompt.call_args.kwargs["expert_context"]


async def test_build_runner_prompt_expert(monkeypatch):
    tc = MagicMock()
    tc.round_count = 3
    r = _runner(tier="expert", turn_context=tc)
    slicer = _stub_slicer(monkeypatch)
    _fake_bus_port(monkeypatch)
    r._consume_guidance = MagicMock()
    r._build_prompt = MagicMock(return_value="p")
    await r._build_runner_prompt(1)
    slicer.slice_for_expert.assert_called_once_with(r.blackboard, cursor=3, round_start=0, round_end=0)


async def test_build_runner_prompt_no_blackboard(monkeypatch):
    r = _runner(tier="large", blackboard=None)
    _stub_slicer(monkeypatch)
    _fake_bus_port(monkeypatch)
    r._consume_guidance = MagicMock()
    r._build_prompt = MagicMock(return_value="p")
    await r._build_runner_prompt(1)
    assert r._build_prompt.call_args.kwargs["dialog_context"] == ""


# ── _generate 杂项（调试日志 / 主管预览 / 超时）────────────────────────────

def _gen_runner(monkeypatch, tier="expert"):
    r = _runner(tier=tier)
    r._supports_native_tool_chat = lambda c: False
    return r


async def test_generate_debug_prompt_logging(monkeypatch):
    r = _gen_runner(monkeypatch)
    monkeypatch.setattr(mr_mod.logger, "isEnabledFor", lambda lvl: True)
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: True)
    client = MagicMock()
    client.generate = AsyncMock(return_value="生成结果")
    r.instance.client = client
    out = await r._generate("提示" * 300)
    assert out == "生成结果"


async def test_generate_supervisor_preview(monkeypatch):
    r = _gen_runner(monkeypatch, tier="supervisor")
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: True)
    client = MagicMock()
    client.generate = AsyncMock(return_value="主管输出")
    r.instance.client = client
    out = await r._generate("提示")
    assert out == "主管输出"


async def test_generate_timeout_raises(monkeypatch):
    r = _gen_runner(monkeypatch)
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: True)
    r.instance.client = MagicMock()

    def _boom(awaitable, timeout):
        awaitable.close()  # 避免未 await 协程告警
        return _raising()

    async def _raising():
        raise asyncio.TimeoutError
    monkeypatch.setattr(mr_mod, "pausable_wait_for", _boom)
    with pytest.raises(asyncio.TimeoutError):
        await r._generate("提示")


# ── _build_system_prompt_for_mode ─────────────────────────────────────────

def _stub_composer(monkeypatch, raise_build=False):
    import config.prompts.composer as comp_mod
    class FakeComposer:
        def build_system(self, req):
            if raise_build:
                raise RuntimeError("composer boom")
            return "【人设】测试人格"
    captured = {}

    class FakeRequest:
        def __init__(self, **kw):
            captured.update(kw)
    monkeypatch.setattr(comp_mod, "PromptComposer", lambda: FakeComposer())
    monkeypatch.setattr(comp_mod, "PromptRequest", FakeRequest)
    return captured


def _stub_skills(monkeypatch, skill=None):
    import modules.thinking.skills as sk_mod
    mgr = MagicMock()
    mgr.get_skill.return_value = skill
    mgr.list_skills.return_value = [skill] if skill else []
    mgr.list_skills_for_role.return_value = [skill] if skill else []
    monkeypatch.setattr(sk_mod, "skill_manager", mgr)
    return mgr


def _patch_cfg_method(monkeypatch, name, fn):
    """pydantic Settings 实例不允许 setattr 任意属性 → 打到类上（带 self 参数）"""
    from config.settings import settings as cfg
    monkeypatch.setattr(type(cfg), name, fn)


def _patch_cfg(monkeypatch, **attrs):
    from config.settings import settings as cfg
    for k, v in attrs.items():
        monkeypatch.setattr(cfg, k, v)
    return cfg


def test_system_prompt_forced_skill_injection(monkeypatch):
    skill = MagicMock()
    skill.id = "code"
    skill.enabled = True
    skill.tool_rules = {"allow": ["calc"]}
    skill.description = "写代码"
    skill.name = "代码专家"
    _patch_cfg_method(monkeypatch, "get_forced_skill", lambda self: "code")
    _stub_skills(monkeypatch, skill)
    r = _runner(tier="large")
    r._active_skill = None
    r._active_skill_tool_rules = None
    r._visible_tool_whitelist = lambda: ["calc"]
    captured = _stub_composer(monkeypatch)
    sp = r._build_system_prompt_for_mode()
    assert "测试人格" in sp
    assert r._active_skill is skill
    assert captured.get("skill_id") == "code"


def test_system_prompt_forced_skill_error(monkeypatch):
    _patch_cfg_method(monkeypatch, "get_forced_skill",
                      lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    _stub_skills(monkeypatch)
    r = _runner(tier="large")
    r._visible_tool_whitelist = lambda: []
    _stub_composer(monkeypatch)
    r._build_system_prompt_for_mode()  # 不抛


def test_system_prompt_no_blackboard(monkeypatch):
    r = _runner(tier="large", blackboard=None)
    r._visible_tool_whitelist = lambda: []
    _stub_composer(monkeypatch)
    sp = r._build_system_prompt_for_mode()
    assert "测试人格" in sp


def test_system_prompt_conscience_guidance(monkeypatch):
    import modules.thinking.probes.probe_tools as pt_mod
    r = _runner(tier="large")
    r.model_id = "large_primary"
    r.session_id = "s1"
    monkeypatch.setattr(pt_mod, "_session_guidance",
                        {("large_primary", "s1"): {"inner_thoughts": "【良知】要诚实"}})
    r._visible_tool_whitelist = lambda: []
    captured = _stub_composer(monkeypatch)
    r._build_system_prompt_for_mode()
    assert "【良知】要诚实" in captured.get("conscience_guidance", "")


def test_system_prompt_conscience_error(monkeypatch):
    import modules.thinking.probes.probe_tools as pt_mod
    class _Bad:
        def get(self, *a):
            raise RuntimeError("bad")
    monkeypatch.setattr(pt_mod, "_session_guidance", _Bad())
    r = _runner(tier="large")
    r.model_id = "m"
    r.session_id = "s"
    r._visible_tool_whitelist = lambda: []
    _stub_composer(monkeypatch)
    r._build_system_prompt_for_mode()  # 不抛


def test_system_prompt_non_core_section(monkeypatch):
    r = _runner(tier="large")
    r._visible_tool_whitelist = lambda: ["calc"]
    _stub_composer(monkeypatch)
    import infra.tool_manager.tool_registry as tr_mod
    monkeypatch.setattr(tr_mod.ToolRegistry, "list_non_core_tools",
                        staticmethod(lambda wl: [{"name": "web_search"}]))
    sp = r._build_system_prompt_for_mode()
    assert "web_search" in sp


def test_system_prompt_non_core_error(monkeypatch):
    r = _runner(tier="large")
    r._visible_tool_whitelist = lambda: []
    _stub_composer(monkeypatch)
    import infra.tool_manager.tool_registry as tr_mod
    def boom(wl):
        raise RuntimeError("no")
    monkeypatch.setattr(tr_mod.ToolRegistry, "list_non_core_tools", staticmethod(boom))
    r._build_system_prompt_for_mode()  # 不抛


def test_system_prompt_composer_fallback(monkeypatch):
    r = _runner(tier="large")
    r._visible_tool_whitelist = lambda: []
    _stub_composer(monkeypatch, raise_build=True)
    sp = r._build_system_prompt_for_mode()
    assert sp == "你是总指挥"


# ── _build_time_context ───────────────────────────────────────────────────

def test_build_time_context_elapsed_branches():
    now = _time.time()
    r = _runner()
    r.blackboard.runtime_state = {"last_user_message_time": now - 30}
    out = r._build_time_context()
    assert "秒前" in out
    r.blackboard.runtime_state = {"last_user_message_time": now - 300}
    assert "分钟前" in r._build_time_context()
    r.blackboard.runtime_state = {"last_user_message_time": now - 7200}
    assert "小时前" in r._build_time_context()
    r.blackboard.runtime_state = {"last_user_message_time": now - 200000}
    assert "天前" in r._build_time_context()
    r.blackboard.runtime_state = {"last_user_message_time": 0}
    assert "首次对话" in r._build_time_context()


def test_build_time_context_error(monkeypatch):
    r = _runner()
    r.blackboard.runtime_state = MagicMock()
    r.blackboard.runtime_state.get.side_effect = RuntimeError("boom")
    out = r._build_time_context()  # 不抛
    assert "当前时间" in out


# ── _build_tool_guard_prompt ──────────────────────────────────────────────

def test_guard_prompt_no_delegation(monkeypatch):
    from config.settings import settings as cfg
    monkeypatch.setattr(type(cfg), "is_delegation_available", property(lambda self: False))
    r = _runner(tier="large")
    r._visible_tool_whitelist = lambda: ["calc"]
    prompt = r._build_tool_guard_prompt()
    assert "不需要委托他人" in prompt


def test_guard_prompt_non_core_success_and_error(monkeypatch):
    import infra.tool_manager.tool_registry as tr_mod
    r = _runner(tier="supervisor")
    r._visible_tool_whitelist = lambda: [f"t{i}" for i in range(12)]
    monkeypatch.setattr(tr_mod.ToolRegistry, "list_non_core_tools",
                        staticmethod(lambda wl: [{"name": "query_tool_details"}]))
    prompt = r._build_tool_guard_prompt()
    assert "query_tool_details" in prompt
    def boom(wl):
        raise RuntimeError("no")
    monkeypatch.setattr(tr_mod.ToolRegistry, "list_non_core_tools", staticmethod(boom))
    r._build_tool_guard_prompt()  # 不抛


# ── 参数验证异常路径 ──────────────────────────────────────────────────────

def test_has_required_tool_args_exception(monkeypatch):
    import infra.tool_manager.tool_registry as tr_mod
    def boom(name):
        raise RuntimeError("no")
    monkeypatch.setattr(tr_mod.ToolRegistry, "get_tool", staticmethod(boom))
    r = _runner()
    assert r._has_required_tool_args("calc", {"a": 1}) is True


def test_missing_required_tool_args_exception(monkeypatch):
    import infra.tool_manager.tool_registry as tr_mod
    def boom(name):
        raise RuntimeError("no")
    monkeypatch.setattr(tr_mod.ToolRegistry, "get_tool", staticmethod(boom))
    r = _runner()
    assert r._missing_required_tool_args("calc", {}) == []


# ── _generate_with_tools：流式 / 模式变更 / 工具分类 / 控制工具 ─────────────

async def test_generate_with_tools_streaming_mode_change(monkeypatch):
    _patch_cfg(monkeypatch, EXECUTION_MODE="plan")
    r, mcp = _tool_runner(monkeypatch, tier="large")
    r._last_known_mode = "edit"
    r._emit_streaming_content = MagicMock()
    client = _stream_client(
        _resp(content="", calls=[_tc("calc", '{"a":1,"op":"+","b":1}')]),
        _resp(content="最终回复"),
    )
    out = await r._generate_with_tools("system", "user", client)
    assert out == "最终回复"
    mcp.execute.assert_called_once()
    assert r._last_known_mode == "plan"
    r._thinker.record_control_decision.assert_called_once_with(
        {"continue": False, "result_summary": "最终回复"})
    assert r._emit_streaming_content.call_count >= 1


async def test_generate_with_tools_mode_check_error(monkeypatch):
    """模式检查（turn 循环内）抛异常 → 走 1598-1599 的 except"""
    import importlib
    cfg_mod = importlib.import_module("config.settings")
    class _FlipMode:
        def __init__(self):
            self.n = 0
        @property
        def effective_execution_mode(self):
            self.n += 1
            if self.n >= 2:  # 第1次访问是 perm_ctrl.get_control_tools 前的 _settings（1552）
                raise RuntimeError("mode boom")
            return "edit"
        @property
        def is_delegation_available(self):
            return True
    monkeypatch.setattr(cfg_mod, "settings", _FlipMode())
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(content="回复"))
    out = await r._generate_with_tools("system", "user", client)
    assert out == "回复"


async def test_generate_with_tools_compression_error(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    engine = MagicMock()
    engine.estimate_tokens.side_effect = RuntimeError("compression boom")
    _fake_compression(monkeypatch, engine)
    client = _chat_client(_resp(content="回复"))
    out = await r._generate_with_tools("system", "user", client)
    assert out == "回复"


async def test_generate_with_tools_expert_errors_append(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="expert")
    mcp.execute.return_value = ToolCallResult(success=False, error="权限不足")
    client = _chat_client(
        _resp(content="", calls=[_tc("calc", '{"a":1,"op":"+","b":1}')]),
        _resp(content="专家最终结果"),
    )
    out = await r._generate_with_tools("system", "user", client)
    assert "专家最终结果" in out
    assert "权限不足" in out


async def test_generate_with_tools_expert_tool_exception(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="expert")
    mcp.execute.side_effect = RuntimeError("mcp down")
    client = _chat_client(
        _resp(content="", calls=[_tc("calc", '{"a":1,"op":"+","b":1}')]),
        _resp(content="完成"),
    )
    out = await r._generate_with_tools("system", "user", client)
    assert "完成" in out


async def test_generate_with_tools_supervisor_forced_end(monkeypatch):
    r, _ = _tool_runner(monkeypatch, tier="supervisor")
    r.MAX_CHAT_TOOL_TURNS = 1
    client = _chat_client(_resp(content="", calls=None))
    out = await r._generate_with_tools("system", "user", client)
    assert "已处理" in out
    r._thinker.record_control_decision.assert_called_once()


async def test_generate_with_tools_query_tool_details(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    info = MagicMock()
    info.description = "查询网页"
    info.risk_level = "medium"
    info.category = "web"
    info.to_json_schema.return_value = {"type": "object", "properties": {"url": {"type": "string"}}}
    import infra.tool_manager.tool_registry as tr_mod
    monkeypatch.setattr(tr_mod.ToolRegistry, "get_tool", staticmethod(lambda name: info))
    client = _chat_client(
        _resp(content="", calls=[_tc("query_tool_details", '{"tool_name": "web_fetch"}')]),
        _resp(content="继续执行"),
    )
    out = await r._generate_with_tools("system", "user", client)
    assert "继续执行" in out
    info.to_json_schema.assert_called()


async def test_generate_with_tools_query_tool_details_missing_name(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(
        _resp(content="", calls=[_tc("query_tool_details", '{"tool_name": ""}')]),
        _resp(content="继续"),
    )
    out = await r._generate_with_tools("system", "user", client)
    assert "继续" in out


async def test_generate_with_tools_query_tool_details_exception(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    import infra.tool_manager.tool_registry as tr_mod
    def boom(name):
        raise RuntimeError("registry down")
    monkeypatch.setattr(tr_mod.ToolRegistry, "get_tool", staticmethod(boom))
    client = _chat_client(
        _resp(content="", calls=[_tc("query_tool_details", '{"tool_name": "x"}')]),
        _resp(content="继续"),
    )
    out = await r._generate_with_tools("system", "user", client)
    assert "继续" in out


async def test_generate_with_tools_control_continue_thinking(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("continue_thinking", '{"continue": false, "result_summary": "任务完成", "wait_seconds": 5}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "任务完成" in out
    r._thinker.record_control_decision.assert_called_once_with(
        {"continue": False, "wait_seconds": 5, "result_summary": "任务完成"})


async def test_generate_with_tools_control_respond_to_user(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("respond_to_user", '{"content": "直接回复用户"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert out == "直接回复用户"


async def test_generate_with_tools_control_set_memory_focus(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("set_memory_focus", '{"mix": {"topic": "股市"}}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert isinstance(out, str)
    assert r._thinker._memory_focus == {"topic": "股市"}


async def test_generate_with_tools_control_request_skill_allowed(monkeypatch):
    skill = MagicMock()
    skill.id = "code"
    skill.name = "代码专家"
    skill.enabled = True
    skill.description = "负责写代码"
    skill.tool_rules = None
    _stub_skills(monkeypatch, skill)
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("request_skill", '{"skill_id": "code"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "代码专家" in out
    assert r._active_skill is skill


async def test_generate_with_tools_control_request_skill_forced(monkeypatch):
    _patch_cfg_method(monkeypatch, "get_forced_skill", lambda self: "other")
    _stub_skills(monkeypatch)
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("request_skill", '{"skill_id": "code"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "技能未找到" in out


async def test_generate_with_tools_control_request_skill_denied(monkeypatch):
    _stub_skills(monkeypatch, skill=None)
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("request_skill", '{"skill_id": "code"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "技能未找到" in out


async def test_generate_with_tools_control_stop_skill_active(monkeypatch):
    skill = MagicMock()
    skill.id = "code"
    skill.name = "代码专家"
    r, mcp = _tool_runner(monkeypatch, tier="large")
    r._active_skill = skill
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("stop_skill", '{"reason": "完成"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "无活跃技能" in out
    assert r._active_skill is None


async def test_generate_with_tools_control_stop_skill_forced(monkeypatch):
    _patch_cfg_method(monkeypatch, "get_forced_skill", lambda self: "code")
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("stop_skill", '{"reason": "x"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "无活跃技能" in out


async def test_generate_with_tools_control_stop_skill_none(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("stop_skill", '{"reason": "x"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "无活跃技能" in out


async def test_generate_with_tools_control_list_skills(monkeypatch):
    skill = MagicMock()
    skill.id = "calc_skill"
    skill.name = "计算器"
    skill.description = "计算"
    _stub_skills(monkeypatch, skill)
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("list_skills", '{}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "计算器" in out


async def test_generate_with_tools_control_stop_task_success(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    mgr = MagicMock()
    mgr.stop_runner = AsyncMock(return_value=True)
    r.manager = mgr
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("stop_task", '{"target_model_id": "expert_1", "reason": "任务已过时"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "思考控制" in out
    mgr.stop_runner.assert_awaited_once_with("expert_1")


async def test_generate_with_tools_control_stop_task_fail(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    mgr = MagicMock()
    mgr.stop_runner = AsyncMock(return_value=False)
    r.manager = mgr
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("stop_task", '{"target_model_id": "expert_1", "reason": "x"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "思考控制" in out
    mgr.stop_runner.assert_awaited_once_with("expert_1")


async def test_generate_with_tools_control_stop_task_no_manager(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    r.manager = None
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("stop_task", '{"target_model_id": "e1", "reason": "x"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "思考控制" in out


async def test_generate_with_tools_control_stop_task_missing_args(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("stop_task", '{}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "思考控制" in out


async def test_generate_with_tools_control_exception(monkeypatch):
    import modules.thinking.skills as sk_mod
    mgr = MagicMock()
    mgr.list_skills_for_role.side_effect = RuntimeError("skills down")
    monkeypatch.setattr(sk_mod, "skill_manager", mgr)
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("list_skills", '{}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "可用技能" in out


# ── delegate_task / create_supervisor ─────────────────────────────────────

def _stub_delegation(monkeypatch, result=None, raise_delegate=False):
    import modules.thinking.core.delegation_port as dp
    adapter = MagicMock()
    if raise_delegate:
        adapter.delegate = AsyncMock(side_effect=RuntimeError("adapter down"))
    else:
        adapter.delegate = AsyncMock(return_value=result or type(
            "R", (), {"success": True, "error": "", "metadata": {}})())
    monkeypatch.setattr(dp, "ProbeDelegationAdapter", lambda: adapter)
    return adapter


async def test_generate_with_tools_delegate_success(monkeypatch):
    _stub_delegation(monkeypatch, result=type("R", (), {"success": True, "error": ""})())
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("delegate_task", '{"role": "expert_implementer", "task": "实现功能"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "委托" in out
    assert r._status == "waiting_delegation"


async def test_generate_with_tools_delegate_failure(monkeypatch):
    import modules.thinking.identity as id_mod
    monkeypatch.setattr(id_mod, "get_identities", lambda: {
        "expert_implementer": {"role": "expert_implementer"},
        "supervisor_code": {"role": "supervisor_code"},
    })
    _stub_delegation(monkeypatch, result=type("R", (), {"success": False, "error": "未找到匹配的角色"})())
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("delegate_task", '{"role": "no_such_role", "task": "任务"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "委托失败" in out
    assert "supervisor_code" in out


async def test_generate_with_tools_delegate_plan_blocked(monkeypatch):
    _patch_cfg(monkeypatch, EXECUTION_MODE="plan")
    _stub_delegation(monkeypatch)
    r, mcp = _tool_runner(monkeypatch, tier="large")
    r._thinker = MagicMock()
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("delegate_task", '{"role": "expert_1", "task": "写入文件完成部署"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "安全门控拦截" in out


async def test_generate_with_tools_delegate_exception(monkeypatch):
    _stub_delegation(monkeypatch, raise_delegate=True)
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("delegate_task", '{"role": "expert_1", "task": "任务"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert isinstance(out, str)


async def test_generate_with_tools_delegate_identity_error(monkeypatch):
    import modules.thinking.identity as id_mod
    def boom():
        raise RuntimeError("no identities")
    monkeypatch.setattr(id_mod, "get_identities", boom)
    _stub_delegation(monkeypatch, result=type("R", (), {"success": False, "error": "失败"})())
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("delegate_task", '{"role": "x", "task": "任务"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "无法获取角色列表" in out


async def test_generate_with_tools_create_supervisor_success(monkeypatch):
    import modules.thinking.model_factory as mf_mod
    import modules.thinking.identity as id_mod
    factory = MagicMock()
    instance = MagicMock()
    instance.model_id = "supervisor_code_001"
    factory.create_supervisor.return_value = instance
    monkeypatch.setattr(mf_mod, "get_model_factory", lambda: factory)
    ident_cls = type("I", (), {"from_template": staticmethod(lambda k: MagicMock())})
    monkeypatch.setattr(id_mod, "ModelIdentity", ident_cls)
    monkeypatch.setattr(id_mod, "get_identities", lambda: {})
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("create_supervisor", '{"role": "data_supervisor", "template_key": "supervisor_code", "task": "分析数据"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "创建主管成功" in out


async def test_generate_with_tools_create_supervisor_missing_args(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("create_supervisor", '{"role": "x"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "缺少必填参数" in out


async def test_generate_with_tools_create_supervisor_exception(monkeypatch):
    import modules.thinking.model_factory as mf_mod
    import modules.thinking.identity as id_mod
    factory = MagicMock()
    factory.create_supervisor.side_effect = RuntimeError("factory down")
    monkeypatch.setattr(mf_mod, "get_model_factory", lambda: factory)
    ident_cls = type("I", (), {"from_template": staticmethod(lambda k: MagicMock())})
    monkeypatch.setattr(id_mod, "ModelIdentity", ident_cls)
    monkeypatch.setattr(id_mod, "get_identities", lambda: {})
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("create_supervisor", '{"role": "x", "template_key": "supervisor_code"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "创建主管异常" in out


# ── 普通工具执行：安全门控 / 摘要 / web 包裹 / 最大轮次 / 重试 ──────────────

async def test_generate_with_tools_gate_not_allowed(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large", gate_allowed=(False, "写操作需要审批"))
    client = _chat_client(
        _resp(content="", calls=[_tc("calc", '{"a":1,"op":"+","b":1}')]),
        _resp(content="调整后完成"),
    )
    out = await r._generate_with_tools("system", "user", client)
    assert "调整后完成" in out
    assert mcp.execute.call_count == 0


async def test_generate_with_tools_gate_exception_retries(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    gate = MagicMock()
    gate.check = AsyncMock(side_effect=RuntimeError("gate exploded"))
    monkeypatch.setattr(mr_mod, "get_tool_security_gate", lambda: gate)
    r.GENERATE_RETRIES = 2
    client = _chat_client(_resp(content="", calls=[_tc("calc", '{"a":1,"op":"+","b":1}')]))
    out = await r._generate_with_tools("system", "user", client)
    assert "模型调用失败" in out
    assert r._status == "error"


async def test_generate_with_tools_user_approved(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large", gate_allowed=(True, "用户批准 已同意"))
    client = _chat_client(
        _resp(content="", calls=[_tc("calc", '{"a":1,"op":"+","b":1}')]),
        _resp(content="完成"),
    )
    out = await r._generate_with_tools("system", "user", client)
    assert "完成" in out


async def test_generate_with_tools_todo_inject_session(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    captured = {}
    def _execute(req):
        captured["args"] = req.params
        return ToolCallResult(success=True, result="todo ok")
    mcp.execute.side_effect = _execute
    client = _chat_client(
        _resp(content="", calls=[_tc("todo", '{"action": "create", "items": "[]"}')]),
        _resp(content="完成"),
    )
    out = await r._generate_with_tools("system", "user", client)
    assert captured["args"].get("session_id") == "s1"


async def test_generate_with_tools_web_wrapping(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    mcp.execute.return_value = ToolCallResult(success=True, result="网页内容")
    client = _chat_client(
        _resp(content="", calls=[_tc("web_search", '{"query": "天气"}')]),
        _resp(content="汇总完成"),
    )
    out = await r._generate_with_tools("system", "user", client)
    assert "汇总完成" in out


async def test_generate_with_tools_exec_command_summary(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    mcp.execute.return_value = ToolCallResult(success=True, result='{"exit_code": 0, "stdout": "ok"}')
    bb = r.blackboard
    client = _chat_client(
        _resp(content="", calls=[_tc("exec_command", '{"command": "ls -la"}')]),
        _resp(content="完成"),
    )
    await r._generate_with_tools("system", "user", client)
    written = [c.kwargs.get("content") for c in bb.write_thought.call_args_list]
    assert any("exec_command" in w and "exit=0" in w for w in written)


async def test_generate_with_tools_git_status_summary(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    bb = r.blackboard
    client = _chat_client(
        _resp(content="", calls=[_tc("git_status", '{}')]),
        _resp(content="完成"),
    )
    await r._generate_with_tools("system", "user", client)
    written = [c.kwargs.get("content") for c in bb.write_thought.call_args_list]
    assert any("git_status: ok" in w for w in written)


async def test_generate_with_tools_big_result_summary(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    mcp.execute.return_value = ToolCallResult(success=True, result="x" * 200)
    bb = r.blackboard
    client = _chat_client(
        _resp(content="", calls=[_tc("calc", '{"a":1,"op":"+","b":1}')]),
        _resp(content="完成"),
    )
    await r._generate_with_tools("system", "user", client)
    written = [c.kwargs.get("content") for c in bb.write_thought.call_args_list]
    assert any("200 chars" in w for w in written)


async def test_generate_with_tools_blackboard_write_error(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    r.blackboard.write_thought.side_effect = RuntimeError("bb down")
    client = _chat_client(
        _resp(content="", calls=[_tc("calc", '{"a":1,"op":"+","b":1}')]),
        _resp(content="完成"),
    )
    out = await r._generate_with_tools("system", "user", client)
    assert "完成" in out


async def test_generate_with_tools_missing_args_with_hint(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    r._missing_required_tool_args = MagicMock(return_value=["keys"])
    client = _chat_client(
        _resp(content="", calls=[_tc("keyboard_hotkey", '{}')]),
        _resp(content="修正完成"),
    )
    out = await r._generate_with_tools("system", "user", client)
    assert out == "修正完成"


async def test_generate_with_tools_missing_args_keyboard_press_hint(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    r._missing_required_tool_args = MagicMock(return_value=["key"])
    client = _chat_client(
        _resp(content="", calls=[_tc("keyboard_press", '{}')]),
        _resp(content="修正完成"),
    )
    out = await r._generate_with_tools("system", "user", client)
    assert out == "修正完成"


async def test_generate_with_tools_max_turns(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    r.MAX_CHAT_TOOL_TURNS = 2
    client = _chat_client(
        _resp(content="", calls=[_tc("calc", '{"a":1,"op":"+","b":1}')]),
        _resp(content="", calls=[_tc("calc", '{"a":1,"op":"+","b":1}')]),
    )
    out = await r._generate_with_tools("system", "user", client)
    assert "工具调用达到上限" in out


async def test_generate_with_tools_max_turns_expert_errors(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="expert")
    r.MAX_CHAT_TOOL_TURNS = 1
    mcp.execute.return_value = ToolCallResult(success=False, error="超时")
    client = _chat_client(_resp(content="", calls=[_tc("calc", '{"a":1,"op":"+","b":1}')]))
    out = await r._generate_with_tools("system", "user", client)
    assert "工具调用失败记录" in out


async def test_generate_with_tools_chat_raises_retries(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = MagicMock()
    client.supports_native_tools = True
    delattr(client, 'chat_stream')
    client.chat = AsyncMock(side_effect=RuntimeError("network down"))
    r.instance.client = client
    out = await r._generate_with_tools("system", "user", client)
    assert "模型调用失败" in out
    assert r._status == "error"


async def test_generate_with_tools_503(monkeypatch):
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = MagicMock()
    client.supports_native_tools = True
    delattr(client, 'chat_stream')
    client.chat = AsyncMock(side_effect=RuntimeError("503 Service Unavailable"))
    r.instance.client = client
    out = await r._generate_with_tools("system", "user", client)
    assert "503" in out


# ── _format_messages_for_context / _check_messages / _build_prompt ─────────

def test_format_messages_dict_other_action():
    msgs = [{"role": "assistant", "content": {"action": "bogus", "x": 1}}]
    out = ModelRunner._format_messages_for_context(msgs)
    assert "bogus" in out


async def test_check_messages_exception(monkeypatch):
    import modules.thinking.communication.interface as iface_mod
    bus = MagicMock()
    bus.receive = AsyncMock(side_effect=RuntimeError("bus down"))
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)
    r = _runner()
    assert await r._check_messages() == []


def test_build_prompt_with_expert_context():
    r = _runner()
    out = r._build_prompt(guidance="", dialog_context="", expert_context="【专家消息】完成")
    assert "专家消息" in out


# ── _wait_for_user_response / ask_user_intent / mode change 补充 ───────────

async def test_wait_for_user_response_send_error(monkeypatch):
    import modules.security_system.tool_security_gate as tsg
    def boom(*a, **k):
        raise RuntimeError("emit down")
    monkeypatch.setattr(tsg, "_emit_security_event", boom)
    r = _runner()
    if hasattr(r, "_pending_user_responses"):
        del r._pending_user_responses
    out = await r._wait_for_user_response("user_intent_request", {"question": "q"})
    assert out["response"] == "事件发送失败"


async def test_handle_ask_user_intent_timeout(monkeypatch):
    r = _runner()
    r._wait_for_user_response = AsyncMock(return_value={"timeout": True})
    out = await r._handle_ask_user_intent("q", ["A"], "ctx")
    assert "超时" in out


async def test_handle_ask_user_intent_cancelled(monkeypatch):
    r = _runner()
    r._wait_for_user_response = AsyncMock(return_value={"cancelled": True})
    out = await r._handle_ask_user_intent("q", ["A"], "ctx")
    assert "连接已断开" in out


async def test_handle_mode_change_settings_error(monkeypatch):
    from modules.security_system.tool_security_gate import ToolSecurityGate
    import modules.security_system.tool_security_gate as tsg
    import importlib
    cfg_mod = importlib.import_module("config.settings")
    ToolSecurityGate._pending_reviews.clear()
    monkeypatch.setattr(tsg, "_emit_security_event", lambda *a, **k: None)
    # _handle_mode_change_request 内 `from config.settings import settings as _cfg`
    # 拿到 plain object() → object.__setattr__ 抛 AttributeError → 走 1106-1107
    monkeypatch.setattr(cfg_mod, "settings", object())
    r = _runner()
    r.session_id = "s1"
    task = asyncio.create_task(r._handle_mode_change_request("原因", "edit"))
    await asyncio.sleep(0.05)
    rid = next(iter(ToolSecurityGate._pending_reviews))
    ToolSecurityGate.resolve_review(rid, True, "批准")
    out = await asyncio.wait_for(task, timeout=2)
    assert "同意切换到" in out


def test_resolve_user_response_no_attr():
    r = _runner()
    del r._pending_user_responses
    r.resolve_user_response("rid", {})  # 不抛


# ── _push_reasoning 补充 ──────────────────────────────────────────────────

def test_push_reasoning_identity_error(monkeypatch):
    class _Bad:
        def __getattribute__(self, name):
            raise RuntimeError("bad identity")
    r = _runner()
    r.identity = _Bad()
    import modules.thinking.api_stream as ap
    cm = MagicMock()
    cm.active_connections = {}
    monkeypatch.setattr(ap, "connection_manager", cm)
    r._push_reasoning("思考内容")  # 不抛


def test_push_reasoning_send_error(monkeypatch):
    r = _runner()
    import modules.thinking.api_stream as ap
    cm = MagicMock()
    cm.active_connections = {"s1": object()}
    cm.send_json_from_thread = MagicMock(side_effect=RuntimeError("ws down"))
    monkeypatch.setattr(ap, "connection_manager", cm)
    r._push_reasoning("思考内容")  # 不抛


# ── ModelRunnerManager 细节 ───────────────────────────────────────────────

def _mk_manager(monkeypatch, identities=None):
    import modules.thinking.communication.interface as iface_mod
    bus = MagicMock()
    bus.receive = AsyncMock(return_value=[])
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)
    identities = identities or {
        "expert_implementer": {"tier": "expert", "model_id": "expert_implementer"},
        "supervisor_code": {"tier": "supervisor", "model_id": "supervisor_code"},
        "large_primary": {"tier": "large", "model_id": "large_primary"},
    }
    import modules.thinking.identity as ident_mod

    def _fake_get_identities():
        return identities
    class _FakeIdentity:
        def __init__(self, key):
            self.model_id = identities[key]["model_id"]
            self.tier = identities[key]["tier"]
            self.name = key
            self.role = key
            self.default_skill = ""
    monkeypatch.setattr(ident_mod, "get_identities", _fake_get_identities)
    monkeypatch.setattr(ident_mod.ModelIdentity, "from_template", staticmethod(_FakeIdentity))
    perms = MagicMock()
    perms.max_concurrent_runners = 5
    monkeypatch.setattr(ident_mod, "get_permissions", lambda key: perms)
    factory = MagicMock()
    import modules.thinking.model_factory as mf_mod
    monkeypatch.setattr(mf_mod, "get_model_factory", lambda: factory)
    import modules.thinking.skills as sk_mod
    skill_mgr = MagicMock()
    skill_mgr.get_skill.return_value = None
    monkeypatch.setattr(sk_mod, "skill_manager", skill_mgr)

    class FakeRunner(MagicMock):
        def __init__(self, *a, **k):
            super().__init__()
            self.tier = k.get("model_instance").identity.tier
            self.model_id = k.get("model_instance").identity.model_id
            self.identity = k.get("model_instance").identity
            self._thinker = None
            self._running = False
            self._status = "idle"
            self._status_detail = ""
            self._active_skill = None
            self._active_skill_tool_rules = None
        async def start(self, *a, **k):
            self._running = True
        async def stop(self):
            self._running = False
    monkeypatch.setattr(mr_mod, "ModelRunner", FakeRunner)
    return ModelRunnerManager(session_id="s1"), factory, skill_mgr


def _make_identity_kwargs(monkeypatch, tier="expert"):
    import modules.thinking.identity as ident_mod
    ident = MagicMock(tier=tier, model_id="x", name="n", role="r", default_skill="")
    return ident


async def test_start_runner_agent_inactive(monkeypatch):
    _patch_cfg_method(monkeypatch, "get_agent_active", lambda self, key: False)
    m, factory, _ = _mk_manager(monkeypatch)
    assert await m.start_runner("expert_implementer", "任务") is None


async def test_start_runner_max_per_role(monkeypatch):
    import modules.thinking.identity as ident_mod
    m, factory, _ = _mk_manager(monkeypatch)
    perms = MagicMock()
    perms.max_concurrent_runners = 1
    monkeypatch.setattr(ident_mod, "get_permissions", lambda key: perms)
    factory.create_expert.return_value.identity = _make_identity_kwargs(monkeypatch)
    mid = await m.start_runner("expert_implementer", "任务")
    assert mid is not None
    assert await m.start_runner("expert_implementer", "任务2") is None


async def test_start_runner_large_tier(monkeypatch):
    m, factory, _ = _mk_manager(monkeypatch)
    factory.create_large.return_value.identity = _make_identity_kwargs(monkeypatch, tier="large")
    mid = await m.start_runner("large_primary", "任务")
    assert mid is not None
    factory.create_large.assert_called_once()


async def test_start_runner_supervisor_tier(monkeypatch):
    m, factory, _ = _mk_manager(monkeypatch)
    factory.create_supervisor.return_value.identity = _make_identity_kwargs(monkeypatch, tier="supervisor")
    mid = await m.start_runner("supervisor_code", "任务")
    assert mid is not None
    factory.create_supervisor.assert_called_once()


async def test_start_runner_skill_injected(monkeypatch):
    skill = MagicMock()
    skill.enabled = True
    skill.tool_rules = {"allow": ["calc"]}
    skill.id = "code"
    _patch_cfg_method(monkeypatch, "get_forced_skill", lambda self: "")
    m, factory, skill_mgr = _mk_manager(monkeypatch)
    skill_mgr.get_skill.return_value = skill
    factory.create_expert.return_value.identity = _make_identity_kwargs(monkeypatch)
    await m.start_runner("expert_implementer", "任务", skill_id="code")
    assert skill_mgr.get_skill.called


async def test_start_runner_skill_forced(monkeypatch):
    skill = MagicMock()
    skill.enabled = True
    skill.tool_rules = None
    skill.id = "code"
    _patch_cfg_method(monkeypatch, "get_forced_skill", lambda self: "code")
    m, factory, skill_mgr = _mk_manager(monkeypatch)
    skill_mgr.get_skill.return_value = skill
    factory.create_expert.return_value.identity = _make_identity_kwargs(monkeypatch)
    await m.start_runner("expert_implementer", "任务")
    assert skill_mgr.get_skill.called


async def test_start_runner_skill_disabled(monkeypatch):
    skill = MagicMock()
    skill.enabled = False
    skill.tool_rules = None
    _patch_cfg_method(monkeypatch, "get_forced_skill", lambda self: "")
    m, factory, skill_mgr = _mk_manager(monkeypatch)
    skill_mgr.get_skill.return_value = skill
    factory.create_expert.return_value.identity = _make_identity_kwargs(monkeypatch)
    mid = await m.start_runner("expert_implementer", "任务", skill_id="code")
    assert mid is not None


async def test_start_runner_skill_error(monkeypatch):
    _patch_cfg_method(monkeypatch, "get_forced_skill",
                      lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    m, factory, skill_mgr = _mk_manager(monkeypatch)
    factory.create_expert.return_value.identity = _make_identity_kwargs(monkeypatch)
    mid = await m.start_runner("expert_implementer", "任务")
    assert mid is not None


async def test_start_runner_exception(monkeypatch):
    m, factory, _ = _mk_manager(monkeypatch)
    factory.create_expert.side_effect = RuntimeError("factory down")
    assert await m.start_runner("expert_implementer", "任务") is None


async def test_stop_runner_destroy_error(monkeypatch):
    import modules.thinking.model_factory as mf_mod
    factory = MagicMock()
    factory.destroy.side_effect = RuntimeError("destroy down")
    monkeypatch.setattr(mf_mod, "get_model_factory", lambda: factory)
    m = _mgr(monkeypatch)
    r = _runner()
    r.tier = "expert"
    r.stop = AsyncMock()
    m._runners = {"m1": r}
    m._count_by_tier = {"expert": 1}
    assert await m.stop_runner("m1") is True


async def test_start_listening_external_identity_error(monkeypatch):
    import modules.thinking.identity as id_mod
    def boom(*a, **k):
        raise RuntimeError("yaml down")
    monkeypatch.setattr(id_mod, "load_external_identities", boom)
    m = _mgr(monkeypatch)
    await m.start_listening()
    assert m._running is True
    await m.stop_listening()


async def test_stop_listening_unsubscribe_error(monkeypatch):
    m = _mgr(monkeypatch)
    m._bus.unsubscribe = AsyncMock(side_effect=RuntimeError("bus down"))
    m._running = True
    await m.stop_listening()  # 不抛


# ── _listen_loop / _drain_runner_messages / terminate ─────────────────────

async def test_listen_loop_basic(monkeypatch):
    m = _mgr(monkeypatch)
    m._running = True
    m._message_event = asyncio.Event()
    m._drain_runner_messages = AsyncMock()
    counter = {"n": 0}
    def _t():
        counter["n"] += 1
        return 1000.0 if counter["n"] == 1 else 1031.0
    monkeypatch.setattr(mr_mod.time, "time", _t)
    m._sweep_orphaned_runners = MagicMock()
    task = asyncio.create_task(m._listen_loop())
    await asyncio.sleep(0.05)
    m._message_event.set()
    await asyncio.sleep(0.05)
    m._running = False
    m._message_event.set()
    await asyncio.wait_for(task, timeout=2)
    assert m._drain_runner_messages.await_count >= 1
    m._sweep_orphaned_runners.assert_called()


async def test_listen_loop_error(monkeypatch):
    m = _mgr(monkeypatch)
    m._running = True
    m._message_event = asyncio.Event()
    calls = {"n": 0}
    async def drain():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
    m._drain_runner_messages = drain
    task = asyncio.create_task(m._listen_loop())
    await asyncio.sleep(0.05)
    m._message_event.set()  # 唤醒1 → drain#1 抛异常（覆盖 2726-2727）
    await asyncio.sleep(0.05)
    m._message_event.set()  # 唤醒2 → drain#2 正常
    await asyncio.sleep(0.05)
    m._running = False
    m._message_event.set()
    await asyncio.wait_for(task, timeout=2)
    assert calls["n"] >= 2


async def test_listen_loop_cancelled(monkeypatch):
    m = _mgr(monkeypatch)
    m._running = True
    m._message_event = asyncio.Event()
    m._drain_runner_messages = AsyncMock()
    task = asyncio.create_task(m._listen_loop())
    await asyncio.sleep(0.02)
    task.cancel()
    await task  # _listen_loop 捕获 CancelledError 后正常退出


async def test_drain_messages_invalid_json(monkeypatch):
    m = _mgr(monkeypatch)
    m._running = True
    msg = type("M", (), {"content": "这不是json"})()
    m._bus.receive = AsyncMock(side_effect=[[msg], []])
    m._handle_probe_started = AsyncMock()
    m._handle_probe_stopped = AsyncMock()
    m._handle_terminate_session = AsyncMock()
    await m._drain_runner_messages()
    m._handle_probe_started.assert_not_awaited()


async def test_drain_messages_terminate(monkeypatch):
    m = _mgr(monkeypatch)
    m._running = True
    msg = type("M", (), {"content": {"action": "terminate_session", "reason": "风险"}})()
    m._bus.receive = AsyncMock(side_effect=[[msg], []])
    m._handle_terminate_session = AsyncMock()
    await m._drain_runner_messages()
    m._handle_terminate_session.assert_awaited_once()


async def test_handle_probe_started_no_model_id(monkeypatch):
    m = _mgr(monkeypatch)
    m.start_runner = AsyncMock(return_value=None)
    await m._handle_probe_started({
        "identity_key": "expert_1", "task_description": "任务", "return_to_session_id": "s1",
    })


async def test_handle_terminate_session_error(monkeypatch):
    m = _mgr(monkeypatch)
    r = _runner()
    async def boom():
        raise RuntimeError("stop failed")
    r.stop = boom
    m._runners = {"m1": r}
    m._running = True
    await m._handle_terminate_session({"reason": "风险"})
    assert m._running is False


# ── 模块级函数补充 ─────────────────────────────────────────────────────────

async def test_get_runner_manager_update_blackboard(monkeypatch):
    import threading as _t
    r = _runner()
    mgr = MagicMock()
    mgr.blackboard = MagicMock()
    mgr.turn_context = None
    mgr._runners = {"m1": r}
    monkeypatch.setattr(mr_mod, "_runner_managers", {"s1": mgr})
    monkeypatch.setattr(mr_mod, "_runner_managers_lock", _t.RLock())
    bb = MagicMock()
    tc = MagicMock()
    out = get_runner_manager("s1", blackboard=bb, turn_context=tc)
    assert out.blackboard is bb
    assert r.blackboard is bb
    assert out.turn_context is tc


async def test_remove_runner_manager_shutdown_error(monkeypatch):
    monkeypatch.setattr(mr_mod, "_runner_managers", {})
    mgr = MagicMock()
    mgr.shutdown = AsyncMock(side_effect=RuntimeError("shutdown boom"))
    monkeypatch.setattr(mr_mod, "_runner_managers", {"s1": mgr})
    await remove_runner_manager("s1")  # 不抛


async def test_reject_session_user_responses_more(monkeypatch):
    r = _runner()
    r._pending_user_responses = {}
    fut = asyncio.Future()
    fut.set_result("done")
    r._pending_user_responses = {"rid": fut}
    mgr = type("M", (), {"_runners": {"m1": r}})()
    monkeypatch.setattr(mr_mod, "_runner_managers", {"s1": mgr})
    assert reject_session_user_responses("s1") == 0
    del r._pending_user_responses
    assert reject_session_user_responses("s1") == 0


# ── 第二轮补测：_run_runtime_expert / _think_loop 续接 / 生成失败路径 ──────

def test_run_runtime_expert_persistent(monkeypatch):
    """persistent 专家 → run_loop（check_messages_fn + task_description）"""
    r = _runner()
    r.model_id = "m1"
    r._task_description = "任务"
    calls = {}
    class FakeExpert:
        is_persistent = True
        def __init__(self, **kw):
            self.identity = MagicMock()
            self.identity.role = "monitor"
        async def run_loop(self, check_messages_fn, task_description):
            calls["fn"] = check_messages_fn
            calls["desc"] = task_description
    r._check_messages = lambda: None
    asyncio.run(r._run_runtime_expert(FakeExpert))
    assert calls["desc"] == "任务"
    assert callable(calls["fn"])


def test_run_runtime_expert_on_demand_notify_fail(monkeypatch):
    """on_demand 专家：通知 orchestrator 失败（bus.send 抛）→ 332-333 不致命"""
    import modules.thinking.communication.interface as iface_mod
    bus = MagicMock()
    bus.send = AsyncMock(side_effect=RuntimeError("send down"))
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)
    from types import SimpleNamespace
    monkeypatch.setattr(iface_mod, "Message", MagicMock)
    monkeypatch.setattr(iface_mod, "MessageType", SimpleNamespace(SYSTEM="SYSTEM"))
    r = _runner()
    r.model_id = "m1"
    r.session_id = "s1"
    r.tier = "expert"
    r._task_description = "任务"
    r._task_id = "t1"
    r._return_to_model_id = ""
    class FakeExpert:
        is_persistent = False
        def __init__(self, **kw):
            self.identity = MagicMock()
            self.identity.role = "code_writer"
        async def run_cli_mode(self, **kw):
            return {"success": True, "result": "结果", "iterations": 1, "tool_calls": 0}
    asyncio.run(r._run_runtime_expert(FakeExpert))  # 不抛


def test_run_runtime_expert_on_demand_wakeup_fail(monkeypatch):
    """on_demand 专家：notify 成功但唤醒失败 → 359-360"""
    import modules.thinking.communication.interface as iface_mod
    bus = MagicMock()
    sent = []
    async def _send(msg):
        sent.append(msg)
        if len(sent) == 2:
            raise RuntimeError("wakeup send down")
    bus.send = _send
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)
    from types import SimpleNamespace
    monkeypatch.setattr(iface_mod, "Message", MagicMock)
    monkeypatch.setattr(iface_mod, "MessageType", SimpleNamespace(SYSTEM="SYSTEM"))
    r = _runner()
    r.model_id = "m1"
    r.session_id = "s1"
    r.tier = "expert"
    r._task_description = "任务"
    r._task_id = "t1"
    r._return_to_model_id = "large_primary"
    class FakeExpert:
        is_persistent = False
        def __init__(self, **kw):
            self.identity = MagicMock()
            self.identity.role = "code_writer"
        async def run_cli_mode(self, **kw):
            return {"success": True, "result": "结果", "iterations": 1, "tool_calls": 0}
    asyncio.run(r._run_runtime_expert(FakeExpert))  # 不抛


async def test_think_loop_continuation(monkeypatch):
    """large 委托后等待唤醒 → 续接一轮后退出（474-492）"""
    r = _runner(tier="large")
    r._running = True
    r._task_description = "任务"
    r._task_id = "t1"
    r.identity_key = ""
    thinker = MagicMock()
    delegations = {"d1": {"status": "pending"}}
    thinker._pending_delegations = delegations
    state = {"called": 0}

    async def _ct(prompt):
        state["called"] += 1
        if state["called"] == 1:
            return [{"t": "1"}]
        delegations.clear()
        return []

    thinker.continuous_think = _ct
    thinker.reset_for_continuation = MagicMock()
    thinker.add_external_prompt = MagicMock()
    CT = MagicMock(return_value=thinker)
    import modules.thinking.core.continuous_thinker as ct_mod
    monkeypatch.setattr(ct_mod, "ContinuousThinker", CT)
    import modules.thinking.communication.interface as iface_mod
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)
    r._write_final_result = AsyncMock()
    r._notify_thinking_complete = AsyncMock()
    r._wait_for_wakeup_event = AsyncMock(return_value="【进度汇报】专家运行中")
    await r._think_loop()
    thinker.reset_for_continuation.assert_called_once()
    thinker.add_external_prompt.assert_called_once()
    assert "进度汇报" in r._task_description


async def test_think_loop_exception_break(monkeypatch):
    """continuous_think 抛非取消异常 → break 退出（450-454）"""
    r = _runner(tier="expert")
    r._running = True
    r._task_description = "任务"
    r._task_id = "t1"
    r.identity_key = ""
    thinker = MagicMock()
    thinker.continuous_think = AsyncMock(side_effect=RuntimeError("boom"))
    CT = MagicMock(return_value=thinker)
    import modules.thinking.core.continuous_thinker as ct_mod
    monkeypatch.setattr(ct_mod, "ContinuousThinker", CT)
    import modules.thinking.communication.interface as iface_mod
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)
    r._write_final_result = AsyncMock()
    r._notify_thinking_complete = AsyncMock()
    await r._think_loop()
    r._notify_thinking_complete.assert_awaited_once()


async def test_think_loop_subscribe_fail(monkeypatch):
    """MessageBus 订阅失败 → 回退轮询（424-425）"""
    r = _runner(tier="expert")
    r._running = True
    r._task_description = "任务"
    r._task_id = "t1"
    r.identity_key = ""
    thinker = MagicMock()
    thinker.continuous_think = AsyncMock(return_value=[])
    thinker._pending_delegations = {}
    CT = MagicMock(return_value=thinker)
    import modules.thinking.core.continuous_thinker as ct_mod
    monkeypatch.setattr(ct_mod, "ContinuousThinker", CT)
    import modules.thinking.communication.interface as iface_mod
    bus = MagicMock()
    bus.subscribe = AsyncMock(side_effect=RuntimeError("bus down"))
    bus.unsubscribe = AsyncMock()
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)
    r._write_final_result = AsyncMock()
    r._notify_thinking_complete = AsyncMock()
    await r._think_loop()


# ── 前端握手 / 查询工具未知 / 门控上下文 / 结果写入 补充 ────────────────────

async def test_generate_frontend_handshake_error(monkeypatch):
    """confirm_frontend_connection 抛异常 → 握手失败走 1231-1232"""
    import modules.thinking.frontend_channel as fc_mod
    def boom(sid):
        raise RuntimeError("channel down")
    monkeypatch.setattr(fc_mod, "confirm_frontend_connection", boom)
    r = _runner(tier="expert")
    r._supports_native_tool_chat = lambda c: False
    client = MagicMock()
    client.generate = AsyncMock(return_value="结果")
    r.instance.client = client
    out = await r._generate("提示")
    assert out == "结果"


async def test_generate_with_tools_query_unknown_tool(monkeypatch):
    """query_tool_details 目标工具不存在 → 列出可用工具（1747-1752）"""
    import infra.tool_manager.tool_registry as tr_mod
    monkeypatch.setattr(tr_mod.ToolRegistry, "get_tool", staticmethod(lambda name: None))
    monkeypatch.setattr(tr_mod.ToolRegistry, "_tools", {"calc": object(), "todo": object()})
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(
        _resp(content="", calls=[_tc("query_tool_details", '{"tool_name": "ghost"}')]),
        _resp(content="继续"),
    )
    out = await r._generate_with_tools("system", "user", client)
    assert "继续" in out


async def test_generate_with_tools_request_skill_forced_error(monkeypatch):
    """request_skill 读取 forced_skill 抛异常 → 1803-1804"""
    _patch_cfg_method(monkeypatch, "get_forced_skill",
                      lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    skill = MagicMock()
    skill.id = "code"
    skill.name = "代码专家"
    skill.enabled = True
    skill.description = "d"
    skill.tool_rules = None
    _stub_skills(monkeypatch, skill)
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("request_skill", '{"skill_id": "code"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "代码专家" in out


async def test_generate_with_tools_stop_skill_forced_error(monkeypatch):
    """stop_skill 读取 forced_skill 抛异常 → 1827-1828"""
    _patch_cfg_method(monkeypatch, "get_forced_skill",
                      lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    skill = MagicMock()
    skill.id = "code"
    skill.name = "代码专家"
    r, mcp = _tool_runner(monkeypatch, tier="large")
    r._active_skill = skill
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("stop_skill", '{"reason": "x"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "无活跃技能" in out


async def test_generate_with_tools_delegate_mode_get_error(monkeypatch):
    """delegate_task 读取执行模式抛异常 → 1901-1902 走 edit 默认"""
    import importlib
    cfg_mod = importlib.import_module("config.settings")
    class _BadMode:
        def __init__(self):
            self.n = 0
        @property
        def effective_execution_mode(self):
            self.n += 1
            # 1552(perm_ctrl) / 1586(turn 内模式检查) 用 edit；第3次(1899 delegate)抛
            if self.n >= 3:
                raise RuntimeError("mode down")
            return "edit"
        @property
        def is_delegation_available(self):
            return True
    monkeypatch.setattr(cfg_mod, "settings", _BadMode())
    _stub_delegation(monkeypatch)
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("delegate_task", '{"role": "expert_1", "task": "任务"}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "委托" in out


async def test_generate_with_tools_gate_ctx_messages(monkeypatch):
    """安全门控 dialog_context 含消息（_format_messages_for_context）→ 2125"""
    import modules.thinking.communication.interface as iface_mod
    bus = MagicMock()
    msg = MagicMock()
    msg.sender = "expert_1"
    msg.content = "之前的结论"
    msg.msg_type = MagicMock()
    msg.msg_type.value = "expert_result"
    bus.receive = AsyncMock(return_value=[msg])
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = _chat_client(
        _resp(content="", calls=[_tc("calc", '{"a":1,"op":"+","b":1}')]),
        _resp(content="完成"),
    )
    out = await r._generate_with_tools("system", "user", client)
    assert "完成" in out


async def test_write_final_result_extraction_fail(monkeypatch):
    """专家/主管记忆提取失败 → 690-693 不致命"""
    r = _runner(tier="expert")
    r._task_description = "任务"
    r._task_id = "t1"
    snap = MagicMock()
    snap.final_result = "观察内容"
    snap.control_decision = None
    thinker = MagicMock()
    thinker.get_process_snapshot.return_value = snap
    r._thinker = thinker
    bb = MagicMock()
    r.blackboard = bb
    import modules.thinking.api_stream as stream_mod
    async def boom(*a, **k):
        raise RuntimeError("extraction down")
    monkeypatch.setattr(stream_mod, "_post_task_extraction_helper", boom)
    await r._write_final_result()
    bb.add_observation.assert_called_once()


async def test_write_final_result_snapshot_fail_history(monkeypatch):
    """快照失败 → 从思考历史恢复（609-611 + 620-623）"""
    r = _runner(tier="expert")
    r._task_description = "任务"
    r._task_id = "t1"
    thinker = MagicMock()
    thinker.get_process_snapshot.side_effect = RuntimeError("snapshot down")
    thinker.history_thoughts = ["最后一轮思考"]
    r._thinker = thinker
    bb = MagicMock()
    r.blackboard = bb
    import modules.thinking.api_stream as stream_mod
    monkeypatch.setattr(stream_mod, "_post_task_extraction_helper", AsyncMock())
    await r._write_final_result()
    bb.add_observation.assert_called_once()


async def test_write_final_result_no_model_id(monkeypatch):
    """model_id 为空 → 提前返回（635）"""
    r = _runner(tier="expert")
    r.model_id = ""
    r._task_description = "任务"
    r._task_id = "t1"
    snap = MagicMock()
    snap.final_result = "结果"
    snap.control_decision = None
    thinker = MagicMock()
    thinker.get_process_snapshot.return_value = snap
    r._thinker = thinker
    await r._write_final_result()  # 直接返回


async def test_notify_thinking_complete_send_fail(monkeypatch):
    """thinking_complete 发送失败 → 992-993 不致命"""
    import modules.thinking.communication.interface as iface_mod
    bus = MagicMock()
    bus.send = AsyncMock(side_effect=RuntimeError("send down"))
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)
    r = _runner()
    r._task_id = "t1"
    await r._notify_thinking_complete()  # 不抛


async def test_save_partial_result_empty(monkeypatch):
    """无历史且无流式内容 → 557 直接返回"""
    r = _runner()
    r._thinker = MagicMock()
    r._thinker.history_thoughts = [None, ""]
    r._current_streaming_content = "   "
    await r._save_partial_result()  # parts 为空


async def test_save_partial_result_error(monkeypatch):
    """保存部分输出时黑板写入失败 → 585-586 不致命"""
    r = _runner()
    r._thinker = MagicMock()
    r._thinker.history_thoughts = ["第一轮"]
    r._current_streaming_content = ""
    r.blackboard.set_final_response.side_effect = RuntimeError("bb down")
    await r._save_partial_result()  # 不抛


# ── Manager 异常路径补充 ───────────────────────────────────────────────────

async def test_start_runner_agent_active_error(monkeypatch):
    """get_agent_active 抛异常 → 2456-2457 不拦截"""
    _patch_cfg_method(monkeypatch, "get_agent_active",
                      lambda self, key: (_ for _ in ()).throw(RuntimeError("boom")))
    m, factory, _ = _mk_manager(monkeypatch)
    factory.create_expert.return_value.identity = _make_identity_kwargs(monkeypatch)
    mid = await m.start_runner("expert_implementer", "任务")
    assert mid is not None


async def test_start_runner_skill_get_error(monkeypatch):
    """skill_manager.get_skill 抛异常 → 2552-2553 不致命"""
    _patch_cfg_method(monkeypatch, "get_forced_skill", lambda self: "")
    m, factory, skill_mgr = _mk_manager(monkeypatch)
    skill_mgr.get_skill.side_effect = RuntimeError("skill mgr down")
    factory.create_expert.return_value.identity = _make_identity_kwargs(monkeypatch)
    mid = await m.start_runner("expert_implementer", "任务", skill_id="code")
    assert mid is not None


async def test_reject_session_user_responses_error(monkeypatch):
    """reject_session_user_responses 处理抛异常 → 2985-2986"""
    r = _runner()
    bad = object()  # 无 .done() 的对象
    r._pending_user_responses = {"rid": bad}
    mgr = type("M", (), {"_runners": {"m1": r}})()
    monkeypatch.setattr(mr_mod, "_runner_managers", {"s1": mgr})
    assert reject_session_user_responses("s1") == 0


def test_runner_managers_lock_is_rlock():
    import _thread
    assert isinstance(mr_mod._runner_managers_lock, _thread.RLock)


def test_push_expert_output_supervisor():
    """主管输出 → 前端独立气泡（role=supervisor，带身份）"""
    from unittest.mock import MagicMock, patch
    from modules.thinking.core.model_runner import ModelRunner
    r = ModelRunner.__new__(ModelRunner)
    r.model_id = "sup_1"; r.session_id = "s1"; r.tier = "supervisor"
    r.identity = MagicMock(); r.identity.name = "代码主管"; r.identity.tier = "supervisor"

    sent = []
    fake_cm = MagicMock()
    fake_cm.active_connections = {"ws1": object()}
    fake_cm.send_json_from_thread = lambda sid, ev: sent.append((sid, ev))
    with patch("modules.thinking.api_stream.connection_manager", fake_cm), \
         patch("modules.thinking.api_stream._build_event") as be:
        be.return_value = {"role": "supervisor", "data": {"identity_name": "代码主管"}}
        r._push_expert_output("正在拆分任务")
        r._push_expert_output("  ")  # 空白不推
    assert len(sent) == 1
    assert sent[0][1]["role"] == "supervisor"
    assert sent[0][1]["data"]["identity_name"] == "代码主管"


def test_push_expert_output_expert():
    """专家输出 → 前端独立气泡（role=expert）"""
    from unittest.mock import MagicMock, patch
    from modules.thinking.core.model_runner import ModelRunner
    r = ModelRunner.__new__(ModelRunner)
    r.model_id = "expert_1"; r.session_id = "s1"; r.tier = "expert"
    r.identity = MagicMock(); r.identity.name = "实现专家"; r.identity.tier = "expert"

    sent = []
    fake_cm = MagicMock()
    fake_cm.active_connections = {"ws1": object()}
    fake_cm.send_json_from_thread = lambda sid, ev: sent.append((sid, ev))
    with patch("modules.thinking.api_stream.connection_manager", fake_cm), \
         patch("modules.thinking.api_stream._build_event") as be:
        be.return_value = {"role": "expert", "data": {"identity_name": "实现专家"}}
        r._push_expert_output("已完成实现")
    assert len(sent) == 1
    assert sent[0][1]["role"] == "expert"


# ── 断点续思考（超时后从已保存上下文继续） ────────────────────────────────

async def test_generate_with_tools_resume_from_checkpoint(monkeypatch):
    """断点续思考：_resume_requested 时从已保存上下文快照继续，而非从头重建"""
    r, mcp = _tool_runner(monkeypatch, tier="large")
    r._resume_requested = True
    r._resume_context = [
        ChatMessage(role="user", content="旧思考上下文").to_dict(),
        ChatMessage(role="assistant", content="已有结论").to_dict(),
    ]
    client = _chat_client(_resp(content="继续完成"))
    out = await r._generate_with_tools("system", "user", client)
    assert "继续完成" in out
    sent = client.chat.call_args.kwargs["messages"]
    assert sent[0].role == "user"
    assert "旧思考上下文" in sent[0].content
    assert "直接继续" in sent[-1].content
    # 断点已被消费，避免重复恢复
    assert r._resume_requested is False


async def test_generate_with_tools_no_checkpoint_normal_flow(monkeypatch):
    """无断点快照（首次思考）时走正常 system+user 初始化"""
    r, mcp = _tool_runner(monkeypatch, tier="large")
    r._resume_requested = True
    r._resume_context = None  # 未保存断点 → 正常流程
    client = _chat_client(_resp(content="ok"))
    out = await r._generate_with_tools("system", "user", client)
    assert "ok" in out
    sent = client.chat.call_args.kwargs["messages"]
    assert sent[0].role == "system"
    assert sent[1].role == "user"


async def test_generate_with_tools_saves_resume_context(monkeypatch):
    """工具循环每轮开头保存断点快照到 runner 与黑板（含落库调用）"""
    r, mcp = _tool_runner(monkeypatch, tier="large")
    blackboard = MagicMock()
    blackboard.resume_context = None
    r.blackboard = blackboard
    client = _chat_client(
        _resp(content=None, calls=[_tc("calc", '{"a":1,"op":"+","b":1}')]),
        _resp(content="完成"),
    )
    out = await r._generate_with_tools("system", "user", client)
    assert "完成" in out
    assert r._resume_context and len(r._resume_context) >= 2
    assert blackboard.resume_context == r._resume_context
    blackboard.persist.assert_called()


# ── 委托链工具：query_delegation / resume_delegation ───────────────────────

def _real_bb():
    from modules.thinking.cognition.blackboard import CognitiveBlackboard
    return CognitiveBlackboard(session_id="s1", turn_id="t1")


async def test_query_delegation_missing_context_limit(monkeypatch):
    """query_delegation 必须传 context_limit（截取参数必填）"""
    r, mcp = _tool_runner(monkeypatch, tier="large")
    r.blackboard = _real_bb()
    out = await r._handle_query_delegation({"delegation_id": "p1"})
    assert "context_limit" in out
    out2 = await r._handle_query_delegation({"context_limit": 1000})
    assert "delegation_id" in out2


async def test_query_delegation_not_found_lists(monkeypatch):
    """委托不存在时列出当前委托"""
    r, mcp = _tool_runner(monkeypatch, tier="large")
    bb = _real_bb()
    bb.write_delegation("ex", "任务A", probe_id="p-a")
    r.blackboard = bb
    out = await r._handle_query_delegation({"delegation_id": "ghost", "context_limit": 1000})
    assert "未找到委托" in out
    assert "p-a" in out


async def test_query_delegation_returns_context(monkeypatch):
    """正常返回委托进度+上下文（按 context_limit 截取）"""
    r, mcp = _tool_runner(monkeypatch, tier="large")
    bb = _real_bb()
    did = bb.write_delegation("ex", "分析性能问题", probe_id="p-c",
                              caller_model_id="large_primary")
    bb.update_delegation_progress(did, progress="分析中", status="running")
    r.blackboard = bb
    out = await r._handle_query_delegation({"delegation_id": "p-c", "context_limit": 5000})
    assert "分析性能问题" in out
    assert "分析中" in out
    assert "large_primary" in out
    # 截取生效
    short = await r._handle_query_delegation({"delegation_id": "p-c", "context_limit": 30})
    assert "已截断" in short


async def test_resume_delegation_default_return_to(monkeypatch):
    """resume_delegation 未传 return_to → 默认返回原委托者"""
    import modules.thinking.core.delegation_port as dp_mod
    captured = {}

    async def fake_delegate(self, request):
        captured["request"] = request
        return dp_mod.DelegationResult(success=True, probe_id="probe_new",
                                       metadata={"probe_id": "probe_new", "task_id": "task_root"})

    monkeypatch.setattr(dp_mod.ProbeDelegationAdapter, "delegate", fake_delegate)
    r, mcp = _tool_runner(monkeypatch, tier="supervisor")
    bb = _real_bb()
    did = bb.write_delegation("ex", "原任务", probe_id="p-orig",
                              caller_model_id="large_primary",
                              return_to_model_id="large_primary",
                              origin_task_id="task_root")
    r.blackboard = bb
    r._delegation_id = "p-me"
    r._origin_task_id = "task_root"
    out = await r._handle_resume_delegation({"delegation_id": did})
    assert "已继续委托" in out
    req = captured["request"]
    assert req.role == "ex"
    assert req.return_to_model_id == "large_primary"  # 默认原委托者
    assert req.task_id == "task_root"
    # 委托链更新：标记 running + 新委托
    d = bb.get_delegation(did)
    assert d["status"] == "running"
    assert "probe_new" in d["progress"]


async def test_resume_delegation_explicit_return_to(monkeypatch):
    """resume_delegation 传了 return_to 则用指定值"""
    import modules.thinking.core.delegation_port as dp_mod
    captured = {}

    async def fake_delegate(self, request):
        captured["request"] = request
        return dp_mod.DelegationResult(success=True, probe_id="probe_new2",
                                       metadata={"probe_id": "probe_new2", "task_id": "task_root"})

    monkeypatch.setattr(dp_mod.ProbeDelegationAdapter, "delegate", fake_delegate)
    r, mcp = _tool_runner(monkeypatch, tier="supervisor")
    bb = _real_bb()
    did = bb.write_delegation("ex", "原任务", probe_id="p-orig2",
                              caller_model_id="large_primary",
                              return_to_model_id="large_primary")
    r.blackboard = bb
    out = await r._handle_resume_delegation(
        {"delegation_id": did, "return_to_model_id": "another_001"}
    )
    assert captured["request"].return_to_model_id == "another_001"


async def test_resume_delegation_not_found(monkeypatch):
    """委托不存在返回失败"""
    r, mcp = _tool_runner(monkeypatch, tier="supervisor")
    r.blackboard = _real_bb()
    out = await r._handle_resume_delegation({"delegation_id": "ghost"})
    assert "未找到委托" in out


async def test_delegate_task_records_chain(monkeypatch):
    """delegate_task 分发成功时把委托链写入黑板"""
    import modules.thinking.core.delegation_port as dp_mod
    async def fake_delegate(self, request):
        return dp_mod.DelegationResult(
            success=True, probe_id="probe_x",
            metadata={"probe_id": "probe_x", "task_id": "task_t1", "target_tier": "expert"},
        )
    monkeypatch.setattr(dp_mod.ProbeDelegationAdapter, "delegate", fake_delegate)
    r, mcp = _tool_runner(monkeypatch, tier="large")
    bb = _real_bb()
    r.blackboard = bb
    r._delegation_id = "p-parent"
    r._origin_task_id = "task_root"
    client = _chat_client(_resp(
        content=None,
        calls=[_tc("delegate_task", '{"role": "expert_code_writer", "task": "实现X", "wait_seconds": 120}')],
    ))
    out = await r._generate_with_tools("system", "user", client)
    assert "委托" in out
    d = bb.get_delegation("probe_x")
    assert d is not None
    assert d["role"] == "expert_code_writer"
    assert d["caller_model_id"] == "large_primary"
    assert d["parent_delegation_id"] == "p-parent"
    assert d["origin_task_id"] == "task_root"
    assert d["return_to_model_id"] == "large_primary"


class _FakeOnDemandExpert:
    """轻量 on_demand 专家：run_cli_mode 返回固定结果"""
    is_persistent = False

    def __init__(self, **kwargs):
        self.identity = MagicMock()
        self.identity.role = "security_monitor"
        self.run_cli_mode = AsyncMock(return_value={
            "success": True, "result": "监控完成", "iterations": 2, "tool_calls": 3,
        })


async def test_runtime_expert_thinking_result_uses_probe_id(monkeypatch):
    """RuntimeExpert 完成通知上级时，delegation_id 用 probe_id（委托链 key）而非 task_id"""
    import modules.thinking.communication.interface as iface_mod
    bus = MagicMock()
    bus.send = AsyncMock()
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)

    expert_cls = _FakeOnDemandExpert

    r = _runner(tier="expert")
    r._delegation_id = "probe_monitor_abc123"
    r._task_id = "task_root_1"
    r._return_to_model_id = "large_primary"
    r.identity.role = "security_monitor"

    await r._run_runtime_expert(expert_cls)

    sent = [c.args[0] for c in bus.send.call_args_list]
    result_msgs = [m for m in sent if m.content.get("action") == "thinking_result"]
    assert len(result_msgs) == 1
    content = result_msgs[0].content
    assert content["delegation_id"] == "probe_monitor_abc123"  # 委托链 key
    assert content["task_id"] == "task_root_1"                 # 上级 pending 匹配
    assert content["result"] == "监控完成"


# ── 上下文 90% 自动总结（工具循环） ────────────────────────────────────────

async def test_maybe_summarize_context_threshold(monkeypatch):
    """上下文超 90% 时自动总结并替换 messages + 落黑板"""
    r, mcp = _tool_runner(monkeypatch, tier="large")
    # 真实小黑板（落 observation）
    bb = _real_bb()
    r.blackboard = bb
    # 窗口 100 → 阈值 90；fake compression estimate_tokens=123 → 触发
    r._thinker = None
    from modules.thinking.identity import ModelIdentity
    r.instance.identity = ModelIdentity(model_id="large_primary", tier="large", context_length=100)
    client = MagicMock()
    client.supports_native_tools = True
    client.chat_stream = AsyncMock(return_value=_resp(content="总结摘要"))
    r.instance.client = client
    messages = [ChatMessage(role="system", content="你是助手。"),
                ChatMessage(role="user", content="请完成一个非常长的任务。")]
    ok = await r._maybe_summarize_context(messages, "原任务")
    assert messages[1].role == "user"
    assert "自动总结" in messages[1].content or "总结" in messages[1].content
    # 落黑板 observation
    obs = [o for o in bb.observations if o.metadata.get("context_type") == "tool_loop_summary"]
    assert len(obs) == 1
    assert "总结摘要" in obs[0].content


async def test_maybe_summarize_context_below(monkeypatch):
    """上下文未超 90% 不触发总结"""
    r, mcp = _tool_runner(monkeypatch, tier="large")
    from modules.thinking.identity import ModelIdentity
    r.instance.identity = ModelIdentity(model_id="large_primary", tier="large", context_length=100000)
    messages = [ChatMessage(role="system", content="你是助手。"),
                ChatMessage(role="user", content="你好")]
    ok = await r._maybe_summarize_context(messages, "原任务")
    assert ok is False


async def test_maybe_summarize_context_fail_fallback(monkeypatch):
    """总结调用失败/返回空 → 不替换 messages（继续原上下文）"""
    r, mcp = _tool_runner(monkeypatch, tier="large")
    from modules.thinking.identity import ModelIdentity
    r.instance.identity = ModelIdentity(model_id="large_primary", tier="large", context_length=50)
    client = MagicMock()
    client.chat_stream = AsyncMock(return_value=_resp(content=None))
    r.instance.client = client
    messages = [ChatMessage(role="system", content="你是助手。"),
                ChatMessage(role="user", content="长任务。" * 30)]
    ok = await r._maybe_summarize_context(messages, "原任务")
    assert ok is False
    assert len(messages) == 2  # 未替换
    assert "长任务" in messages[1].content


# ── read_context：按轮次读取黑板记忆 ───────────────────────────────────────

async def test_handle_read_context_rounds(monkeypatch):
    """read_context 按指定轮次范围返回对话，并设置后续读取范围"""
    r, mcp = _tool_runner(monkeypatch, tier="large")
    bb = _real_bb()
    for i in range(1, 6):
        bb.write_thought(f"m{i}", "large", f"第{i}轮内容", round_num=i)
    r.blackboard = bb
    out = await r._handle_read_context({"round_start": 2, "round_end": 4, "context_limit": 3000})
    assert "轮2" in out
    assert "第2轮内容" in out
    assert "第5轮内容" not in out
    assert r._dialog_round_start == 2
    assert r._dialog_round_end == 4


async def test_handle_read_context_empty(monkeypatch):
    """指定轮次无记录时返回提示"""
    r, mcp = _tool_runner(monkeypatch, tier="large")
    r.blackboard = _real_bb()
    out = await r._handle_read_context({"round_start": 100, "round_end": 200})
    assert "无对话记录" in out


async def test_notify_timeout_to_parent(monkeypatch):
    """委托等待超时 → 自动激活上一级（带进度 + 委托 id + timeout 标记）"""
    import modules.thinking.communication.interface as iface_mod
    bus = MagicMock()
    bus.send = AsyncMock()
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)
    r = _runner(tier="supervisor")
    r._return_to_model_id = "large_primary"
    r._delegation_id = "probe_expert_x"
    r._task_id = "task_root_1"
    r._collect_expert_progress = AsyncMock(return_value="专家执行中")
    await r._notify_timeout_to_parent()
    sent = [c.args[0] for c in bus.send.call_args_list]
    assert len(sent) == 1
    content = sent[0].content
    assert content["action"] == "thinking_result"
    assert content["timeout"] is True
    assert content["delegation_id"] == "probe_expert_x"
    assert content["task_id"] == "task_root_1"
    assert "超时" in content["result"]
    assert "resume_delegation" in content["result"]
    assert sent[0].recipient == "large_primary"


# ── 关键边界补充 ───────────────────────────────────────────────────────────

async def test_summarize_client_error_returns_empty(monkeypatch):
    """_summarize 客户端抛异常 → 返回空（不替换上下文）"""
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = MagicMock()
    client.supports_native_tools = True
    client.chat_stream = AsyncMock(side_effect=RuntimeError("api down"))
    r.instance.client = client
    out = await r._summarize([ChatMessage(role="user", content="内容")])
    assert out == ""


async def test_maybe_summarize_at_exact_threshold(monkeypatch):
    """估算恰好等于阈值（90%）→ 不触发总结（<= 不总结）"""
    r, mcp = _tool_runner(monkeypatch, tier="large")
    from modules.thinking.identity import ModelIdentity
    r.instance.identity = ModelIdentity(model_id="large", tier="large", context_length=100)
    # fake engine 返回 90 → 等于 threshold=90 → 不触发
    import modules.thinking.context.compression as cc
    engine = MagicMock()
    engine.estimate_tokens.return_value = 90
    monkeypatch.setattr(cc, "get_compression_engine", lambda: engine)
    messages = [ChatMessage(role="system", content="s"), ChatMessage(role="user", content="u")]
    ok = await r._maybe_summarize_context(messages, "原任务")
    assert ok is False


async def test_maybe_summarize_empty_messages(monkeypatch):
    """messages 为空 → 直接返回 False 不总结"""
    r, mcp = _tool_runner(monkeypatch, tier="large")
    ok = await r._maybe_summarize_context([], "原任务")
    assert ok is False


async def test_notify_timeout_no_parent(monkeypatch):
    """无上级（return_to_model_id 为空）→ 不发送，安全跳过"""
    import modules.thinking.communication.interface as iface_mod
    bus = MagicMock()
    bus.send = AsyncMock()
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)
    r = _runner(tier="expert")
    r._return_to_model_id = ""
    await r._notify_timeout_to_parent()
    assert bus.send.call_count == 0


async def test_handle_read_context_no_blackboard(monkeypatch):
    """无黑板 → 返回读取失败"""
    r, mcp = _tool_runner(monkeypatch, tier="large")
    r.blackboard = None
    out = await r._handle_read_context({"round_start": 1, "round_end": 2})
    assert "读取失败" in out


async def test_handle_read_context_limit_clamp(monkeypatch):
    """context_limit 超范围被 clamp 到 500-10000"""
    r, mcp = _tool_runner(monkeypatch, tier="large")
    bb = _real_bb()
    bb.write_thought("m", "large", "内容", round_num=1)
    r.blackboard = bb
    out = await r._handle_read_context({"round_start": 1, "round_end": 1, "context_limit": 10})
    assert "已截断" in out or "对话记录" in out


async def test_resume_delegation_missing_id(monkeypatch):
    """resume_delegation 缺 delegation_id → 返回失败"""
    r, mcp = _tool_runner(monkeypatch, tier="supervisor")
    r.blackboard = _real_bb()
    out = await r._handle_resume_delegation({})
    assert "delegation_id" in out


async def test_summarize_chat_path_no_stream(monkeypatch):
    """client 无 chat_stream（走 chat 非流式）时 _summarize 正常返回摘要"""
    r, mcp = _tool_runner(monkeypatch, tier="large")
    client = MagicMock()
    client.supports_native_tools = True
    delattr(client, "chat_stream")  # 非流式
    client.chat = AsyncMock(return_value=_resp(content="非流式摘要"))
    r.instance.client = client
    out = await r._summarize([ChatMessage(role="user", content="上下文")])
    assert out == "非流式摘要"
    client.chat.assert_awaited_once()


async def test_maybe_summarize_system_prompt_fallback(monkeypatch):
    """messages 首条非 system 时用默认系统提示（不崩）"""
    r, mcp = _tool_runner(monkeypatch, tier="large")
    from modules.thinking.identity import ModelIdentity
    r.instance.identity = ModelIdentity(model_id="large", tier="large", context_length=100)
    r._summarize = AsyncMock(return_value="摘要")
    messages = [ChatMessage(role="user", content="没有 system 开头")]  # 首条非 system
    ok = await r._maybe_summarize_context(messages, "任务")
    assert ok is True
    assert messages[0].role == "system"
    assert "你是智能助手" in messages[0].content


async def test_think_loop_wait_timeout_uses_tool_wait(monkeypatch):
    """委托等待超时使用工具设置的 wait_seconds（非硬编码）；超时后自动激活上级"""
    r = _runner(tier="large")
    r._running = True
    r._task_description = "任务"
    r._task_id = "t1"
    r.identity_key = ""
    r._return_to_model_id = "supervisor_001"
    r._wakeup_event = FakeEvent()

    thinker = MagicMock()
    thinker._pending_delegations = {"task_x": {"status": "pending"}}
    from modules.thinking.core.continuous_thinker import ThinkingControlDecision
    thinker._last_control_decision = ThinkingControlDecision(
        should_continue=True, wait_seconds=45, reason="", result_summary="", delegations=[], raw={},
    )
    thinker.continuous_think = AsyncMock(return_value=[])
    CT = MagicMock(return_value=thinker)
    import modules.thinking.core.continuous_thinker as ct_mod
    monkeypatch.setattr(ct_mod, "ContinuousThinker", CT)

    import modules.thinking.communication.interface as iface_mod
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: bus)

    r._write_final_result = AsyncMock()
    r._notify_thinking_complete = AsyncMock()
    r._notify_timeout_to_parent = AsyncMock()
    captured = {}

    async def fake_wait(**kw):
        captured["timeout"] = kw.get("timeout")
        return None  # 等待超时

    r._wait_for_wakeup_event = fake_wait

    await r._think_loop()
    assert captured.get("timeout") == 45.0  # 用工具设置的 wait_seconds，而非硬编码 300
    r._notify_timeout_to_parent.assert_awaited_once()


def test_max_chat_tool_turns_default_300():
    """工具循环防死循环兜底轮数为 300（上下文超 90% 自动总结，不再 25 轮硬限）"""
    import modules.thinking.core.model_runner as mr
    assert mr.ModelRunner.MAX_CHAT_TOOL_TURNS == 300


async def test_maybe_summarize_syncs_context_tokens(monkeypatch):
    """上下文检查时把 messages token 估算同步到 thinker._context_tokens（供前端展示真实占用）"""
    r, mcp = _tool_runner(monkeypatch, tier="large")
    from modules.thinking.identity import ModelIdentity
    r.instance.identity = ModelIdentity(model_id="large", tier="large", context_length=100000)
    thinker = MagicMock()
    r._thinker = thinker
    # fake engine 返回 123
    import modules.thinking.context.compression as cc
    engine = MagicMock()
    engine.estimate_tokens.return_value = 123
    monkeypatch.setattr(cc, "get_compression_engine", lambda: engine)
    messages = [ChatMessage(role="system", content="s"), ChatMessage(role="user", content="u")]
    await r._maybe_summarize_context(messages, "任务")
    # 即使未触发总结（未超 90%），也同步了 context_tokens
    assert thinker._context_tokens == 123


async def test_push_todo_update(monkeypatch):
    """todo 变更 → 推送 type='todo' 事件给前端（替代轮询）"""
    import modules.thinking.api_stream as api
    sent = []
    class _CM:
        active_connections = {"s1": object()}
        @staticmethod
        def send_json_from_thread(sid, event, timeout=5.0):
            sent.append((sid, event))
    monkeypatch.setattr(api, "connection_manager", _CM)
    monkeypatch.setattr(api, "_build_event", lambda **kw: kw)
    r, mcp = _tool_runner(monkeypatch, tier="expert")
    r.session_id = "s1"
    r._push_todo_update()
    assert len(sent) == 1
    sid, ev = sent[0]
    assert sid == "s1"
    assert ev["msg_type"] == "todo"
    assert ev["event"] == "todo_changed"


async def test_todo_tool_execution_triggers_push(monkeypatch):
    """模型调用 todo 工具成功 → _generate_with_tools 自动触发 _push_todo_update"""
    r, mcp = _tool_runner(monkeypatch, tier="large")
    pushed = {"n": 0}
    r._push_todo_update = lambda: pushed.__setitem__("n", pushed["n"] + 1)
    # MCP execute 返回 todo 成功
    mcp.execute.return_value = type("R", (), {"success": True, "result": '{"action":"create","items":[]}'})()
    client = _chat_client(
        _resp(content=None, calls=[_tc("todo", '{"action":"list"}')]),
        _resp(content="完成"),
    )
    out = await r._generate_with_tools("system", "user", client)
    assert "完成" in out
    assert pushed["n"] == 1  # todo 工具执行成功触发一次推送
