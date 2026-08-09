"""multi_model_orchestrator 纯方法测试（此前 14% 覆盖）"""
from unittest.mock import MagicMock

import modules.thinking.multi_model_orchestrator as orc_mod
from modules.thinking.multi_model_orchestrator import MultiModelOrchestrator, get_active_sessions


def test_get_active_sessions(monkeypatch):
    monkeypatch.setattr(orc_mod, "_session_registry", {"s1": {"session_id": "s1"}})
    monkeypatch.setattr(orc_mod, "_session_registry_lock", __import__("threading").Lock())
    assert get_active_sessions() == [{"session_id": "s1"}]


def _orc():
    o = MultiModelOrchestrator.__new__(MultiModelOrchestrator)
    return o


def test_build_security_error():
    o = _orc()
    d = o._build_security_error("违规内容", 100.0)
    assert d["response"] == "[安全拦截] 违规内容"
    assert d["focus"] == "security_blocked"
    assert d["security_passed"] is False


def test_match_skill(monkeypatch):
    o = _orc()
    import modules.thinking.skills as skills_mod
    mgr = MagicMock()
    skill = MagicMock()
    skill.id = "code_writer"
    skill.name = "写代码"
    mgr.match_skill.return_value = skill
    monkeypatch.setattr(skills_mod, "skill_manager", mgr)
    assert o._match_skill("帮我写代码") == "code_writer"


def test_match_skill_no_match(monkeypatch):
    o = _orc()
    import modules.thinking.skills as skills_mod
    mgr = MagicMock()
    mgr.match_skill.return_value = None
    monkeypatch.setattr(skills_mod, "skill_manager", mgr)
    assert o._match_skill("随便聊聊") == ""


def test_match_skill_exception(monkeypatch):
    o = _orc()
    import modules.thinking.skills as skills_mod
    mgr = MagicMock()
    mgr.match_skill.side_effect = RuntimeError("boom")
    monkeypatch.setattr(skills_mod, "skill_manager", mgr)
    assert o._match_skill("hi") == ""


def test_is_user_visible_response():
    assert orc_mod.MultiModelOrchestrator._is_user_visible_response(None) is False
    assert orc_mod.MultiModelOrchestrator._is_user_visible_response({"content": "   "}) is False
    assert orc_mod.MultiModelOrchestrator._is_user_visible_response({"metadata": {"internal_protocol": True}, "content": "x"}) is False
    assert orc_mod.MultiModelOrchestrator._is_user_visible_response({"metadata": {"final_visible": False}, "content": "x"}) is False
    assert orc_mod.MultiModelOrchestrator._is_user_visible_response({"metadata": {}, "content": "delegate_task(专家)"}) is False
    assert orc_mod.MultiModelOrchestrator._is_user_visible_response({"metadata": {}, "content": "正常回复"}) is True
