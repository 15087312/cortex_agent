"""model_runner 纯方法测试（此前 18% 覆盖）"""
import asyncio
from unittest.mock import MagicMock

import modules.thinking.core.model_runner as mr_mod
from modules.thinking.core.model_runner import ModelRunner


def test_get_tool_security_gate(monkeypatch):
    import modules.security_system.tool_security_gate as tsg
    fake = MagicMock()
    monkeypatch.setattr(tsg, "get_tool_security_gate", lambda: fake)
    assert mr_mod.get_tool_security_gate() is fake


def _runner(**kw):
    inst = MagicMock()
    ident = MagicMock()
    ident.model_id = kw.get("model_id", "large_primary")
    ident.tier = kw.get("tier", "large")
    inst.identity = ident
    r = ModelRunner.__new__(ModelRunner)
    r.instance = inst
    r.identity = ident
    r.model_id = ident.model_id
    r.tier = ident.tier
    r.blackboard = MagicMock()
    r.session_id = kw.get("session_id", "s1")
    r.manager = None
    r._running = False
    r._task = None
    r._task_description = ""
    r._task_id = ""
    r._return_to_model_id = ""
    r._return_to_session_id = ""
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
    return r


def test_context_tokens():
    r = _runner()
    assert r.context_tokens == 0
    r._thinker = MagicMock()
    r._thinker._context_tokens = 100
    assert r.context_tokens == 100


def test_context_window_size():
    r = _runner()
    assert r.context_window_size == 128000
    r._thinker = MagicMock()
    r._thinker._context_window_size = 64000
    assert r.context_window_size == 64000


def test_supervisor_property():
    r = _runner()
    assert r.supervisor == ""
    r._return_to_model_id = "supervisor_x"
    assert r.supervisor == "supervisor_x"


def test_inject_guidance():
    r = _runner()
    r.inject_guidance("引导")
    assert r._pending_guidance == ["引导"]


def test_update_loop_state():
    r = _runner()
    r._update_loop_state(think_round=3, think_max=10, think_wait=2.5)
    assert r._think_loop_state == {"round": 3, "max": 10, "wait": 2.5}
    assert r._react_loop is None
    r._react_loop = {"turn": 1}
    r._update_loop_state(think_round=0)
    assert r._think_loop_state == {"round": 3, "max": 10, "wait": 2.5}
    assert r._react_loop is None


def test_build_awakening_progress():
    r = _runner()
    prompt = r._build_awakening_prompt("【进度汇报】专家正常")
    assert "进度汇报" in prompt
    assert "继续等待" in prompt


def test_build_awakening_timeout():
    r = _runner()
    prompt = r._build_awakening_prompt("【等待超时】任务超时")
    assert "等待超时" in prompt


def test_build_awakening_source_tier():
    r = _runner()
    prompt = r._build_awakening_prompt("source_tier=expert 任务完成")
    assert "专家" in prompt


def test_build_awakening_has_results():
    r = _runner()
    thinker = MagicMock()
    thinker._pending_delegations = {"d1": {"status": "completed", "result_received": True}}
    r._thinker = thinker
    prompt = r._build_awakening_prompt("结果来了")
    assert "任务已有结果" in prompt


def test_build_awakening_no_results():
    r = _runner()
    thinker = MagicMock()
    thinker._pending_delegations = {"d1": {"status": "pending", "result_received": False}}
    r._thinker = thinker
    prompt = r._build_awakening_prompt("还在执行")
    assert "任务状态" in prompt


def test_collect_expert_progress_empty(monkeypatch):
    r = _runner()
    import modules.thinking.core.model_runner as mod
    monkeypatch.setattr(mod, "_runner_managers", {})
    assert asyncio.run(r._collect_expert_progress()) == ""
