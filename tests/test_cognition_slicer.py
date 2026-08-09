"""thinking/cognition/context_slicer 测试（此前 46% 覆盖）：各 tier 切片"""
from unittest.mock import MagicMock

from modules.thinking.cognition.context_slicer import ContextSlicer


def _slicer():
    return ContextSlicer.__new__(ContextSlicer)


def _bb(**kw):
    bb = MagicMock()
    bb.observations = []
    bb.goal = kw.get("goal", "")
    bb.current_plan = kw.get("current_plan", [])
    bb.risks = kw.get("risks", [])
    bb.delegations = kw.get("delegations", {})
    bb.expert_findings = kw.get("expert_findings", {})
    bb.runtime_state = kw.get("runtime_state", {})
    bb.get_observations_since = lambda cursor: []
    return bb


def test_format_plan():
    s = _slicer()
    out = s._format_plan([{"description": "第一步"}, {"description": "第二步"}])
    assert "1. 第一步" in out
    assert s._format_plan([]) == "(无计划)"


def test_format_delegations():
    s = _slicer()
    out = s._format_delegations({"d1": {"status": "pending", "role": "expert", "task": "重构"}})
    assert "- [pending] expert: 重构" in out
    assert s._format_delegations({}) == "(无委托)"


def test_format_risks():
    s = _slicer()
    out = s._format_risks([{"severity": "high", "description": "风险A"}])
    assert "【high】风险A" in out
    assert s._format_risks([]) == "(无风险)"


def test_slice_for_large_full():
    s = _slicer()
    bb = _bb(
        goal="完成项目",
        current_plan=[{"description": "规划"}],
        risks=[{"severity": "high", "description": "风险"}],
        delegations={"d1": {"status": "pending", "role": "expert", "task": "任务"}},
        expert_findings={"k": {"role": "expert", "content": "发现"}},
    )
    out = s.slice_for_large(bb)
    assert "总体目标" in out
    assert "当前计划" in out
    assert "风险摘要" in out
    assert "委托状态" in out
    assert "专家发现" in out


def test_slice_for_large_empty():
    s = _slicer()
    out = s.slice_for_large(_bb())
    assert out == ""


def test_slice_for_supervisor_and_expert():
    s = _slicer()
    bb = _bb(goal="目标")
    assert isinstance(s.slice_for_supervisor(bb), str)
    assert isinstance(s.slice_for_expert(bb), str)


def test_slice_for_supervisor_task():
    s = _slicer()
    dlg = MagicMock()
    dlg.task = "完成任务"
    dlg.metadata = {"context": "背景"}
    bb = _bb(goal="目标", delegations={"d1": dlg}, runtime_state={"available_tools": {"tool_a": {}}})
    out = s.slice_for_supervisor(bb, delegation_id="d1")
    assert "完成任务" in out
    assert "可用工具" in out
    assert "tool_a" in out


def test_format_runtime_state_and_observations():
    s = _slicer()
    out = s._format_runtime_state({"mode": "auto", "available_tools": {"x": {}}})
    assert "mode" in out
    assert "available_tools" not in out
    obs = type("O", (), {"tier": "expert", "content": "内容"})()
    assert "内容" in s._format_observations([obs])
    assert s._format_observations([]) == "(无历史)"
