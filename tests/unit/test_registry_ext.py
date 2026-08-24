"""测试：config/providers/registry.py — 供应商名 / 格式名 / URL 三级解析。

覆盖：
- 供应商名解析（deepseek/gemini/azure/cohere/ollama/bedrock）
- 供应商目录缺省补齐（URL / 模型名 / 环境变量密钥）
- 显式格式优先于 URL；URL 推断优先于默认 OpenAI
"""
from config.providers.registry import ProviderRegistry, get_provider
from config.providers.openai import OpenAIProvider
from config.providers.anthropic import AnthropicProvider
from config.providers.gemini import GeminiProvider
from config.providers.azure import AzureProvider
from config.providers.bedrock import BedrockProvider
from config.providers.cohere import CohereProvider
from config.providers.ollama import OllamaProvider

_registry = ProviderRegistry()


def test_registry_url_detects_gemini():
    assert _registry.get_provider_class("https://generativelanguage.googleapis.com/v1beta") is GeminiProvider


def test_registry_url_detects_azure():
    assert _registry.get_provider_class("https://my-resource.openai.azure.com") is AzureProvider
    assert _registry.get_provider_class("https://azure.openai.somewhere.com/v1") is AzureProvider


def test_registry_url_detects_bedrock():
    assert _registry.get_provider_class("https://bedrock-runtime.us-east-1.amazonaws.com") is BedrockProvider


def test_registry_url_detects_cohere():
    assert _registry.get_provider_class("https://api.cohere.com/v2") is CohereProvider


def test_registry_url_detects_ollama():
    assert _registry.get_provider_class("http://localhost:11434") is OllamaProvider
    assert _registry.get_provider_class("http://127.0.0.1:11434") is OllamaProvider


def test_registry_name_resolution():
    assert _registry.get_provider_class_by_name("gemini") is GeminiProvider
    assert _registry.get_provider_class_by_name("openrouter") is OpenAIProvider
    assert _registry.get_provider_class_by_name("kimi") is OpenAIProvider
    assert _registry.get_provider_class_by_name("unknown") is None


def test_get_provider_by_name_fills_defaults():
    p = get_provider("", "", "", "", provider_name="deepseek")
    assert isinstance(p, OpenAIProvider)
    assert p.base_url == "https://api.deepseek.com/v1"
    assert p.model_name == "deepseek-chat"


def test_get_provider_by_name_explicit_overrides():
    p = get_provider("my-model", "my-key", "https://custom.example/v1", "", provider_name="deepseek")
    assert p.base_url == "https://custom.example/v1"
    assert p.model_name == "my-model"
    assert p.api_key == "my-key"


def test_get_provider_explicit_format_still_works():
    p = get_provider("m", "k", "https://api.openai.com/v1", api_format="anthropic")
    assert isinstance(p, AnthropicProvider)


def test_get_provider_url_inference_works():
    p = get_provider("m", "k", "https://api.anthropic.com/v1")
    assert isinstance(p, AnthropicProvider)


def test_get_provider_default_openai():
    p = get_provider("m", "k", "")
    assert isinstance(p, OpenAIProvider)