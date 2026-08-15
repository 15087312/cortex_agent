"""chat_light/prompt_composer 测试（此前 33% 覆盖）：系统提示词组装"""
from modules.thinking.chat_light import prompt_composer as pc

S = type(pc.settings)


def _composer():
    c = pc.PromptComposer.__new__(pc.PromptComposer)
    c._identity = "你是{assistant_name}，用户叫{user_name}。"
    return c


def _patch(monkeypatch, override="", persona=""):
    monkeypatch.setattr(S, "get_system_override", lambda self, role: override)
    monkeypatch.setattr(S, "get_persona", lambda self, role: persona)
    # 确定化：mock get_agent_active，避免被用户真实 personas.yaml 的编排状态影响
    monkeypatch.setattr(S, "get_agent_active", lambda self, role: True)
    monkeypatch.setattr(pc.settings, "ASSISTANT_NAME", "助手")
    monkeypatch.setattr(pc.settings, "USER_NAME", "用户")


def test_build_system_persona_override(monkeypatch):
    c = _composer()
    _patch(monkeypatch, override="完全自定义")
    assert c.build_system("记忆") == "完全自定义"


def test_build_system_default_identity(monkeypatch):
    c = _composer()
    c._build_perception_section = lambda: "【被动感知系统】\n感知说明"
    _patch(monkeypatch)
    out = c.build_system("记忆内容")
    assert "你是助手" in out
    assert "感知系统" in out
    assert "记忆内容" in out


def test_build_system_identity_format_failure(monkeypatch):
    c = pc.PromptComposer.__new__(pc.PromptComposer)
    c._identity = "含{花括号}的{设定}"
    c._build_perception_section = lambda: ""
    _patch(monkeypatch)
    out = c.build_system("")
    assert "花括号" in out  # format 失败回退原样


def test_build_perception_section():
    c = pc.PromptComposer.__new__(pc.PromptComposer)
    assert isinstance(c._build_perception_section(), str)


def test_prompt_composer_real_init_loads_identity():
    """PromptComposer 真实构造：_load_prompts 真实读取 base.yaml"""
    from modules.thinking.chat_light.prompt_composer import PromptComposer
    c = PromptComposer()  # 真实 __init__（不 mock）
    assert isinstance(c._identity, str)


# ── 纯对话人设回退：orchestrator 无自定义时用 large-tier 自定义 agent ──────

def test_build_system_falls_back_to_large_custom_agent(monkeypatch):
    """orchestrator 无人设 → 回退到自定义 large-tier 总指挥 agent 的人设（修复设置无效）"""
    from config.settings import settings
    from modules.thinking.chat_light.prompt_composer import PromptComposer

    def fake_get_persona(self, role):
        if role == "custom_boss":
            return "【自定义总指挥】我是自定义人格。"
        return ""

    def fake_get_custom_agents(self):
        return [{"role": "custom_boss", "name": "自定义总指挥", "tier": "large"}]

    monkeypatch.setattr(type(settings), "get_persona", fake_get_persona)
    monkeypatch.setattr(type(settings), "get_custom_agents", fake_get_custom_agents)
    monkeypatch.setattr(type(settings), "get_system_override", lambda self, role: "")
    # 确定化：全部激活，不依赖真实 personas.yaml 的编排状态
    monkeypatch.setattr(type(settings), "get_agent_active", lambda self, role: True)

    sp = PromptComposer().build_system("")
    assert "【自定义总指挥】" in sp


def test_build_system_orchestrator_persona_priority(monkeypatch):
    """orchestrator 有自定义人设时优先，不回退"""
    from config.settings import settings
    from modules.thinking.chat_light.prompt_composer import PromptComposer

    def fake_get_persona(self, role):
        if role == "orchestrator":
            return "【总指挥人设】官方总指挥。"
        if role == "custom_boss":
            return "【自定义】不该被用。"
        return ""

    def fake_get_custom_agents(self):
        return [{"role": "custom_boss", "name": "x", "tier": "large"}]

    monkeypatch.setattr(type(settings), "get_persona", fake_get_persona)
    monkeypatch.setattr(type(settings), "get_custom_agents", fake_get_custom_agents)
    monkeypatch.setattr(type(settings), "get_system_override", lambda self, role: "")
    # 确定化：orchestrator 激活，否则依赖真实 personas.yaml 编排状态会 flaky
    monkeypatch.setattr(type(settings), "get_agent_active", lambda self, role: True)

    sp = PromptComposer().build_system("")
    assert "【总指挥人设】" in sp
    assert "【自定义】" not in sp
