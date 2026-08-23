"""multi_model_orchestrator 测试（此前 10% 覆盖）：技能匹配、委托可用性"""
from unittest.mock import MagicMock, patch

import modules.thinking.multi_model_orchestrator as mmo
from modules.thinking.multi_model_orchestrator import MultiModelOrchestrator


def _orch():
    return MultiModelOrchestrator.__new__(MultiModelOrchestrator)


def _no_forced(monkeypatch):
    """隔离：确保不受用户真实 ~/.cortex/personas.yaml 中 forced_skill 残留影响"""
    # 注意：settings 是 pydantic BaseSettings 实例（__setattr__ 拒绝未知字段），
    # 不能直接改实例属性；替换 config.settings 模块上的 settings 属性为 fake。
    import sys, types
    cfg_mod = sys.modules["config.settings"]
    monkeypatch.setattr(cfg_mod, "settings", types.SimpleNamespace(get_forced_skill=lambda: ""))


def test_match_skill_returns_id(monkeypatch):
    orch = _orch()
    _no_forced(monkeypatch)
    fake_skill = MagicMock()
    fake_skill.id = "code_review"
    fake_skill.name = "代码审查"
    captured = {}
    def _match(q, role=""):
        captured["role"] = role
        return fake_skill
    monkeypatch.setattr("modules.thinking.skills.skill_manager.match_skill", _match)
    assert orch._match_skill("帮我审查代码") == "code_review"
    # 关键回归：role 必须跟随当前激活的总指挥，而非硬编码 orchestrator
    assert captured["role"] == mmo.resolve_active_large_role()


def test_match_skill_no_match(monkeypatch):
    orch = _orch()
    _no_forced(monkeypatch)
    monkeypatch.setattr("modules.thinking.skills.skill_manager.match_skill",
                        lambda q, role="": None)
    assert orch._match_skill("你好") == ""


def test_match_skill_forced_priority(monkeypatch):
    """设置了强制技能时，_match_skill 直接返回强制技能（不依赖自动匹配）"""
    orch = _orch()
    import sys, types
    cfg_mod = sys.modules["config.settings"]
    monkeypatch.setattr(cfg_mod, "settings", types.SimpleNamespace(get_forced_skill=lambda: "code_review"))
    # 即使自动匹配会抛异常，强制技能优先也应返回
    monkeypatch.setattr("modules.thinking.skills.skill_manager.match_skill",
                        lambda q, role="": (_ for _ in ()).throw(RuntimeError("不应被调用")))
    assert orch._match_skill("随便聊聊") == "code_review"


def test_match_skill_exception_safe(monkeypatch):
    orch = _orch()
    _no_forced(monkeypatch)
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
