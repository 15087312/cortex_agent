"""runtime_expert 补测 — read_requests 过滤 / 异常 / run_loop 全分支 / CLI 超时与异常

不真实调用模型/工具：全程 mock。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock


import modules.thinking.runtime_expert as re_mod
from modules.thinking.runtime_expert import RuntimeExpert


class _ConcreteExpert(RuntimeExpert):
    async def process(self, request_text, messages, dialog_context=""):
        return "ok"


def _expert(**kw):
    e = _ConcreteExpert.__new__(_ConcreteExpert)
    e._blackboard = kw.get("blackboard", None)
    e.model_id = kw.get("model_id", "expert_x")
    e.session_id = kw.get("session_id", "s1")
    e.model_instance = kw.get("model_instance", None)
    ident = MagicMock()
    ident.name = kw.get("name", "代码实现专家")
    ident.role = kw.get("role", "code_writer")
    ident.tier = "expert"
    ident.expertise = ["python"]
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


def _dialog(entries=None):
    d = MagicMock()
    d.read_dialog = MagicMock(return_value=entries or [])
    d.write_thought = MagicMock(return_value=type("E", (), {"entry_id": "e1"})())
    d.write_response = MagicMock(return_value=type("E", (), {"entry_id": "e2"})())
    d.format_for_model = MagicMock(return_value="formatted")
    return d


# ── read_requests 过滤 / 异常 ───────────────────────────────────────────

def test_read_requests_filters_all_branches():
    e = _expert(model_id="m1")
    d = _dialog([
        {"entry_id": "seen", "metadata": {}, "content": "代码实现专家 问题", "model_id": "other"},
        {"entry_id": "self", "metadata": {}, "content": "代码实现专家 问题", "model_id": "m1"},
        {"entry_id": "system", "metadata": {}, "content": "代码实现专家 问题", "model_id": "system"},
        {"entry_id": "hidden", "metadata": {"visibility": "hidden"}, "content": "代码实现专家 问题", "model_id": "o"},
        {"entry_id": "internal", "metadata": {"internal_protocol": True}, "content": "代码实现专家", "model_id": "o"},
        {"entry_id": "irrelevant", "metadata": {}, "content": "完全无关", "model_id": "o"},
        {"entry_id": "", "metadata": {}, "content": "代码实现专家 无id", "model_id": "o"},
    ])
    e._blackboard = d
    e._seen_request_entry_ids.add("seen")
    out = e.read_requests(limit=2)
    # 只剩无 entry_id 的最后一条符合
    assert all(x["entry_id"] != "seen" for x in out)
    assert "代码实现专家" in out[-1]["content"]


def test_read_requests_exception():
    e = _expert()
    d = _dialog()
    d.read_dialog.side_effect = RuntimeError("boom")
    e._blackboard = d
    assert e.read_requests() == []


def test_read_requests_limit():
    e = _expert()
    entries = [{"entry_id": f"e{i}", "metadata": {}, "content": "代码实现专家 请求", "model_id": "o"} for i in range(8)]
    e._blackboard = _dialog(entries)
    out = e.read_requests(limit=2)
    assert len(out) == 2


# ── write_thought / write_response 异常 ─────────────────────────────────

def test_write_thought_exception():
    e = _expert()
    d = _dialog()
    d.write_thought.side_effect = RuntimeError("boom")
    e._blackboard = d
    assert e.write_thought("x") is None


def test_write_thought_no_dialog():
    e = _expert()
    assert e.write_thought("x") is None


def test_write_response_exception():
    e = _expert()
    d = _dialog()
    d.write_response.side_effect = RuntimeError("boom")
    e._blackboard = d
    assert e.write_response("x") is None


def test_write_response_no_dialog():
    e = _expert()
    assert e.write_response("x") is None


# ── _is_relevant ────────────────────────────────────────────────────────

def test_is_relevant_branches():
    e = _expert(model_id="m1", name="名字", role="角色")
    assert e._is_relevant("提到名字")
    assert e._is_relevant("提到角色")
    assert e._is_relevant("提到m1")
    assert not e._is_relevant("无关内容")


# ── run_loop 全分支 ─────────────────────────────────────────────────────

async def test_run_loop_subscribe_failure(monkeypatch):
    e = _expert()
    e._running = True
    e.write_thought = MagicMock()
    e.read_requests = MagicMock(return_value=[])
    e.process = AsyncMock(return_value="")
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus",
                        lambda: (_ for _ in ()).throw(RuntimeError("no bus")))

    async def check():
        return []
    await e.run_loop(check, "任务", max_rounds=1, max_idle_rounds=1, think_interval=0.01)


async def test_run_loop_sync_check_fn(monkeypatch):
    e = _expert()
    e._running = True
    e.write_thought = MagicMock()
    e.read_requests = MagicMock(return_value=[])
    e.process = AsyncMock(return_value="")
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus", lambda: bus)
    calls = {"n": 0}

    def check():  # 同步函数
        calls["n"] += 1
        return [{"content": "TASK_COMPLETE"}]
    await e.run_loop(check, "任务", max_rounds=3, max_idle_rounds=1, think_interval=0.01)
    assert calls["n"] >= 1


async def test_run_loop_no_blackboard(monkeypatch):
    """blackboard 为 None → format_for_model 跳过"""
    e = _expert()
    e._running = True
    e.write_thought = MagicMock()
    e.read_requests = MagicMock(return_value=[])
    e.process = AsyncMock(return_value="")
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus", lambda: bus)
    calls = {"n": 0}

    async def check():
        calls["n"] += 1
        if calls["n"] >= 2:
            return [{"content": "TASK_COMPLETE"}]
        return []
    await e.run_loop(check, "任务", max_rounds=3, max_idle_rounds=1, think_interval=0.01)


async def test_run_loop_format_exception(monkeypatch):
    e = _expert()
    e._running = True
    e.write_thought = MagicMock()
    e.read_requests = MagicMock(return_value=[])
    d = _dialog()
    d.format_for_model.side_effect = RuntimeError("format fail")
    e._get_dialog = MagicMock(return_value=d)
    e.process = AsyncMock(return_value="")
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus", lambda: bus)
    calls = {"n": 0}

    async def check():
        calls["n"] += 1
        if calls["n"] >= 2:
            return [{"content": "TASK_COMPLETE"}]
        return []
    await e.run_loop(check, "任务", max_rounds=3, max_idle_rounds=1, think_interval=0.01)


async def test_run_loop_dialog_requests_processed(monkeypatch):
    """dialog_requests 非空 → 触发 process，非持久专家完成自动停止（313-316）"""
    e = _expert()
    e._running = True
    e.write_thought = MagicMock()
    e.read_requests = MagicMock(return_value=[{"content": "对话框请求"}])
    d = _dialog()
    e._get_dialog = MagicMock(return_value=d)
    e.process = AsyncMock(return_value="结果")
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus", lambda: bus)

    async def check():
        return []
    await e.run_loop(check, "任务", max_rounds=3, max_idle_rounds=1, think_interval=0.01)
    assert e.process.called


async def test_run_loop_idle_exit(monkeypatch):
    """连续空闲轮次 → 自动停止（320, 323-330）"""
    e = _expert()
    e._running = True
    e.write_thought = MagicMock()
    e.read_requests = MagicMock(return_value=[])
    e.process = AsyncMock(return_value="")
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus", lambda: bus)

    async def check():
        return []
    await e.run_loop(check, "任务", max_rounds=10, max_idle_rounds=1, think_interval=0.01)
    assert e._running is False or e._round <= 3


async def test_run_loop_memory_manager_keeps_alive(monkeypatch):
    """memory_manager 角色 → 空闲不清除（325-336）"""
    e = _expert(role="memory_manager")
    e._running = True
    e.write_thought = MagicMock()
    e.read_requests = MagicMock(return_value=[])
    e.process = AsyncMock(return_value="")
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus", lambda: bus)
    calls = {"n": 0}

    async def check():
        calls["n"] += 1
        if calls["n"] >= 3:
            return [{"content": "TASK_COMPLETE"}]
        return []
    await e.run_loop(check, "任务", max_rounds=5, max_idle_rounds=1, think_interval=0.01)


async def test_run_loop_termination_signal(monkeypatch):
    """收到终止信号 → _running=False（339-345）"""
    e = _expert()
    e._running = True
    e.write_thought = MagicMock()
    e.read_requests = MagicMock(return_value=[])
    e.process = AsyncMock(return_value="")
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus", lambda: bus)
    calls = {"n": 0}

    async def check():
        calls["n"] += 1
        return [{"content": "停止委托"}]
    await e.run_loop(check, "任务", max_rounds=3, max_idle_rounds=1, think_interval=0.01)
    assert e._round >= 1


async def test_run_loop_exception_and_cancel(monkeypatch):
    """process 抛异常 → 写入异常到黑板；再触发 CancelledError 退出"""
    e = _expert()
    e._running = True
    e.write_thought = MagicMock()
    e.read_requests = MagicMock(return_value=[])
    e.process = AsyncMock(side_effect=[RuntimeError("boom"), asyncio.CancelledError()])
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus", lambda: bus)

    async def check():
        return [{"content": "任务"}]
    await e.run_loop(check, "任务", max_rounds=3, max_idle_rounds=1, think_interval=0.01)
    e.write_thought.assert_called()


async def test_run_loop_unsubscribe_failure(monkeypatch):
    e = _expert()
    e._running = True
    e.write_thought = MagicMock()
    e.read_requests = MagicMock(return_value=[])
    e.process = AsyncMock(return_value="")
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock(side_effect=RuntimeError("unsub fail"))
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus", lambda: bus)
    calls = {"n": 0}

    async def check():
        calls["n"] += 1
        if calls["n"] >= 2:
            return [{"content": "TASK_COMPLETE"}]
        return []
    await e.run_loop(check, "任务", max_rounds=3, max_idle_rounds=1, think_interval=0.01)


async def test_run_loop_non_persistent_auto_exit(monkeypatch):
    """非持久专家，process 返回结果且无消息/无对话框请求 → 自动停止（313-316）"""
    e = _expert()
    e._running = True
    e.write_thought = MagicMock()
    e.read_requests = MagicMock(return_value=[])
    d = _dialog()
    e._get_dialog = MagicMock(return_value=d)
    e.process = AsyncMock(return_value="处理结果")
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus", lambda: bus)

    async def check():
        return []
    await e.run_loop(check, "任务", max_rounds=3, max_idle_rounds=5, think_interval=0.01)
    assert e._round == 1


async def test_run_loop_exception_thought_fail(monkeypatch):
    """process 抛异常且 write_thought 也失败 → 异常被吞掉（369-370）"""
    e = _expert()
    e._running = True
    e.read_requests = MagicMock(return_value=[])
    e.process = AsyncMock(side_effect=RuntimeError("boom"))
    # 第 1 次调用（就绪通知）成功；之后调用抛异常
    e.write_thought = MagicMock(side_effect=[None, RuntimeError("thought fail")])
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus", lambda: bus)
    calls = {"n": 0}

    async def check():
        calls["n"] += 1
        if calls["n"] >= 2:
            return [{"content": "TASK_COMPLETE"}]
        return [{"content": "任务"}]
    await e.run_loop(check, "任务", max_rounds=3, max_idle_rounds=1, think_interval=0.01)  # 不抛异常


# ── run_cli_mode 更多分支 ───────────────────────────────────────────────

async def test_run_cli_mode_round_timeout(monkeypatch):
    """轮级超时（484-488）"""
    e = _expert(model_instance=MagicMock())
    e.model_instance.generate = AsyncMock(return_value="答案")
    monkeypatch.setattr(e, "_extract_tool_calls", lambda resp: [])
    # 第 1 次调用（总体检查）返回小值；第 2 次调用（本轮检查）返回大值
    vals = iter([0.0, 999.0])
    monkeypatch.setattr(re_mod, "effective_elapsed_since", lambda *a: next(vals))
    out = await e.run_cli_mode("任务", max_iterations=3, timeout=100, round_timeout=1)
    assert out["success"] is False
    assert "Round timeout" in out["error"]


async def test_run_cli_mode_generate_timeout(monkeypatch):
    """模型生成超时（504-506）"""
    e = _expert(model_instance=MagicMock())
    e.model_instance.generate = AsyncMock(return_value="x")
    monkeypatch.setattr(e, "_extract_tool_calls", lambda resp: [])
    monkeypatch.setattr(re_mod, "effective_elapsed_since", lambda *a: 0.0)
    monkeypatch.setattr(re_mod, "pausable_wait_for",
                        lambda *a, **k: (_ for _ in ()).throw(asyncio.TimeoutError()))
    out = await e.run_cli_mode("任务", max_iterations=3)
    assert out["success"] is False
    assert "timeout" in out["error"].lower()


async def test_run_cli_mode_top_exception(monkeypatch):
    """run_cli_mode 顶层异常（579-583）"""
    e = _expert(model_instance=MagicMock())
    e.model_instance.generate = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(e, "_extract_tool_calls", lambda resp: [])
    monkeypatch.setattr(re_mod, "effective_elapsed_since", lambda *a: 0.0)
    monkeypatch.setattr(re_mod, "pausable_wait_for", None)
    # 让 _model_generate 抛出的异常穿透 while → 顶层 except
    out = await e.run_cli_mode("任务", max_iterations=1)
    assert out["success"] is False


async def test_run_cli_mode_max_iterations(monkeypatch):
    """达到最大迭代（570-577）"""
    e = _expert(model_instance=MagicMock())
    e.model_instance.generate = AsyncMock(return_value="继续")
    monkeypatch.setattr(e, "_extract_tool_calls",
                        lambda resp: [{"name": "x", "arguments": {}}])
    monkeypatch.setattr(e, "_execute_tool_call", AsyncMock(return_value="工具结果"))
    monkeypatch.setattr(re_mod, "effective_elapsed_since", lambda *a: 0.0)
    out = await e.run_cli_mode("任务", max_iterations=2)
    assert out.get("reached_max_iterations") is True


async def test_run_cli_mode_tool_string_args_json_fail(monkeypatch):
    """arguments 是字符串且 JSON 解析失败 → 空 dict（712-713）"""
    e = _expert()
    gate = MagicMock()
    gate.check = AsyncMock(return_value=(True, "ok"))
    svc = MagicMock()
    result = MagicMock()
    result.success = False
    result.error = "失败"
    svc.execute = MagicMock(return_value=result)
    monkeypatch.setattr("modules.security_system.tool_security_gate.get_tool_security_gate", lambda: gate)
    monkeypatch.setattr("infra.mcp.factory.get_mcp_tool_service", lambda: svc)
    monkeypatch.setattr("infra.mcp.types.ToolCallRequest", MagicMock)
    out = await e._execute_tool_call({"name": "x", "arguments": "{bad json"})
    assert "Error" in out


async def test_execute_tool_call_top_exception(monkeypatch):
    """工具执行整体异常（748-750）"""
    e = _expert()
    monkeypatch.setattr("modules.security_system.tool_security_gate.get_tool_security_gate",
                        lambda: (_ for _ in ()).throw(RuntimeError("no gate")))
    out = await e._execute_tool_call({"name": "x", "arguments": {}})
    assert "Error" in out


async def test_execute_tool_call_none_result(monkeypatch):
    """result.result 为 None → (无返回值)"""
    e = _expert()
    gate = MagicMock()
    gate.check = AsyncMock(return_value=(True, "ok"))
    svc = MagicMock()
    result = MagicMock()
    result.success = True
    result.result = None
    svc.execute = MagicMock(return_value=result)
    monkeypatch.setattr("modules.security_system.tool_security_gate.get_tool_security_gate", lambda: gate)
    monkeypatch.setattr("infra.mcp.factory.get_mcp_tool_service", lambda: svc)
    monkeypatch.setattr("infra.mcp.types.ToolCallRequest", MagicMock)
    assert await e._execute_tool_call({"name": "x", "arguments": {}}) == "(无返回值)"
