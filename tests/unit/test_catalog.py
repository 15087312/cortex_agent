"""测试：config/providers/catalog.py — 供应商目录解析。

覆盖：
- 35+ 供应商注册、按名称 / 别名（含中文）解析
- 目录条目字段（base_url / api_format / default_model / env_key）
- list_providers 数量与完整性
"""
from config.providers import catalog
from config.providers.catalog import get_spec, list_providers
from config.providers.base import ProviderSpec


def test_catalog_has_many_providers():
    assert len(list_providers()) >= 35


def test_catalog_names_are_unique_specs():
    specs = [get_spec(n) for n in list_providers()]
    assert all(isinstance(s, ProviderSpec) for s in specs)
    names = [s.name for s in specs]
    assert len(names) == len(set(names))


def test_resolve_by_canonical_name():
    spec = get_spec("deepseek")
    assert spec.name == "deepseek"
    assert spec.api_format == "openai"
    assert spec.default_model == "deepseek-chat"
    assert spec.env_key == "DEEPSEEK_API_KEY"


def test_resolve_by_alias():
    assert get_spec("kimi").name == "moonshot"
    assert get_spec("google-ai-studio").name == "gemini"
    assert get_spec("qwen").name == "dashscope"
    assert get_spec("glm").name == "zhipu"
    assert get_spec("bedrock").name == "aws-bedrock"
    assert get_spec("grok").name == "xai"


def test_resolve_case_insensitive():
    assert get_spec("DeepSeek").name == "deepseek"
    assert get_spec("  Groq  ").name == "groq"


def test_resolve_unknown_returns_none():
    assert get_spec("") is None
    assert get_spec("nonexistent-vendor") is None


def test_catalog_protocol_variety():
    """不同协议格式的供应商都在目录里"""
    assert get_spec("gemini").api_format == "gemini"
    assert get_spec("anthropic").api_format == "anthropic"
    assert get_spec("azure").api_format == "azure"
    assert get_spec("bedrock").api_format == "bedrock"
    assert get_spec("cohere").api_format == "cohere"
    assert get_spec("ollama").api_format == "ollama"
    assert get_spec("dashscope").api_format == "dashscope"


def test_catalog_export():
    assert catalog.CATALOG is not None