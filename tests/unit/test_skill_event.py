"""技能生命周期事件（type='skill'）发射测试。

只验证 ModelRunner._set_active_skill 在技能激活/停用/去重时，
正确经由 _push_skill_event 发出 skill_loaded / skill_unloaded 事件。
用 object.__new__ 构造最小实例，跳过重型 __init__。
"""
import pytest


def _make_runner():
    from modules.thinking.core.model_runner import ModelRunner

    r = object.__new__(ModelRunner)
    r._active_skill = None
    r._active_skill_tool_rules = None
    r.session_id = "s1"
    r.tier = "large"
    return r


class _Skill:
    def __init__(self, sid, name, rules=None):
        self.id = sid
        self.name = name
        self.tool_rules = rules


def test_set_active_skill_emits_loaded():
    r = _make_runner()
    sent = []
    r._push_skill_event = lambda event, **kw: sent.append((event, kw))

    r._set_active_skill(_Skill("deep-investigate", "深度调查"))

    assert len(sent) == 1
    event, kw = sent[0]
    assert event == "skill_loaded"
    assert kw["skill_id"] == "deep-investigate"
    assert kw["name"] == "深度调查"
    assert kw["phase"] == "start"
    assert r._active_skill.id == "deep-investigate"
    assert r._active_skill_tool_rules is None


def test_set_active_skill_dedup_same_skill():
    r = _make_runner()
    sent = []
    r._push_skill_event = lambda event, **kw: sent.append((event, kw))

    r._set_active_skill(_Skill("a", "技能A"))
    r._set_active_skill(_Skill("a", "技能A"))  # 同一技能重复设置 → 不重复发射

    assert [e for e, _ in sent] == ["skill_loaded"]
    assert r._active_skill.id == "a"


def test_set_active_skill_switch_and_unload():
    r = _make_runner()
    sent = []
    r._push_skill_event = lambda event, **kw: sent.append((event, kw))

    r._set_active_skill(_Skill("a", "技能A"))
    r._set_active_skill(_Skill("b", "技能B"))  # 切换 → 不会发 unload，只发新的 loaded
    assert [e for e, _ in sent] == ["skill_loaded", "skill_loaded"]
    assert r._active_skill.id == "b"

    r._set_active_skill(None)  # 停用 → skill_unloaded
    assert sent[-1][0] == "skill_unloaded"
    assert sent[-1][1]["skill_id"] == "b"
    assert r._active_skill is None
