"""
Ollama 本地适配器

Ollama 同时提供原生 /api/chat 与 OpenAI 兼容 /v1/chat/completions。
本适配器走 OpenAI 兼容端点（无认证），保证与上层 OpenAI 协议完全一致。
"""
from config.providers.openai import OpenAIProvider


class OllamaProvider(OpenAIProvider):
    """本地 Ollama（OpenAI 兼容 /v1 端点，无认证）"""

    format_name = "ollama"

    def build_headers(self) -> dict:
        # Ollama 本地端点无需认证
        return {"Content-Type": "application/json"}

    def chat_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"