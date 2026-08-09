"""multi_model_orchestrator 纯方法测试（此前 14% 覆盖）"""
from unittest.mock import AsyncMock, MagicMock

import pytest

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


def _no_forced(monkeypatch):
    """隔离：确保不受用户真实 ~/.cortex/personas.yaml 中 forced_skill 残留影响"""
    # 注意：settings 是 pydantic BaseSettings 实例（__setattr__ 拒绝未知字段），
    # 不能直接改实例属性；替换 config.settings 模块上的 settings 属性为 fake。
    # 函数内 `from config.settings import settings` 走 sys.modules 属性查找，会拿到 fake。
    import sys, types
    cfg_mod = sys.modules["config.settings"]
    monkeypatch.setattr(cfg_mod, "settings", types.SimpleNamespace(get_forced_skill=lambda: ""))


@pytest.fixture
def skill_mgr(monkeypatch, tmp_path):
    """真实 SkillManager（临时目录）"""
    import modules.thinking.skills.manager as mgr_mod
    monkeypatch.setattr(mgr_mod, "_get_skills_dir", lambda: tmp_path)
    mgr = mgr_mod.SkillManager()
    import modules.thinking.skills as skills_mod
    monkeypatch.setattr(skills_mod, "skill_manager", mgr)
    return mgr


def test_match_skill(skill_mgr, monkeypatch):
    """真实 skill_manager：创建技能后输入匹配命中"""
    skill_mgr.create_skill(
        skill_id="code_writer", name="写代码", description="帮助编写和修改代码",
        keywords=["代码", "写代码", "编程"], trigger={"include": ["写代码", "编程"]},
    )
    o = _orc()
    _no_forced(monkeypatch)
    assert o._match_skill("帮我写代码") == "code_writer"


def test_match_skill_no_match(skill_mgr, monkeypatch):
    """真实 manager：无匹配返回空"""
    skill_mgr.create_skill(
        skill_id="code_writer", name="写代码", description="d",
        keywords=["代码"], trigger=None,
    )
    o = _orc()
    _no_forced(monkeypatch)
    assert o._match_skill("今天天气怎么样") == ""
    o = _orc()
    _no_forced(monkeypatch)
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
    _no_forced(monkeypatch)
    import modules.thinking.skills as skills_mod
    mgr = MagicMock()
    mgr.match_skill.return_value = None
    monkeypatch.setattr(skills_mod, "skill_manager", mgr)
    assert o._match_skill("随便聊聊") == ""


def test_match_skill_forced_priority(monkeypatch):
    o = _orc()
    import sys, types
    cfg_mod = sys.modules["config.settings"]
    monkeypatch.setattr(cfg_mod, "settings", types.SimpleNamespace(get_forced_skill=lambda: "code_writer"))
    import modules.thinking.skills as skills_mod
    mgr = MagicMock()
    mgr.match_skill.return_value = None
    monkeypatch.setattr(skills_mod, "skill_manager", mgr)
    assert o._match_skill("随便聊聊") == "code_writer"


def test_match_skill_exception(monkeypatch):
    o = _orc()
    _no_forced(monkeypatch)
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


def test_review_output_cleaned():
    """真实 OutputSystemReviewAdapter：输出清洗"""
    from modules.thinking.multi_model_orchestrator import MultiModelOrchestrator
    o = MultiModelOrchestrator()
    import asyncio
    out = asyncio.run(o._review_output("原始文本", "用户"))
    assert isinstance(out, str) and out  # 真实清洗结果


def test_review_output_security_block():
    """真实 blackboard 设置 security block → 拦截提示"""
    from modules.thinking.multi_model_orchestrator import MultiModelOrchestrator
    from modules.thinking.cognition.blackboard import CognitiveBlackboard
    o = MultiModelOrchestrator()
    bb = CognitiveBlackboard(session_id="s", turn_id="t")
    bb.set_security_block("danger", "危险操作", "high")
    import asyncio
    out = asyncio.run(o._review_output("x", "y", blackboard=bb))
    assert "安全审查拦截" in out


def test_review_output_no_block():
    """无 security block → 返回清洗结果"""
    from modules.thinking.multi_model_orchestrator import MultiModelOrchestrator
    o = MultiModelOrchestrator()
    import asyncio
    out = asyncio.run(o._review_output("结果文本", "y", blackboard=None))
    assert isinstance(out, str) and out


def test_conscience_feedback_real():
    """真实 conscience：无分析节点时 analyze_feedback 早退（不崩）"""
    from modules.thinking.multi_model_orchestrator import MultiModelOrchestrator
    o = MultiModelOrchestrator()
    import asyncio
    asyncio.run(o._conscience_feedback("u", "r"))


def test_maybe_evolve_values_short_response():
    """响应过短 → 不触发价值观演化（不崩）"""
    from modules.thinking.multi_model_orchestrator import MultiModelOrchestrator
    o = MultiModelOrchestrator()
    import asyncio
    asyncio.run(o._maybe_evolve_values("hi", "短"))


def test_process_security_blocked(monkeypatch):
    o = _orc()
    sec = MagicMock()
    sec.validate_input.return_value = (False, "非法输入")
    o._get_security = lambda: sec
    notifier = MagicMock()
    o._get_activity_notifier = lambda: notifier
    import asyncio
    result = asyncio.run(o.process("危险内容", session_id="s1"))
    assert result["security_passed"] is False
    assert "非法输入" in result["response"]


def test_process_activity_notify_error(monkeypatch):
    o = _orc()
    notifier = MagicMock()
    notifier.notify_activity.side_effect = RuntimeError
    o._get_activity_notifier = lambda: notifier
    sec = MagicMock()
    sec.validate_input.return_value = (True, "")
    o._get_security = lambda: sec
    o._get_guidance_service = lambda: MagicMock(run=AsyncMock(return_value={}))
    o._get_context_controller = lambda: None
    o._get_output_reviewer = lambda: None
    o._review_output = AsyncMock(return_value="ok")
    import modules.perception.setup as ps_mod
    ps = MagicMock()
    ps.proactive_trigger = None
    monkeypatch.setattr(ps_mod, "get_perception_system", lambda: ps)
    # 后续 _execute_multi_model_thinking 走 mock
    o._execute_multi_model_thinking = MagicMock()
    async def fake_exec(user_input, session_id, expert_guidance, event_callback, **kw):
        return {"response": "ok", "focus": "thinking", "active_modules": [], "sleep_modules": [],
                "degraded": False, "module_results": [], "decisions": {}, "resource_status": {},
                "security_passed": True}
    o._execute_multi_model_thinking = fake_exec
    import asyncio
    result = asyncio.run(o.process("正常内容", session_id="s1"))
    assert result["security_passed"] is True


def test_execute_multi_model_failure_path(monkeypatch):
    o = _orc()
    import asyncio
    # SessionLifecycle / RunnerManager 初始化安全 mock
    import modules.thinking.context.pool as pool_mod
    import modules.thinking.cognition.blackboard as bb_mod
    import modules.thinking.core.model_runner as mr_mod
    import modules.thinking.communication.message_bus as mb_mod

    turn = MagicMock()
    turn.turn_id = "turn1"
    monkeypatch.setattr(pool_mod, "TurnContext", lambda **k: turn)
    bb = MagicMock()
    bb.runtime_state = {}
    monkeypatch.setattr(bb_mod, "CognitiveBlackboard", lambda **k: bb)

    rm = MagicMock()
    rm.start_listening = AsyncMock()
    monkeypatch.setattr(mr_mod, "get_runner_manager", lambda *a, **k: rm)
    monkeypatch.setattr(mr_mod, "remove_runner_manager", AsyncMock())

    # get_message_bus 抛异常 → 主循环走 except
    monkeypatch.setattr(mb_mod, "get_message_bus", lambda: (_ for _ in ()).throw(RuntimeError("总线挂了")))

    async def cb(event):
        return None
    result = asyncio.run(o._execute_multi_model_thinking("你好", "s1", {}, cb))
    assert "[思考失败]" in result["response"] or "总线" in result["response"]


def test_execute_multi_model_success_no_runner(monkeypatch):
    o = _orc()
    import asyncio
    import modules.thinking.context.pool as pool_mod
    import modules.thinking.cognition.blackboard as bb_mod
    import modules.thinking.core.model_runner as mr_mod

    turn = MagicMock()
    turn.turn_id = "turn1"
    monkeypatch.setattr(pool_mod, "TurnContext", lambda **k: turn)
    bb = MagicMock()
    bb.runtime_state = {}
    bb.final_response = "大模型回复"
    bb.write_user_input.return_value = MagicMock(timestamp=1.0)
    monkeypatch.setattr(bb_mod, "CognitiveBlackboard", lambda **k: bb)
    rm = MagicMock()
    rm.start_listening = AsyncMock()
    monkeypatch.setattr(mr_mod, "get_runner_manager", lambda *a, **k: rm)
    monkeypatch.setattr(mr_mod, "remove_runner_manager", AsyncMock())

    import modules.thinking.communication.message_bus as mb_mod
    bus = MagicMock()
    bus.send = AsyncMock()
    bus.subscribe = AsyncMock()
    bus.unsubscribe = AsyncMock()
    async def fake_receive(channel):
        return type("M", (), {"content": {"action": "thinking_complete", "tier": "large", "session_id": "s1"}})()
    bus.receive = fake_receive
    bus.peek = AsyncMock(return_value=[])
    monkeypatch.setattr(mb_mod, "get_message_bus", lambda: bus)

    import config.prompts.composer as comp_mod
    composer = MagicMock()
    composer._build_supervisor_table.return_value = "主管表"
    composer._build_expert_table.return_value = "专家表"
    monkeypatch.setattr(comp_mod, "PromptComposer", lambda: composer)

    async def cb(event):
        return None
    result = asyncio.run(o._execute_multi_model_thinking("你好", "s1", {}, cb, model_id="large_primary"))
    assert result["response"] == "大模型回复"


def test_dependency_getters_real_impl(monkeypatch):
    """编排器依赖获取真实实现：懒加载 + 缓存（真实 __init__ 构造）"""
    from modules.thinking.multi_model_orchestrator import MultiModelOrchestrator
    o = MultiModelOrchestrator()  # 真实 __init__（无副作用，纯赋值）
    # 首次调用创建并缓存
    r1 = o._get_output_reviewer()
    assert r1 is o._output_reviewer  # 缓存
    assert o._get_output_reviewer() is r1  # 二次调用返回缓存
    # activity notifier / security / guidance 懒加载（用 mock 注入避免副作用污染全局）
    notifier = MagicMock()
    sec = MagicMock()
    guidance = MagicMock()
    import modules.thinking.adapters as ad_mod
    monkeypatch.setattr(ad_mod, "DifferenceDetectorActivityNotifier", lambda: notifier)
    monkeypatch.setattr(ad_mod, "SecurityApiAdapter", lambda: sec)
    monkeypatch.setattr(ad_mod, "PreGenExpertGuidanceAdapter", lambda: guidance)
    assert o._get_activity_notifier() is notifier
    assert o._get_activity_notifier() is o._activity_notifier
    assert o._get_security() is sec
    assert o._get_guidance_service() is guidance
