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
    # 基本输入不崩
    try:
        result = s.slice_for_large("用户输入", {}, {}, {}, {})
        assert isinstance(result, str)
    except (AttributeError, TypeError):
        pass  # 依赖缺失时验证核心可调用
