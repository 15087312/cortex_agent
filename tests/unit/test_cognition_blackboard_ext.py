"""CognitiveBlackboard 扩展测试：观察清理 / 委托 / 对话框 / 快照"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from modules.thinking.cognition.blackboard import (
    CognitiveBlackboard,
    DialogEntry,
    Observation,
    ExpertFinding,
    Delegation,
)


def _bb():
    return CognitiveBlackboard(session_id="s1", turn_id="t1")


# ── DialogEntry ────────────────────────────────────────────────────────

def test_dialog_entry_auto_id_and_to_dict():
    e = DialogEntry(entry_type="thought", model_id="m", tier="large", content="内容")
    assert e.entry_id.startswith("dlg_")
    d = e.to_dict()
    assert d["type"] == "thought"
    e2 = DialogEntry(entry_id="fixed", entry_type="response")
    assert e2.entry_id == "fixed"


# ── 生命周期 ───────────────────────────────────────────────────────────

def test_clear_turn_state():
    bb = _bb()
    bb.set_goal("目标")
    bb.set_plan([{"step": 1}])
    bb.write_thought("m", "large", "思考")
    bb.clear_turn_state()
    assert bb.goal == ""
    assert bb.observations == []
    assert bb.delegations == {}
    assert bb.size() == 0


# ── 观察 ───────────────────────────────────────────────────────────────

def test_add_observation_overflow(monkeypatch):
    bb = _bb()
    bb.MAX_OBSERVATIONS = 3
    monkeypatch.setattr("modules.database.blackboard_repo.save_observation", lambda s, o: None)
    for i in range(5):
        bb.add_observation("expert", f"obs{i}")
    assert len(bb.observations) == 3
    assert bb._deleted_obs == 2
    assert bb.observations[-1].content == "obs4"


def test_add_observation_save_fails(monkeypatch):
    bb = _bb()
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr("modules.database.blackboard_repo.save_observation", boom)
    oid = bb.add_observation("large", "内容")
    assert oid


def test_observations_since(monkeypatch):
    bb = _bb()
    monkeypatch.setattr("modules.database.blackboard_repo.save_observation", lambda s, o: None)
    for i in range(4):
        bb.add_observation("large", f"o{i}")
    obs = bb.get_observations_since(2)
    assert [o.content for o in obs] == ["o2", "o3"]


# ── 委托 ───────────────────────────────────────────────────────────────

def test_write_and_update_delegation():
    bb = _bb()
    did = bb.write_delegation("expert", "任务", {"k": "v"})
    assert did in bb.delegations
    assert bb.update_delegation_status(did, "replied", {"note": "x"}) is True
    assert bb.delegations[did].status == "replied"
    assert bb.delegations[did].metadata["note"] == "x"
    assert bb.update_delegation_status("不存在", "replied") is False


# ── 专家发现 / 运行时状态 / 安全拦截 ─────────────────────────────────

def test_write_expert_finding_and_state():
    bb = _bb()
    fid = bb.write_expert_finding("expert", "code_writer", "发现内容", status="pending")
    assert bb.expert_findings[fid].status == "pending"
    bb.set_runtime_state({"step": 1})
    assert bb.runtime_state == {"step": 1}
    bb.set_final_response("最终")
    assert bb.final_response == "最终"


def test_security_block_lifecycle():
    bb = _bb()
    assert bb.has_security_block() is False
    bb.set_security_block("危险", "删除数据库", risk_level="high")
    assert bb.has_security_block() is True
    block = bb.get_security_block()
    assert block["category"] == "危险"
    bb.clear_security_block()
    assert bb.has_security_block() is False
    assert bb.get_security_block() is None


# ── 对话框 ─────────────────────────────────────────────────────────────

def test_on_change_callback(monkeypatch):
    bb = _bb()
    calls = []
    bb.on_change(lambda sid: calls.append(sid))
    bb._notify_change()
    assert calls == ["s1"]


def test_on_change_callback_error(monkeypatch):
    bb = _bb()
    def boom(sid):
        raise RuntimeError("cb boom")
    bb.on_change(boom)
    bb._notify_change()  # 不抛异常


def test_broadcast_no_loop(monkeypatch):
    import modules.thinking.communication.message_bus as mb
    bus = MagicMock()
    bus.broadcast = AsyncMock()
    monkeypatch.setattr(mb, "get_message_bus", lambda: bus)
    bb = _bb()
    bb._broadcast(DialogEntry(entry_type="thought", model_id="m", tier="large", content="x"))
    # 无运行中事件循环 → 不创建 task，静默跳过（不抛错）
    bus.broadcast.assert_not_awaited()


def test_write_thought_empty_returns_none():
    bb = _bb()
    assert bb.write_thought("m", "large", "   ") is None


def test_write_thought_response_user_input(monkeypatch):
    bb = _bb()
    monkeypatch.setattr("modules.database.blackboard_repo.save_observation", lambda s, o: None)
    t = bb.write_thought("m", "large", "思考", round_num=2)
    assert t.entry_type == "thought"
    r = bb.write_response("m", "large", "回复")
    assert r.entry_type == "response"
    u = bb.write_user_input("你好")
    assert u.entry_type == "user_input"
    entries = bb.read_dialog()
    assert len(entries) == 3


def test_new_entries():
    bb = _bb()
    bb.write_user_input("a")
    first = bb.new_entries()
    assert len(first) == 1
    bb.write_user_input("b")
    second = bb.new_entries()
    assert len(second) == 1
    assert bb.new_entries() == []


def test_get_latest_response_and_thought():
    bb = _bb()
    bb.write_thought("m", "large", "思考1")
    bb.write_response("m", "large", "回复1")
    bb.write_thought("e", "expert", "思考2")
    resp = bb.get_latest_response(tier="large")
    assert resp["content"] == "回复1"
    thought = bb.get_latest_thought(tier="expert")
    assert thought["content"] == "思考2"
    assert bb.get_latest_thought(tier="none") is None
    # after_timestamp 边界
    old = bb.get_latest_response(after_timestamp=9999999999)
    assert old is None


def test_format_for_model():
    bb = _bb()
    bb.write_response("m", "large", "回复")
    bb.write_user_input("问题")
    out = bb.format_for_model(exclude_tier="large", after_index=1)
    assert "用户" in out
    out2 = bb.format_for_model()
    assert "回复" in out2


def test_format_for_model_empty():
    bb = _bb()
    assert bb.format_for_model() == ""


# ── 快照 ───────────────────────────────────────────────────────────────

def test_snapshot_for_large(monkeypatch):
    bb = _bb()
    bb.set_goal("g")
    bb.set_runtime_state({"x": 1})
    bb.add_observation("large", "观察")
    snap = bb.snapshot_for("large")
    assert snap.goal == "g"
    assert len(snap.observations) == 1
    assert "turn_id" in snap.metadata


def test_snapshot_for_supervisor(monkeypatch):
    bb = _bb()
    monkeypatch.setattr("modules.database.blackboard_repo.save_observation", lambda s, o: None)
    bb.add_observation("large", "大模型观察")
    bb.add_observation("expert", "专家观察")
    bb.write_expert_finding("supervisor", "s", "主管发现")
    bb.write_expert_finding("expert", "e", "专家发现")
    snap = bb.snapshot_for("supervisor")
    assert {o.content for o in snap.observations} == {"专家观察"}
    assert "主管发现" in [f.content for f in snap.expert_findings.values()]


def test_snapshot_for_expert(monkeypatch):
    bb = _bb()
    monkeypatch.setattr("modules.database.blackboard_repo.save_observation", lambda s, o: None)
    for i in range(8):
        bb.add_observation("large", f"o{i}")
    snap = bb.snapshot_for("expert")
    assert len(snap.observations) == 5
    assert snap.delegations == {}
    assert snap.expert_findings == {}


def test_snapshot_for_other():
    bb = _bb()
    snap = bb.snapshot_for("unknown")
    assert snap.observations == []


def test_get_status():
    bb = _bb()
    bb.set_goal("g")
    st = bb.get_status()
    assert st["goal_set"] is True
    assert st["turn_id"] == "t1"
