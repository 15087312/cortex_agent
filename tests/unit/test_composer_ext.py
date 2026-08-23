"""config/prompts/composer.py 补测 — PromptComposer 全部分支

Mock loader（假 YAML 数据）与 settings 人设/覆盖，不触碰真实文件。
"""
from unittest.mock import MagicMock, patch

from config.prompts.composer import PromptComposer, PromptRequest, RoleInfo


def _settings():
    from config.settings import settings
    return type(settings)


def _loader(data=None):
    loader = MagicMock()
    loader.load.side_effect = lambda name: (data or {}).get(name)
    return loader


def _composer(data=None):
    c = PromptComposer.__new__(PromptComposer)
    c._loader = _loader(data)
    return c


def _req(**kw):
    defaults = dict(tier="large", role="orchestrator", mode="edit")
    defaults.update(kw)
    return PromptRequest(**defaults)


# ── build() 轮次上下文 ─────────────────────────────────────────────────────

def test_build_non_context_pool():
    c = _composer()
    assert c.build("not-a-pool", "orchestrator", "large", "问题") == "【当前任务】\n问题"


def test_build_no_question():
    c = _composer()
    assert c.build("x", "r", "t") == ""


def test_build_with_turn_context(monkeypatch):
    class FakeCtx:
        def view(self, role):
            return f"round-{role}"
    monkeypatch.setattr("modules.thinking.context.pool.TurnContext", FakeCtx)
    c = _composer()
    out = c.build(FakeCtx(), "orchestrator", "large", "q")
    assert "round-orchestrator" in out
    assert "【当前任务】" in out


# ── build_system 顶层 ───────────────────────────────────────────────────────

def test_build_system_override_from_settings():
    c = _composer()
    with patch.object(_settings(), "get_system_override", return_value="OVERRIDE"):
        assert c.build_system(_req(custom_system="  custom  ")) == "custom"
        assert c.build_system(_req()) == "OVERRIDE"


def test_build_system_override_exception():
    c = _composer()
    with patch.object(_settings(), "get_system_override", side_effect=RuntimeError("boom")):
        out = c.build_system(_req())
    assert "【工具使用】" in out


def test_build_system_conscience_guidance():
    c = _composer()
    out = c.build_system(_req(conscience_guidance="上次学会的东西"))
    assert "【你回忆起的过往经验】" in out
    assert "上次学会的东西" in out


def test_build_system_with_skill():
    data = {"base": {"tiers": {}, "safety": [], "perception": [], "modes": {},
                     "network": [], "output": {}, "execution": {}, "tool_rules": {},
                     "values": {}}, "roles": {"roles": {}}}
    c = _composer(data)
    fake = MagicMock()
    fake.to_prompt_block.return_value = "SKILL BLOCK"
    with patch("modules.thinking.skills.skill_manager.get_skill", return_value=fake):
        out = c.build_system(_req(skill_id="web_surfer"))
    assert "SKILL BLOCK" in out


def test_build_system_skill_not_found():
    c = _composer({"base": {}})
    with patch("modules.thinking.skills.skill_manager.get_skill", return_value=None):
        assert "SKILL" not in c.build_system(_req(skill_id="missing"))


def test_build_system_skill_error():
    c = _composer({"base": {}})
    with patch("modules.thinking.skills.skill_manager.get_skill",
               side_effect=RuntimeError("skill down")):
        assert c.build_system(_req(skill_id="bad")) != ""


# ── _get_role ───────────────────────────────────────────────────────────────

def test_get_role_known_role():
    data = {"roles": {"roles": {"orchestrator": {
        "name": "系统主模型", "tier": "large", "model_id": "m1",
        "personality": "p", "speaking_style": "s",
        "expertise": "a,b", "weaknesses": ["w"], "tool_whitelist": ["x"]}}}}
    r = _composer(data)._get_role("orchestrator")
    assert r.name == "系统主模型"
    assert r.expertise == ["a", "b"]
    assert r.weaknesses == ["w"]
    assert r.tool_whitelist == ["x"]


def test_get_role_fallback_orchestrator():
    data = {"roles": {"roles": {"orchestrator": {"name": "默认"}}}}
    r = _composer(data)._get_role("unknown_role")
    assert r.name == "默认"


def test_get_role_custom_identity(monkeypatch):
    data = {"roles": {"roles": {"orchestrator": {"name": "默认"}}}}
    c = _composer(data)
    monkeypatch.setattr("modules.thinking.identity.get_identities",
                        lambda: {"custom_agent": {"name": "自定义", "expertise": ["x"]}})
    r = c._get_role("custom_agent")
    assert r.name == "自定义"


def test_get_role_custom_identity_exception(monkeypatch):
    data = {"roles": {"roles": {"orchestrator": {"name": "默认"}}}}
    c = _composer(data)
    monkeypatch.setattr("modules.thinking.identity.get_identities",
                        lambda: (_ for _ in ()).throw(RuntimeError("no identity")))
    r = c._get_role("custom_agent")
    assert r.name == "默认"
    assert _composer(data)._get_role("orchestrator").name == "默认"


def test_get_role_custom_identity_empty(monkeypatch):
    data = {"roles": {"roles": {"orchestrator": {"name": "默认"}}}}
    c = _composer(data)
    monkeypatch.setattr("modules.thinking.identity.get_identities", lambda: {})
    r = c._get_role("custom_agent")
    assert r.name == "默认"


# ── _build_identity ─────────────────────────────────────────────────────────

def test_build_identity_full():
    data = {"base": {"tiers": {"large": {"identity": "我是层级身份"}}}}
    c = _composer(data)
    role = RoleInfo(key="r", name="R", tier="large", personality="P",
                    speaking_style="S", expertise=["e1"], weaknesses=["w1"])
    out = c._build_identity(role)
    assert "我是层级身份" in out
    assert "【工具使用】" in out


def test_build_identity_no_extra():
    c = _composer()
    role = RoleInfo(key="r", name="R", personality="P", speaking_style="S")
    out = c._build_identity(role)
    assert "【擅长】" not in out
    assert "【不擅长】" not in out


def test_build_identity_custom_persona():
    c = _composer({"base": {"tiers": {}}})
    with patch.object(_settings(), "get_persona", return_value="自定义人设"):
        out = c._build_identity(RoleInfo(key="r", personality="默认", speaking_style="S"))
    assert "【工具使用】" in out


def test_build_identity_persona_exception():
    c = _composer({"base": {"tiers": {}}})
    with patch.object(_settings(), "get_persona", side_effect=RuntimeError("boom")):
        out = c._build_identity(RoleInfo(key="r", personality="默认", speaking_style="S"))
    assert "【工具使用】" in out


# ── _build_rules 各段 ───────────────────────────────────────────────────────

def test_build_rules_all_sections():
    data = {"base": {
        "safety": ["s1", "s2"],
        "perception": ["p1"],
        "modes": {"edit": ["m1"]},
        "network": ["n1"],
        "output": {"large": ["o1"]},
        "execution": {"large": ["e1"]},
    }}
    out = _composer(data)._build_rules(_req())
    assert "【安全规则 — 强制执行】" in out[0]
    assert "【被动感知系统】" in out[1]
    assert "【执行模式: EDIT】" in out[2]
    assert "【网络内容处理规则】" in out[3]
    assert "【输出规则】" in out[4]
    assert "【执行要求】" in out[5]


def test_build_rules_empty():
    c = _composer({"base": {}})
    assert c._build_rules(_req()) == []


# ── 能力表 ──────────────────────────────────────────────────────────────────

def test_capability_table_supervisor():
    data = {"roles": {"roles": {"w": {"tier": "expert", "expertise": ["x"]}}}}
    out = _composer(data)._build_capability_table(RoleInfo(), "supervisor")
    assert "【可委托的专家】" in out


def test_capability_table_large():
    data = {"roles": {"roles": {"s": {"tier": "supervisor", "expertise": ["x"]}}}}
    out = _composer(data)._build_capability_table(RoleInfo(), "large")
    assert "【可委托的主管】" in out


def test_capability_table_other():
    c = _composer()
    assert c._build_capability_table(RoleInfo(), "expert") == ""


def test_build_expert_table_with_and_without_experts():
    data = {"roles": {"roles": {"a": {"tier": "expert", "expertise": ["x"]},
                                "b": {"tier": "large"}}}}
    out = _composer(data)._build_expert_table()
    assert "a" in out and "b" not in out
    assert _composer({"roles": {"roles": {}}})._build_expert_table() == ""


def test_build_supervisor_table_with_and_without():
    data = {"roles": {"roles": {"s": {"tier": "supervisor", "expertise": ["x"]},
                                "a": {"tier": "expert"}}}}
    out = _composer(data)._build_supervisor_table()
    assert "s" in out
    assert _composer({"roles": {"roles": {}}})._build_supervisor_table() == ""


# ── _build_tool_section / _build_values_section ─────────────────────────────

def test_build_tool_section():
    data = {"base": {"tool_rules": {"common": ["c"], "large": ["l"]}}}
    out = _composer(data)._build_tool_section(_req())
    assert "- c" in out and "- l" in out


def test_build_values_section_both():
    data = {"base": {"values": {"core": ["v1"], "behavior": ["b1"]}}}
    out = _composer(data)._build_values_section()
    assert "【核心价值观约束 - 必须遵守】" in out
    assert "【行为准则 - 必须遵循】" in out


def test_build_values_section_core_only():
    data = {"base": {"values": {"core": ["v1"]}}}
    out = _composer(data)._build_values_section()
    assert "【核心价值观约束" in out
    assert "【行为准则" not in out


def test_build_values_section_behavior_only():
    data = {"base": {"values": {"behavior": ["b1"]}}}
    out = _composer(data)._build_values_section()
    assert "【行为准则" in out
    assert "【核心价值观约束" not in out


def test_build_values_section_empty():
    assert _composer({"base": {}})._build_values_section() == ""


# ── reload ──────────────────────────────────────────────────────────────────

def test_reload():
    c = _composer()
    c.reload()
    c._loader.reload.assert_called_once()


def test_init_uses_global_loader(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr("config.prompts.loader.get_loader", lambda: fake)
    c = PromptComposer()
    assert c._loader is fake


def test_build_supervisor_table_merges_custom_agents(monkeypatch):
    """§79：编排页自定义 agent 出现在可委托主管列表（与 _resolve_role 动态回退一致）"""
    from config.settings import Settings
    monkeypatch.setattr(
        Settings, "get_custom_agents",
        lambda self: [{"role": "security_supervisor", "name": "安全主管", "tier": "supervisor", "expertise": ["安全审计"]}],
    )
    monkeypatch.setattr(Settings, "get_agent_active", lambda self, role: True)
    c = _composer({
        "roles": {"roles": {
            "code_supervisor": {"tier": "supervisor", "expertise": ["代码"]},
        }}
    })
    tbl = c._build_supervisor_table()
    assert "security_supervisor" in tbl
    assert "code_supervisor" in tbl


def test_build_tables_filter_disabled_agents(monkeypatch):
    """编排页关闭的主管/专家不进入可委托能力表"""
    from config.settings import Settings
    monkeypatch.setattr(Settings, "get_custom_agents", lambda self: [])
    monkeypatch.setattr(
        Settings, "get_agent_active",
        lambda self, role: role != "code_supervisor" and role != "data_analyzer",
    )
    c = _composer({
        "roles": {"roles": {
            "code_supervisor": {"tier": "supervisor", "expertise": ["代码"]},
            "design_supervisor": {"tier": "supervisor", "expertise": ["设计"]},
            "data_analyzer": {"tier": "expert", "expertise": ["数据分析"]},
            "ui_designer": {"tier": "expert", "expertise": ["UI"]},
        }}
    })
    sup = c._build_supervisor_table()
    assert "design_supervisor" in sup
    assert "code_supervisor" not in sup
    exp = c._build_expert_table()
    assert "ui_designer" in exp
    assert "data_analyzer" not in exp
