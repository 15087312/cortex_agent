"""thinking/context/manager 测试（此前 21% 覆盖）：外部引导与委托状态格式化"""
from modules.thinking.context.manager import ContextManager


def test_build_external_guidance_empty():
    assert ContextManager.build_external_guidance([], []) == ""


def test_build_external_guidance_persistent_only():
    out = ContextManager.build_external_guidance(["简报A"], [])
    assert "[系统简报 #1]" in out
    assert "简报A" in out


def test_build_external_guidance_transient_only():
    out = ContextManager.build_external_guidance([], ["本轮提示"])
    assert "[本轮提示 #1]" in out
    assert "本轮提示" in out


def test_build_external_guidance_limit():
    out = ContextManager.build_external_guidance(list(range(10)), list(range(5)))
    assert "9" in out          # 保留持久简报最后一条内容
    assert "4" in out          # 保留本轮提示最后一条内容
    assert "#1]\n0" not in out  # 最早的持久简报被裁剪
    assert "#1]\n1" not in out  # 最早的本轮提示被裁剪


def test_build_delegation_status_empty():
    assert ContextManager.build_delegation_status({}) == ""


def test_build_delegation_status_pending():
    d = {"a": {"status": "pending", "round": 1, "role": "expert", "task": "重构模块"}, "b": {"status": "done", "round": 2, "role": "expert", "task": "测试"}}
    out = ContextManager.build_delegation_status(d)
    assert "当前委托状态" in out
    assert "等待专家回复" in out


def test_build_delegation_status_all_done():
    d = {"a": {"status": "done", "round": 1, "role": "expert", "task": "完成"}}
    out = ContextManager.build_delegation_status(d)
    assert "当前委托状态" in out
    assert "等待专家回复" not in out


# ── build_delegation_status：完整委托链（黑板）+ scope 过滤 ──

def _mk_bb():
    from modules.thinking.cognition.blackboard import CognitiveBlackboard
    return CognitiveBlackboard(session_id="s", turn_id="t")


def test_delegation_status_from_blackboard_chain():
    bb = _mk_bb()
    # 根委托（主管发起）→ 子委托（专家）
    bb.write_delegation(
        delegation_id="root", role="code_supervisor", task="主管任务",
        caller_model_id="large_primary", caller_tier="large",
        target_tier="supervisor",
    )
    bb.write_delegation(
        delegation_id="child", role="code_writer", task="专家任务",
        caller_model_id="supervisor_code_001", caller_tier="supervisor",
        target_tier="expert", parent_delegation_id="root",
    )
    out = ContextManager.build_delegation_status({}, blackboard=bb)
    assert "委托链" in out
    assert "root" in out and "child" in out
    assert "主管任务" in out


def test_delegation_status_scope_supervisor_filters():
    bb = _mk_bb()
    # 主管 A 发起的委托
    bb.write_delegation(
        delegation_id="dA", role="code_writer", task="A的任务",
        caller_model_id="sup_A", caller_tier="supervisor", target_tier="expert",
    )
    # 主管 B 发起的委托（不应出现在 A 的视图中）
    bb.write_delegation(
        delegation_id="dB", role="code_writer", task="B的任务",
        caller_model_id="sup_B", caller_tier="supervisor", target_tier="expert",
    )
    out = ContextManager.build_delegation_status({}, blackboard=bb,
                                                  scope_model_id="sup_A", scope_tier="supervisor")
    assert "dA" in out
    assert "dB" not in out


def test_delegation_status_scope_absent_for_large():
    bb = _mk_bb()
    bb.write_delegation(
        delegation_id="dX", role="code_writer", task="X任务",
        caller_model_id="large_primary", caller_tier="large", target_tier="expert",
    )
    out = ContextManager.build_delegation_status({}, blackboard=bb, scope_model_id="", scope_tier="large")
    assert "dX" in out
