"""
Provider 适配层 - 封装不同模型 API 的差异

每个 provider 负责：
- 请求头构建（auth 方式）
- 请求体组装（格式转换）
- 响应解析（格式还原）
"""
from config.providers.base import ProviderBase, ProviderSpec
from config.providers.registry import ProviderRegistry, get_provider
from config.providers.catalog import get_spec, list_providers
from config.providers.openai import OpenAIProvider
from config.providers.anthropic import AnthropicProvider
from config.providers.dashscope import DashScopeProvider
from config.providers.gemini import GeminiProvider
from config.providers.azure import AzureProvider
from config.providers.bedrock import BedrockProvider
from config.providers.cohere import CohereProvider
from config.providers.ollama import OllamaProvider

__all__ = [
    "ProviderBase", "ProviderSpec", "ProviderRegistry", "get_provider",
    "get_spec", "list_providers",
    "OpenAIProvider", "AnthropicProvider", "DashScopeProvider",
    "GeminiProvider", "AzureProvider", "BedrockProvider", "CohereProvider",
    "OllamaProvider",
]
