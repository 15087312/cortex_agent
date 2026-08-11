"""ContextSlicer 测试（此前 25% 覆盖）：各类上下文格式化方法"""
from modules.thinking.cognition.context_slicer import ContextSlicer


def _slicer():
    return ContextSlicer.__new__(ContextSlicer)


def test_format_plan():
    s = _slicer()
    out = s._format_plan([{"description": "分析需求"}, {"description": "实现功能"}])
    assert "1. 分析需求" in out
    assert "2. 实现功能" in out
    assert s._format_plan([]) == "(无计划)"


def test_format_delegations():
    s = _slicer()
    out = s._format_delegations({
        "d1": {"status": "in_progress", "role": "code_writer", "task": "写代码"}
    })
    assert "[in_progress]" in out
    assert "code_writer" in out
    assert s._format_delegations({}) == "(无委托)"


def test_format_risks():
    s = _slicer()
    out = s._format_risks([{"severity": "high", "description": "数据风险"}])
    assert "high" in out
    assert "数据风险" in out
    assert s._format_risks([]) == "(无风险)"


def test_format_tools_empty():
    s = _slicer()
    out = s._format_tools({})
    assert isinstance(out, str)


def test_slice_for_large_basic(monkeypatch):
    s = _slicer()
    # 此前用旧签名调用（传 5 参）导致 TypeError 被 except pass 掩盖（假测试）
    from modules.thinking.cognition.blackboard import CognitiveBlackboard
    bb = CognitiveBlackboard(session_id="s1", turn_id="t1")
    bb.goal = "测试目标"
    result = s.slice_for_large(bb)
    assert isinstance(result, str)
    assert "测试目标" in result
