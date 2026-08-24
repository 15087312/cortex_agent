"""
Azure OpenAI 适配器

请求/响应结构与 OpenAI 一致，但认证用 api-key 头，且端点必须携带 api-version
查询参数。base_url 通常为部署端点（可含 /chat/completions）。
"""
from config.providers.openai import OpenAIProvider


class AzureProvider(OpenAIProvider):
    """Azure OpenAI（api-key 头 + 部署路径 + api-version）"""

    format_name = "azure"
    #: Azure 默认 api-version（可经 base_url 或子类覆盖）
    api_version: str = "2024-06-01"

    def build_headers(self) -> dict:
        return {
            "api-key": self.api_key,
            "Content-Type": "application/json",
        }

    def chat_url(self) -> str:
        base = self.base_url.rstrip("/")
        # 已在 /chat/completions 结尾 → 直接补 api-version
        if base.endswith("/chat/completions"):
            return f"{base}?api-version={self.api_version}"
        # 已是完整端点（可能带 api-version 等查询）→ 原样返回
        if "?" in base:
            return base
        # 根端点 → 拼接
        return f"{base}/chat/completions?api-version={self.api_version}"