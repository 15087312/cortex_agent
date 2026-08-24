"""
Provider 注册表 — 按优先级解析适配器

解析顺序（由高到低）：
1. 供应商名（provider_name，查 catalog，最简配置，如 "deepseek"/"gemini"）
2. 显式格式名（api_format，如 "anthropic"/"azure"）
3. URL 推断（如 base_url 含 "dashscope" → DashScope）
4. 默认 OpenAI 兼容
"""
from typing import Optional, Type

from config.providers.base import ProviderBase, ProviderSpec
from config.providers.catalog import get_spec
from config.providers.openai import OpenAIProvider
from config.providers.anthropic import AnthropicProvider
from config.providers.dashscope import DashScopeProvider
from config.providers.gemini import GeminiProvider
from config.providers.azure import AzureProvider
from config.providers.bedrock import BedrockProvider
from config.providers.cohere import CohereProvider
from config.providers.ollama import OllamaProvider


#: 格式名 → Provider 类
_FORMAT_MAP: dict[str, Type[ProviderBase]] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "dashscope": DashScopeProvider,
    "gemini": GeminiProvider,
    "azure": AzureProvider,
    "bedrock": BedrockProvider,
    "cohere": CohereProvider,
    "ollama": OllamaProvider,
}

#: URL 特征 → Provider 类（URL 推断）
_URL_CHECKS: list[tuple] = [
    (lambda url: "generativelanguage" in (url or "").lower(), GeminiProvider),
    (lambda url: "dashscope" in (url or "").lower() or "modelscope" in (url or "").lower(), DashScopeProvider),
    (lambda url: "anthropic" in (url or "").lower() or "claude" in (url or "").lower(), AnthropicProvider),
    (lambda url: "bedrock-runtime" in (url or "").lower(), BedrockProvider),
    (lambda url: "openai.azure" in (url or "").lower() or "azure.openai" in (url or "").lower(), AzureProvider),
    (lambda url: "api.cohere.com" in (url or "").lower(), CohereProvider),
    (lambda url: ("localhost" in (url or "").lower() or "127.0.0.1" in (url or "")) and "11434" in (url or ""), OllamaProvider),
    (lambda url: "ollama" in (url or "").lower(), OllamaProvider),
]

_DEFAULT = OpenAIProvider


def _spec_format(spec: ProviderSpec) -> Type[ProviderBase]:
    """按 spec.api_format 取 Provider 类；未知格式回退 OpenAI"""
    return _FORMAT_MAP.get((spec.api_format or "").lower().strip()) or _DEFAULT


class ProviderRegistry:
    """根据供应商名 / 格式名 / URL 推断 API 格式，返回对应的 Provider"""

    def resolve_spec(self, provider_name: str = "") -> Optional[ProviderSpec]:
        """按供应商名解析目录条目（未命中返回 None）"""
        return get_spec(provider_name)

    def get_provider_class(self, base_url: str = "") -> Type[ProviderBase]:
        for check, provider_cls in _URL_CHECKS:
            if check(base_url):
                return provider_cls
        return _DEFAULT

    def get_provider_class_by_format(self, fmt: str) -> Optional[Type[ProviderBase]]:
        """按显式格式名取 Provider 类（空/未知回退 URL 推断）"""
        return _FORMAT_MAP.get((fmt or "").lower().strip())

    def get_provider_class_by_name(self, name: str) -> Optional[Type[ProviderBase]]:
        """按供应商名取 Provider 类（查目录）"""
        spec = get_spec(name)
        if not spec:
            return None
        return _FORMAT_MAP.get((spec.api_format or "").lower().strip()) or _DEFAULT

    def create(
        self,
        model_id: str,
        api_key: str,
        base_url: str = "",
        api_format: str = "",
        provider_name: str = "",
    ) -> ProviderBase:
        """构造 Provider 实例

        优先级: provider_name > api_format > URL 推断。
        未给 base_url/model_id 时，用供应商目录的默认值补齐。
        """
        spec = get_spec(provider_name) if provider_name else None
        if spec is not None:
            cls = _FORMAT_MAP.get((spec.api_format or "").lower().strip()) or _DEFAULT
            url = base_url or spec.base_url
            model = model_id or spec.default_model
            key = api_key
            if not key and spec.env_key:
                import os
                key = os.getenv(spec.env_key, "")
            return cls(api_key=key, base_url=url, model_name=model)

        cls = self.get_provider_class_by_format(api_format) or self.get_provider_class(base_url)
        return cls(api_key=api_key, base_url=base_url, model_name=model_id)


registry = ProviderRegistry()


def get_provider(
    model_id: str,
    api_key: str,
    base_url: str = "",
    api_format: str = "",
    provider_name: str = "",
) -> ProviderBase:
    return registry.create(model_id, api_key, base_url, api_format, provider_name)