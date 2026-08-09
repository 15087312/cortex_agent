"""multi_model_orchestrator 纯方法测试（此前 14% 覆盖）"""
from unittest.mock import AsyncMock, MagicMock

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


def test_review_output_cleaned(monkeypatch):
    o = _orc()
    reviewer = MagicMock()
    reviewer.review = MagicMock(return_value="清洗后")
    async def fake_review(r, u):
        return "清洗后"
    reviewer.review = fake_review
    o._get_output_reviewer = lambda: reviewer
    import asyncio
    assert asyncio.run(o._review_output("原始", "用户")) == "清洗后"


def test_review_output_security_block(monkeypatch):
    o = _orc()
    reviewer = MagicMock()
    async def fake_review(r, u):
        return "清洗后"
    reviewer.review = fake_review
    o._get_output_reviewer = lambda: reviewer
    bb = MagicMock()
    bb.has_security_block.return_value = True
    bb.get_security_block.return_value = {"category": "danger", "description": "危险操作", "risk_level": "high"}
    import asyncio
    out = asyncio.run(o._review_output("x", "y", blackboard=bb))
    assert "安全审查拦截" in out


def test_review_output_no_block(monkeypatch):
    o = _orc()
    reviewer = MagicMock()
    async def fake_review(r, u):
        return "结果"
    reviewer.review = fake_review
    o._get_output_reviewer = lambda: reviewer
    import asyncio
    assert asyncio.run(o._review_output("x", "y", blackboard=None)) == "结果"


def test_conscience_feedback(monkeypatch):
    o = _orc()
    import modules.thinking.conscience as cons_mod
    cons = MagicMock()
    cons.analyze_feedback = AsyncMock(return_value=None)
    monkeypatch.setattr(cons_mod, "get_conscience", lambda: cons)
    import asyncio
    asyncio.run(o._conscience_feedback("u", "r"))
    cons.analyze_feedback.assert_awaited_once()


def test_maybe_evolve_values_short_response(monkeypatch):
    o = _orc()
    import asyncio
    asyncio.run(o._maybe_evolve_values("hi", "短"))  # <20 字符直接返回
    # 不抛异常


def test_maybe_evolve_values_risk_keyword(monkeypatch):
    o = _orc()
    import modules.thinking.conscience as cons_mod
    cons = MagicMock()
    cons.review_and_evolve = AsyncMock(return_value=None)
    monkeypatch.setattr(cons_mod, "get_conscience", lambda: cons)
    import asyncio
    long_resp = "我将删除这个文件，因为它包含了多个不再需要的旧版本数据，这是较长的回复内容"
    asyncio.run(o._maybe_evolve_values("请删除这个文件", long_resp))
    cons.review_and_evolve.assert_awaited_once()
