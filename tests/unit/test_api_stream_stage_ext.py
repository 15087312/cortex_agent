"""api_stream think() 阶段事件流处理测试"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import modules.thinking.api_stream as stream


def _system():
    s = stream.StreamThinkingSystem.__new__(stream.StreamThinkingSystem)
    s.sessions = {}
    s._running = False
    s._lock = asyncio.Lock()
    s._orchestrator = MagicMock()
    s._session_repo = None
    return s


async def _setup(s, monkeypatch):
    s.sessions["s1"] = {"processing": False, "messages": [], "started_at": 0, "model_id": "large_primary"}
    s._is_processing = AsyncMock(return_value=False)
    s._append_message = AsyncMock(return_value="uid")
    s._proactive_context_trim = AsyncMock()
    s._emit = AsyncMock()
    s._post_task_extraction = AsyncMock()
    s._persist_thought = AsyncMock()
    s.get_context = MagicMock(return_value=[{"role": "user", "content": "你好"}])
    monkeypatch.setattr(stream, "connection_manager", MagicMock())
    stream.connection_manager.send_json = AsyncMock()
    monkeypatch.setattr("modules.thinking.communication.get_message_bus", lambda: MagicMock())
    monkeypatch.setattr("modules.security_system.tool_security_gate.set_security_event_callback", lambda cb: None)
    monkeypatch.setattr("modules.thinking.frontend_channel.confirm_frontend_connection", lambda sid: True)
    monkeypatch.setattr("modules.thinking.session_graph.get_session_graph_store", lambda: MagicMock())
    repo = MagicMock()
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    return repo


async def test_think_processes_stage_events(monkeypatch):
    s = _system()
    await _setup(s, monkeypatch)

    events = [
        {"type": "model_comm", "source": "m", "target": "t",
         "payload": {"msg_type": "broadcast", "sender": "s", "recipient": "b",
                     "content": {"content": "正在分析中", "entry_type": "thought", "round": 1,
                                 "model_id": "supervisor_code_001"},
                     "metadata": {"dialog_id": "d1", "tier": "supervisor"}}},
        {"type": "tool_call", "target": "web_search", "action": "run", "success": True},
    ]

    async def fake_process(user_input, context, stm, event_callback, session_id, model_id="large_primary"):
        for ev in events:
            event_callback(ev)
            await asyncio.sleep(0.02)
        return {"module_results": [], "decisions": {"probe_signals": []},
                "response": "最终回复", "trace_id": "t", "elapsed_ms": 1,
                "active_modules": [], "focus": "x"}

    s._orchestrator.process = fake_process
    out = await s.think("s1", "你好")
    assert out == "最终回复"
    # 阶段事件被格式化并持久化 + emit
    s._persist_thought.assert_awaited()
    thinking_emits = [c.args[1] for c in s._emit.call_args_list if c.args[1].get("type") == "thinking"]
    assert thinking_emits


async def test_think_final_meta_and_message_id(monkeypatch):
    s = _system()
    await _setup(s, monkeypatch)

    async def fake_process(user_input, context, stm, event_callback, session_id, model_id="large_primary"):
        return {"module_results": [], "decisions": {"probe_signals": []},
                "response": "没有标记的结果", "trace_id": "tr", "elapsed_ms": 5,
                "active_modules": ["thinking"], "focus": "x"}

    s._orchestrator.process = fake_process
    out = await s.think("s1", "你好")
    assert out == "没有标记的结果"
    message_emits = [c.args[1] for c in s._emit.call_args_list if c.args[1].get("event") == "assistant_message"]
    assert message_emits
    assert message_emits[-1]["data"]["output_mode"] == "output"
