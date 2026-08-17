"""黑板委托链/上下文状态内存安全测试

类型: 缓存型泄漏防护——委托链有上限 + 可清理；观察/对话有界；
断点 resume_context 为单值覆盖不累积；长时间运行内存稳定。

（上下文管理重构后补充：委托链节点 MAX_DELEGATIONS、清理方法、多轮运行稳定）
"""
import pytest

from modules.thinking.cognition.blackboard import CognitiveBlackboard

pytestmark = pytest.mark.leak


def _bb():
    return CognitiveBlackboard(session_id="leak_s", turn_id="turn_1")


def test_delegations_capped_by_max():
    """委托链节点数不超上限（MAX_DELEGATIONS），超限清理最旧已完成"""
    bb = _bb()
    for i in range(bb.MAX_DELEGATIONS + 50):
        did = bb.write_delegation(f"ex{i}", f"任务{i}", probe_id=f"p_{i}")
        # 标记已完成，让超限清理可回收
        bb.update_delegation_progress(did, status="replied")
    assert len(bb.delegations) <= bb.MAX_DELEGATIONS


def test_delegations_pending_kept_on_trim():
    """超限清理只清已完成/过期，pending/running 保留"""
    bb = _bb()
    pending_ids = []
    for i in range(30):
        did = bb.write_delegation(f"ex{i}", f"任务{i}", probe_id=f"p{i}")
        if i % 2 == 0:
            bb.update_delegation_progress(did, status="replied")
        else:
            pending_ids.append(did)
    # 手动清一批（未超限，调用 clear_delegations）
    removed = bb.clear_delegations()
    assert removed == 15
    for did in pending_ids:
        assert did in bb.delegations


def test_clear_delegations_keep_recent():
    """clear_delegations 可保留最近 N 条已完成"""
    bb = _bb()
    ids = []
    for i in range(20):
        did = bb.write_delegation(f"ex{i}", f"任务{i}", probe_id=f"p{i}")
        bb.update_delegation_progress(did, status="replied")
        ids.append(did)
    removed = bb.clear_delegations(keep=5)
    assert removed == 15
    assert len(bb.delegations) == 5
    assert ids[-5:] == sorted(bb.delegations.keys())


def test_observations_bounded():
    """观察列表受 MAX_OBSERVATIONS 限制，长时间运行不增长"""
    bb = _bb()
    for i in range(500):
        bb.add_observation("expert", f"观察{i}")
    assert len(bb.observations) <= bb.MAX_OBSERVATIONS


def test_dialog_entries_bounded():
    """对话记录 deque 有上限（500），不随轮次无限增长"""
    bb = _bb()
    for i in range(2000):
        bb.write_thought(f"m{i}", "large", f"第{i}轮", round_num=i)
    assert len(bb._dialog_entries) <= 500


def test_resume_context_single_value_overwritten():
    """断点 resume_context 是单值覆盖，多轮保存不累积"""
    bb = _bb()
    for i in range(50):
        bb.resume_context = [{"role": "user", "content": f"消息{i}"}]
    assert len(bb.resume_context) == 1
    assert bb.resume_context[0]["content"] == "消息49"


def test_many_sessions_clearable():
    """大量黑板可整体清空委托/对话，不残留（防泄漏）"""
    for sid in range(30):
        bb = CognitiveBlackboard(session_id=f"sess_{sid}", turn_id="t")
        for i in range(20):
            bb.write_delegation(f"ex{i}", "任务", probe_id=f"p{i}")
        bb.clear_delegations()
        bb.clear_turn_state()
        assert len(bb.delegations) == 0
        assert len(bb._dialog_entries) == 0
