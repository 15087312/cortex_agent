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

# 显式格式名 → Provider 类（尊重客户端配置的 _api_format；URL 推断见 get_provider_class）
_FORMAT_MAP = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "dashscope": DashScopeProvider,
}


class ProviderRegistry:
    """根据 base_url 推断 API 格式，返回对应的 Provider"""

    def get_provider_class(self, base_url: str = "") -> Type[ProviderBase]:
        for check, provider_cls in _URL_CHECKS:
            if check(base_url):
                return provider_cls
        return _DEFAULT

    def get_provider_class_by_format(self, fmt: str) -> Type[ProviderBase]:
        """按显式格式名取 Provider 类（空/未知回退 URL 推断）"""
        return _FORMAT_MAP.get((fmt or "").lower().strip())

    def create(
        self,
        model_id: str,
        api_key: str,
        base_url: str = "",
        api_format: str = "",
    ) -> ProviderBase:
        cls = self.get_provider_class_by_format(api_format) or self.get_provider_class(base_url)
        return cls(api_key=api_key, base_url=base_url, model_name=model_id)


registry = ProviderRegistry()


def get_provider(
    model_id: str,
    api_key: str,
    base_url: str = "",
    api_format: str = "",
) -> ProviderBase:
    return registry.create(model_id, api_key, base_url, api_format)
