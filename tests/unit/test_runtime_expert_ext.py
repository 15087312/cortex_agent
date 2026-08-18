"""runtime_expert 扩展测试：run_loop / CLI 模式 / 工具解析执行 / 注册表"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import modules.thinking.runtime_expert as re_mod
from modules.thinking.runtime_expert import (
    RuntimeExpert,
    register_runtime_expert,
    get_runtime_expert_class,
    _RUNTIME_EXPERT_REGISTRY,
)


class _ConcreteExpert(RuntimeExpert):
    async def process(self, request_text, messages):
        return "ok"


def _expert(**kw):
    e = _ConcreteExpert.__new__(_ConcreteExpert)
    e._blackboard = kw.get("blackboard", None)
    e.model_id = kw.get("model_id", "expert_x")
    e.session_id = "s1"
    e.model_instance = kw.get("model_instance", None)
    ident = MagicMock()
    ident.name = kw.get("name", "代码实现专家")
    ident.role = kw.get("role", "code_writer")
    ident.tier = "expert"
    ident.expertise = ["python", "重构"]
    ident.startup = kw.get("startup", "on_demand")
    e.identity = ident
    e.startup_mode = kw.get("startup", "on_demand")
    e.is_persistent = e.startup_mode == "persistent"
    e._seen_request_entry_ids = set()
    e._running = kw.get("running", False)
    e._round = kw.get("round", 0)
    e._started_at = kw.get("started_at", None)
    e.logger = MagicMock()
    return e


# ── __init__ 与身份加载 ────────────────────────────────────────────────

def test_init_loads_identity(monkeypatch):
    fake_ident = MagicMock()
    fake_ident.name = "专家"
    fake_ident.role = "expert_role"
    fake_ident.startup = "persistent"
    fake_ident.tier = "expert"
    monkeypatch.setattr("modules.thinking.identity.ModelIdentity.from_template", staticmethod(lambda k: fake_ident))
    class Sub(RuntimeExpert):
        template_key = "expert_test"
        async def process(self, request_text, messages, dialog_context=""):
            return "ok"
    e = Sub(model_id="m1", session_id="s1")
    assert e.identity is fake_ident
    assert e.is_persistent is True
    assert e.startup_mode == "persistent"


def test_init_requires_template_key(monkeypatch):
    class NoKey(RuntimeExpert):
        async def process(self, request_text, messages, dialog_context=""):
            return "ok"
    with pytest.raises(ValueError):
        NoKey()


# ── get_status / stop ──────────────────────────────────────────────────

def test_get_status():
    e = _expert(started_at=100.0, running=True)
    e._round = 3
    st = e.get_status()
    assert st["role"] == "code_writer"
    assert st["round"] == 3
    assert st["running"] is True
    assert st["has_blackboard"] is False
    e.stop()
    assert e._running is False


# ── _model_generate ────────────────────────────────────────────────────

async def test_model_generate_ok():
    e = _expert(model_instance=MagicMock())
    e.model_instance.generate = AsyncMock(return_value="结果")
    assert await e._model_generate("prompt") == "结果"
    kw = e.model_instance.generate.call_args.kwargs
    assert kw["system_prompt"] == re_mod._NEUTRAL_SYSTEM_PROMPT


async def test_model_generate_no_instance():
    e = _expert(model_instance=None)
    with pytest.raises(RuntimeError):
        await e._model_generate("p")


async def test_model_generate_raises():
    e = _expert(model_instance=MagicMock())
    e.model_instance.generate = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await e._model_generate("p")


# ── _extract_tool_calls ────────────────────────────────────────────────

def test_extract_tool_calls_valid():
    e = _expert()
    out = e._extract_tool_calls(
        "<tool>\nname: web_search\narguments: {\"q\": \"天气\"}\n</tool>"
        "<tool>\nname: read_file\narguments: {\"path\": \"/tmp/x\"}\n</tool>"
    )
    assert len(out) == 2
    assert out[0]["name"] == "web_search"
    assert out[0]["arguments"] == {"q": "天气"}


def test_extract_tool_calls_no_match():
    e = _expert()
    assert e._extract_tool_calls("没有工具调用") == []


def test_extract_tool_calls_bad_json():
    e = _expert()
    out = e._extract_tool_calls("<tool>\nname: x\narguments: {bad json}\n</tool>")
    assert out == []


# ── _execute_tool_call ─────────────────────────────────────────────────

async def test_execute_tool_call_empty_name():
    e = _expert()
    assert await e._execute_tool_call({}) == "Error: tool_name is empty"


async def test_execute_tool_call_blocked(monkeypatch):
    e = _expert()
    gate = MagicMock()
    gate.check = AsyncMock(return_value=(False, "权限不足"))
    monkeypatch.setattr("modules.security_system.tool_security_gate.get_tool_security_gate", lambda: gate)
    assert await e._execute_tool_call({"name": "rm", "arguments": {}}) == "[安全门控拦截] 权限不足"


async def test_execute_tool_call_success(monkeypatch):
    e = _expert()
    gate = MagicMock()
    gate.check = AsyncMock(return_value=(True, "ok"))
    svc = MagicMock()
    result = MagicMock()
    result.success = True
    result.result = "结果文本"
    svc.execute = MagicMock(return_value=result)
    monkeypatch.setattr("modules.security_system.tool_security_gate.get_tool_security_gate", lambda: gate)
    monkeypatch.setattr("infra.mcp.factory.get_mcp_tool_service", lambda: svc)
    monkeypatch.setattr("infra.mcp.types.ToolCallRequest", MagicMock)
    out = await e._execute_tool_call({"name": "web_search", "arguments": '{"q": "x"}'})
    assert out == "结果文本"


async def test_execute_tool_call_failure(monkeypatch):
    e = _expert()
    gate = MagicMock()
    gate.check = AsyncMock(return_value=(True, "ok"))
    svc = MagicMock()
    result = MagicMock()
    result.success = False
    result.error = "执行失败"
    svc.execute = MagicMock(return_value=result)
    monkeypatch.setattr("modules.security_system.tool_security_gate.get_tool_security_gate", lambda: gate)
    monkeypatch.setattr("infra.mcp.factory.get_mcp_tool_service", lambda: svc)
    monkeypatch.setattr("infra.mcp.types.ToolCallRequest", MagicMock)
    assert await e._execute_tool_call({"name": "x", "arguments": {}}) == "Error: 执行失败"


# ── run_cli_mode ───────────────────────────────────────────────────────

async def test_run_cli_mode_success(monkeypatch):
    e = _expert(model_instance=MagicMock())
    e.model_instance.generate = AsyncMock(return_value="最终答案")
    monkeypatch.setattr(e, "_extract_tool_calls", lambda resp: [])
    out = await e.run_cli_mode("任务", max_iterations=5)
    assert out["success"] is True
    assert out["result"] == "最终答案"


async def test_run_cli_mode_with_tool_then_finish(monkeypatch):
    e = _expert(model_instance=MagicMock())
    calls = [iter([MagicMock(), MagicMock()]).__next__]
    responses = [
        "<tool>\nname: web_search\narguments: {\"q\":\"天气\"}\n</tool>",
        "最终答复",
    ]
    e.model_instance.generate = AsyncMock(side_effect=responses)
    monkeypatch.setattr(e, "_extract_tool_calls", lambda resp: (
        [{"name": "web_search", "arguments": {"q": "天气"}}] if "<tool>" in resp else []
    ))
    monkeypatch.setattr(e, "_execute_tool_call", AsyncMock(return_value="搜索结果"))
    out = await e.run_cli_mode("任务", max_iterations=5)
    assert out["success"] is True
    assert out["tool_calls"] == 1


async def test_run_cli_mode_tool_error(monkeypatch):
    e = _expert(model_instance=MagicMock())
    e.model_instance.generate = AsyncMock(return_value="<tool>\nname: x\narguments: {}\n</tool>")
    monkeypatch.setattr(e, "_extract_tool_calls", lambda resp: [{"name": "x", "arguments": {}}])
    monkeypatch.setattr(e, "_execute_tool_call", AsyncMock(side_effect=RuntimeError("tool fail")))
    out = await e.run_cli_mode("任务", max_iterations=2)
    assert out["tool_history"]
    assert out["tool_history"][0]["error"]


async def test_run_cli_mode_timeout(monkeypatch):
    e = _expert(model_instance=MagicMock())
    e.model_instance.generate = AsyncMock(return_value="答案")
    monkeypatch.setattr(e, "_extract_tool_calls", lambda resp: [])
    monkeypatch.setattr(re_mod, "effective_elapsed_since", lambda *a: 9999.0)
    out = await e.run_cli_mode("任务", max_iterations=3, timeout=1)
    assert out["success"] is False
    assert "Timeout" in out["error"]


async def test_run_cli_mode_empty_response(monkeypatch):
    e = _expert(model_instance=MagicMock())
    e.model_instance.generate = AsyncMock(return_value="")
    monkeypatch.setattr(e, "_extract_tool_calls", lambda resp: [])
    monkeypatch.setattr(re_mod, "effective_elapsed_since", lambda *a: 0.0)
    out = await e.run_cli_mode("任务", max_iterations=3)
    assert out["success"] is False
    assert "Empty response" in out["error"]


# ── run_loop ───────────────────────────────────────────────────────────

async def test_run_loop_process_and_stop(monkeypatch):
    e = _expert()
    e._round = 0
    e._running = True
    e.write_thought = MagicMock()
    e.read_requests = MagicMock(return_value=[])
    dialog = MagicMock()
    dialog.format_for_model = MagicMock(return_value="ctx")
    e._get_dialog = MagicMock(return_value=dialog)
    e.process = AsyncMock(return_value="处理结果")
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus", lambda: bus)
    monkeypatch.setattr(re_mod, "pausable_wait_for", None)

    # 让 message_event 直接返回：不 sleep
    calls = {"n": 0}
    async def check_messages():
        return [{"content": "任务"}]
    await e.run_loop(check_messages, "任务描述", max_rounds=1, max_idle_rounds=1, think_interval=0.01)
    assert e._round == 1
    bus.subscribe.assert_awaited()
    bus.unsubscribe.assert_awaited()


async def test_run_loop_persistent(monkeypatch):
    e = _expert(startup="persistent")
    e._running = True
    e.write_thought = MagicMock()
    e.read_requests = MagicMock(return_value=[])
    e.process = AsyncMock(return_value="")
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus", lambda: bus)
    calls = {"n": 0}
    async def check_messages():
        calls["n"] += 1
        if calls["n"] >= 2:
            return [{"content": "TASK_COMPLETE"}]
        return []
    await e.run_loop(check_messages, "任务", max_rounds=3, max_idle_rounds=1, think_interval=0.01)
    assert e._round >= 1


# ── _build_cli_prompt ──────────────────────────────────────────────────

def test_build_cli_prompt_with_history(monkeypatch):
    e = _expert()
    monkeypatch.setattr("infra.tool_manager.tool_registry.ToolRegistry.list_tools", staticmethod(lambda: {"t1": {"description": "工具一"}}))
    ctrl = MagicMock()
    ctrl.get_visible_tools = MagicMock(return_value=["t1"])
    monkeypatch.setattr("modules.security_system.tool_permission_controller.get_tool_permission_controller", lambda: ctrl)
    cfg = MagicMock()
    cfg.effective_execution_mode = "edit"
    import sys
    cfg_mod = sys.modules["config.settings"]
    monkeypatch.setattr(cfg_mod, "settings", cfg)
    prompt = e._build_cli_prompt("任务", [{"tool": "t1", "output": "结果1"}], 2)
    assert "任务" in prompt
    assert "工具一" in prompt
    assert "结果1" in prompt
    assert "第 2 轮迭代" in prompt
    # 权限边界 / 任务处理流程 / 返回给上级 提示已注入
    assert "权限边界" in prompt
    assert "任务处理流程" in prompt
    assert "返回给上级" in prompt
    assert "不要扩展任务范围" in prompt


def test_build_cli_prompt_no_tools(monkeypatch):
    e = _expert()
    monkeypatch.setattr("infra.tool_manager.tool_registry.ToolRegistry.list_tools", staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("no"))))
    prompt = e._build_cli_prompt("任务", [], 1)
    assert "系统可用工具" in prompt


# ── 注册表 ─────────────────────────────────────────────────────────────

def test_register_and_get(monkeypatch):
    monkeypatch.setattr(re_mod, "_RUNTIME_EXPERT_REGISTRY", {})
    class MyExpert(RuntimeExpert):
        template_key = "x"
        async def process(self, request_text, messages, dialog_context=""):
            return "x"
    register_runtime_expert("my_role", MyExpert)
    assert get_runtime_expert_class("my_role") is MyExpert
    assert get_runtime_expert_class("none") is None
