"""multi_model_orchestrator 测试（此前 10% 覆盖）：技能匹配、委托可用性"""
from unittest.mock import MagicMock, patch

import modules.thinking.multi_model_orchestrator as mmo
from modules.thinking.multi_model_orchestrator import MultiModelOrchestrator


def _orch():
    return MultiModelOrchestrator.__new__(MultiModelOrchestrator)


def test_match_skill_returns_id(monkeypatch):
    orch = _orch()
    fake_skill = MagicMock()
    fake_skill.id = "code_review"
    fake_skill.name = "代码审查"
    monkeypatch.setattr("modules.thinking.skills.skill_manager.match_skill",
                        lambda q, role="": fake_skill)
    assert orch._match_skill("帮我审查代码") == "code_review"


def test_match_skill_no_match(monkeypatch):
    orch = _orch()
    monkeypatch.setattr("modules.thinking.skills.skill_manager.match_skill",
                        lambda q, role="": None)
    assert orch._match_skill("你好") == ""


def test_match_skill_exception_safe(monkeypatch):
    orch = _orch()
    def _boom(q, role=""):
        raise RuntimeError("skill 异常")
    monkeypatch.setattr("modules.thinking.skills.skill_manager.match_skill", _boom)
    assert orch._match_skill("任意输入") == ""


def test_guidance_service_lazy(monkeypatch):
    orch = _orch()
    orch._guidance_service = None
    # 懒加载 PreGenExpertGuidanceAdapter
    svc = orch._get_guidance_service()
    assert svc is not None
    # 幂等
    assert orch._get_guidance_service() is svc
