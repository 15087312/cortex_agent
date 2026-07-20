"""
Provider 注册表 — 根据 base_url 推断 API 格式选择适配器
"""
from typing import Type
from config.providers.base import ProviderBase
from config.providers.openai import OpenAIProvider
from config.providers.anthropic import AnthropicProvider
from config.providers.dashscope import DashScopeProvider


_URL_CHECKS = [
    (lambda url: "dashscope" in (url or "").lower(), DashScopeProvider),
    (lambda url: "modelscope" in (url or "").lower(), DashScopeProvider),
    (lambda url: "anthropic" in (url or "").lower() or "claude" in (url or "").lower(), AnthropicProvider),
]

_DEFAULT = OpenAIProvider


class ProviderRegistry:
    """根据 base_url 推断 API 格式，返回对应的 Provider"""

    def get_provider_class(self, base_url: str = "") -> Type[ProviderBase]:
        for check, provider_cls in _URL_CHECKS:
            if check(base_url):
                return provider_cls
        return _DEFAULT

    def create(self, model_id: str, api_key: str, base_url: str = "") -> ProviderBase:
        cls = self.get_provider_class(base_url)
        return cls(api_key=api_key, base_url=base_url, model_name=model_id)


registry = ProviderRegistry()


def get_provider(model_id: str, api_key: str, base_url: str = "") -> ProviderBase:
    return registry.create(model_id, api_key, base_url)
