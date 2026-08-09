"""模型身份测试：from_template 应用 model_params 覆盖"""
from config.settings import settings
from modules.thinking.identity import ModelIdentity


def test_model_params_override():
    settings.set_model_params("code_writer", {"temperature": 0.5, "max_tokens": 3000})
    try:
        ident = ModelIdentity.from_template("code_writer")
        assert ident.temperature == 0.5
        assert ident.max_tokens == 3000
    finally:
        settings.set_model_params("code_writer", {})


def test_model_params_cleared_uses_default():
    settings.set_model_params("code_writer", {})
    ident = ModelIdentity.from_template("code_writer")
    # 默认值（identity 模板未配 temperature 时回退 dataclass 默认 0.2）
    assert isinstance(ident.temperature, float)


def test_tool_whitelist_from_template():
    ident = ModelIdentity.from_template("orchestrator")
    assert "todo" in ident.tool_whitelist  # 所有模型白名单含 todo
    sup = ModelIdentity.from_template("code_supervisor")
    assert "todo" in sup.tool_whitelist
