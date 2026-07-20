"""
Provider 适配层 - 封装不同模型 API 的差异

每个 provider 负责：
- 请求头构建（auth 方式）
- 请求体组装（格式转换）
- 响应解析（格式还原）
"""
from config.providers.base import ProviderBase
from config.providers.registry import ProviderRegistry, get_provider

__all__ = ["ProviderBase", "ProviderRegistry", "get_provider"]
