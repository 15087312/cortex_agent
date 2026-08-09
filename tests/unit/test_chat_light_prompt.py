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
