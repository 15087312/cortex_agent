"""会话执行图谱测试（呼唤/回复边、tier 推断、user 归一化、持久化）"""
import pytest

from modules.thinking.session_graph import SessionGraphStore


def _make():
    return SessionGraphStore()


def test_record_generates_call_reply_pairs():
    s = _make()
    s.record("s1", "orch", "总指挥", "large", "", "response", "你好", 1.0)
    s.record("s1", "sup", "代码主管", "supervisor", "orch", "response", "收到", 2.0)
    s.record("s1", "exp", "代码专家", "expert", "sup", "response", "完成", 3.0)
    g = s.get_graph("s1")
    tiers = {n["id"]: n["tier"] for n in g["nodes"]}
    assert tiers["orch"] == "large"
    assert tiers["sup"] == "supervisor"
    assert tiers["exp"] == "expert"
    types = {(e["from"], e["to"], e["type"]) for e in g["edges"]}
    assert ("__user__", "orch", "呼唤") in types
    assert ("orch", "__user__", "回复") in types
    assert ("orch", "sup", "呼唤") in types
    assert ("sup", "orch", "回复") in types
    assert ("sup", "exp", "呼唤") in types
    assert ("exp", "sup", "回复") in types
    assert not any(e["from"] == e["to"] for e in g["edges"])


def test_user_input_skipped():
    s = _make()
    s.record("s1", "user", "用户", "user", "", "input", "你好", 1.0)
    g = s.get_graph("s1")
    assert g["nodes"] == [] and g["edges"] == []


def test_return_to_tier_inference():
    s = _make()
    s.record("s1", "exp", "专家", "expert", "sup_parent", "response", "x", 1.0)
    g = s.get_graph("s1")
    sup = next(n for n in g["nodes"] if n["id"] == "sup_parent")
    assert sup["tier"] == "supervisor"


def test_snapshot_restore():
    s = _make()
    s.record("s1", "orch", "总指挥", "large", "", "response", "hi", 1.0)
    snap = s.snapshot("s1")
    s2 = _make()
    s2.restore("s1", snap)
    g = s2.get_graph("s1")
    assert len(g["nodes"]) == 2  # 用户 + 总指挥
    assert len(g["edges"]) == 2
