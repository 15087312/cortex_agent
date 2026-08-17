"""TurnContext 上下文池测试：生命周期 / 去重 / 角色视图"""
from unittest.mock import MagicMock

import pytest

from modules.thinking.context.pool import TurnContext, ContextFragment, TurnState


def _frag(source="memory", content="内容", target_roles=("large",), title="记忆", priority=0):
    return ContextFragment(
        source=source, content=content, target_roles=target_roles,
        section_title=title, priority=priority,
    )


def test_turn_context_init():
    tc = TurnContext(session_id="s1", user_input="你好")
    assert tc.turn_id
    assert tc.session_id == "s1"
    assert tc.user_input == "你好"
    assert tc.state == TurnState.IDLE


def test_state_transitions():
    tc = TurnContext()
    assert tc.is_active is False
    assert tc.is_complete is False
    tc.transition_to(TurnState.PLANNING)
    assert tc.is_active is True
    tc.transition_to(TurnState.EXECUTING)
    assert tc.is_active is True
    tc.transition_to(TurnState.COMPLETE)
    assert tc.is_complete is True
    assert tc.end_ts is not None
    assert tc.elapsed_seconds >= 0
    tc2 = TurnContext()
    tc2.transition_to(TurnState.ERROR)
    assert tc2.is_complete is True


def test_to_dict():
    tc = TurnContext(session_id="s", user_input="u" * 300)
    tc.transition_to(TurnState.COMPLETE)
    d = tc.to_dict()
    assert d["session_id"] == "s"
    assert d["state"] == "complete"
    assert len(d["user_input"]) == 200  # 截断


def test_add_skips_empty_and_dedup():
    tc = TurnContext()
    tc.add(_frag(content=""))
    assert tc.fragments == {}
    tc.add(_frag(content="相同", source="a"))
    tc.add(_frag(content="相同", source="b"))  # 去重
    assert len(tc.fragments) == 1


def test_view_role_filter_and_alias():
    tc = TurnContext()
    tc.add(_frag(source="a", target_roles=("large",), content="给大模型的"))
    tc.add(_frag(source="b", target_roles=("supervisor",), content="给主管的"))
    out_large = tc.view("large")
    assert "给大模型的" in out_large
    assert "给主管的" not in out_large
    # orchestrator 是 large 别名
    out_orch = tc.view("orchestrator")
    assert "给大模型的" in out_orch
    out_sup = tc.view("supervisor")
    assert "给主管的" in out_sup
    assert "给大模型的" not in out_sup


def test_view_priority_order():
    tc = TurnContext()
    tc.add(_frag(source="a", content="低优先", priority=10, title="A"))
    tc.add(_frag(source="b", content="高优先", priority=0, title="B"))
    out = tc.view("large")
    assert out.index("高优先") < out.index("低优先")


def test_compact_empty():
    tc = TurnContext()
    assert tc._compact("", 100) == ""


def test_compact_over_budget_no_hard_truncate(monkeypatch):
    """超限不硬裁剪（禁止 30/70 截断）：仅告警并返回原样，由总结机制控制"""
    tc = TurnContext()
    engine = MagicMock()
    engine.estimate_tokens = MagicMock(return_value=9999)
    engine.compress = MagicMock(return_value="压缩后")
    monkeypatch.setattr("modules.thinking.context.compression.get_compression_engine", lambda: engine)
    text = "很长" * 100
    assert tc._compact(text, 10) == text  # 原样返回，不调用 compress
    engine.compress.assert_not_called()


def test_compact_within_budget(monkeypatch):
    """未超限直接返回原样"""
    tc = TurnContext()
    engine = MagicMock()
    engine.estimate_tokens = MagicMock(return_value=5)
    monkeypatch.setattr("modules.thinking.context.compression.get_compression_engine", lambda: engine)
    assert tc._compact("内容", 10) == "内容"


def test_compact_max_tokens_zero_returns_original():
    """max_tokens<=0（未配置模型上下文长度）不限制"""
    tc = TurnContext()
    text = "内容" * 50
    assert tc._compact(text, 0) == text
    assert tc._compact(text, -1) == text


def test_compact_real_engine_over_budget():
    """真实 compression 引擎 + 长文本超限 → 返回原样（不硬裁剪）"""
    tc = TurnContext()
    text = "这是一段很长的中文内容。" * 2000
    out = tc._compact(text, 10)
    assert "这是" in out
    assert len(out) > 1000  # 未被截断成头尾小片段


def test_compact_error(monkeypatch):
    tc = TurnContext()
    def boom():
        raise RuntimeError("engine down")
    monkeypatch.setattr("modules.thinking.context.compression.get_compression_engine", boom)
    assert tc._compact("内容", 10) == "内容"
