"""
轻量模型客户端 - 使用 API 调用

用于执行轻量级任务：
- 情感分析
- 意图识别
- 关键词提取
- 文本分类

特点：极速响应、低资源占用、专注单一任务类型
使用 DashScope API (qwen-turbo) 替代本地 MLX 模型。
"""
from .base_model import BaseModelClient
from config.model_config import get_small_model_config
from config.settings import settings
from utils.logger import setup_logger
from modules.management import report_api_error, report_exception
import aiohttp
import asyncio
import json
import threading
from typing import Any

# CONC-6: Module-level lock for singleton initialization
_init_lock = threading.Lock()

logger = setup_logger("lite_model_client")


class LiteModelClient(BaseModelClient):
    """轻量模型客户端 - 使用 DashScope API

    专门处理轻量级 NLP 任务，不作为通用对话模型使用。

    默认配置:
        - model_name: qwen-turbo (快速、低成本)
        - max_tokens: 64
        - temperature: 0.1 (极低温度，保证输出稳定性)
    """

    # 全局单例
    _instance: 'LiteModelClient' = None

    def __init__(
        self,
        model_name: str = None,
        max_tokens: int = 64,
        temperature: float = 0.1,
        api_key: str = None,
        api_url: str = None,
        timeout: int = 15,
    ):
        key = api_key or settings.SMALL_MODEL_API_KEY or settings.LARGE_MODEL_API_KEY
        url = api_url or settings.SMALL_MODEL_API_URL or settings.LARGE_MODEL_API_URL
        super().__init__(key, url, timeout)
        self.model_name = model_name or settings.SMALL_MODEL_NAME
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.logger = setup_logger("lite_model_client")
        self._api_format = self.detect_api_format(self.api_url)

    @classmethod
    async def get_instance(cls) -> 'LiteModelClient':
        """获取全局单例实例（线程安全的初始化）"""
        # CONC-6: Use module-level threading.Lock for initialization
        # Avoids race condition in asyncio.Lock creation
        if cls._instance is None:
            with _init_lock:
                if cls._instance is None:
                    cls._instance = cls.from_config()
        return cls._instance

    @classmethod
    def from_config(cls) -> 'LiteModelClient':
        """从配置创建实例（线程安全）"""
        if cls._instance is not None:
            return cls._instance
        config = get_small_model_config()
        # 使用大模型的 API 配置（因为 lite 模型现在走 API，不再本地加载）
        from config.settings import settings
        return cls(
            model_name=settings.SMALL_MODEL_NAME,
            max_tokens=64,
            temperature=0.1,
            api_key=settings.SMALL_MODEL_API_KEY or settings.LARGE_MODEL_API_KEY,
            api_url=settings.SMALL_MODEL_API_URL or settings.LARGE_MODEL_API_URL,
            timeout=15,
        )

    async def generate(self, prompt: str, max_retries: int = 3, **kwargs) -> str:
        """生成响应 - 使用 DashScope / OpenAI / Anthropic API，带重试机制"""
        headers = self._build_headers(self._api_format)

        if self._api_format == "anthropic":
            payload = {
                "model": self.model_name,
                "max_tokens": kwargs.get("max_tokens", self.max_tokens),
                "system": "你是一个高效的分析助手，回答要简洁直接。",
                "messages": [{"role": "user", "content": prompt}],
            }
        else:
            payload = {
                "model": self.model_name,
                "messages": [
                    {"role": "system", "content": "你是一个高效的分析助手，回答要简洁直接。"},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": kwargs.get("max_tokens", self.max_tokens),
                "temperature": kwargs.get("temperature", self.temperature),
                "top_p": kwargs.get("top_p", 0.9),
            }

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                # RES-1: Reuse pooled session instead of creating new one per request
                session = await self._get_session()
                async with session.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        if self._api_format == "anthropic":
                            content_blocks = data.get("content", [])
                            texts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
                            return "\n".join(texts) if texts else ""
                        choices = data.get("choices", [])
                        if choices:
                            msg = choices[0].get("message", {})
                            content = msg.get("content", "")
                            # DeepSeek 推理模型：content 可能为空，实际内容在 reasoning_content
                            if not content and msg.get("reasoning_content"):
                                content = msg["reasoning_content"]
                            return content
                        return data.get("output", {}).get("text", "")
                    else:
                        # Non-200 status - use text() first to avoid ContentTypeError
                        error_text = await response.text()
                        try:
                            error_data = json.loads(error_text)
                        except (json.JSONDecodeError, ValueError):
                            error_data = error_text
                        report_api_error(
                            Exception(f"API request failed: {response.status} - {error_data}"),
                            module="infra.model.lite_model_client",
                            function="generate",
                            status_code=response.status,
                            request={"model": self.model_name, "api_url": self.api_url},
                            response=error_data,
                            source="model_api",
                        )
                        raise Exception(f"API request failed: {response.status} - {error_data}")
            except asyncio.TimeoutError as e:
                last_error = Exception(f"Lite model timeout (attempt {attempt}/{max_retries})")
                report_exception(
                    e,
                    module="infra.model.lite_model_client",
                    function="generate",
                    context={"api_url": self.api_url, "model": self.model_name},
                    source="model_api",
                )
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                break

        raise last_error or Exception("Lite model request failed after all retries")

    async def analyze_emotion(self, text: str) -> str:
        """情感分析"""
        prompt = f"分析情感倾向，只返回一个词（positive/negative/neutral）：\n{text}"
        result = await self.generate(prompt, max_tokens=10, temperature=0.1)
        result_lower = result.lower().strip()
        for keyword in ("positive", "negative", "neutral"):
            if keyword in result_lower:
                return keyword
        return "neutral"

    async def judge_intent(self, text: str) -> str:
        """意图识别"""
        prompt = f"判断意图，只返回一个词（query/command/chat/other）：\n{text}"
        result = await self.generate(prompt, max_tokens=10, temperature=0.1)
        result_lower = result.lower().strip()
        for keyword in ("query", "command", "chat", "other"):
            if keyword in result_lower:
                return keyword
        return "other"

    async def extract_keywords(self, text: str, top_k: int = 5) -> list:
        """关键词提取"""
        prompt = f"提取{top_k}个关键词，用逗号分隔：\n{text}"
        result = await self.generate(prompt, max_tokens=50, temperature=0.1)
        keywords = [k.strip() for k in result.split(",") if k.strip()]
        return keywords[:top_k]

    async def classify_text(self, text: str, categories: list) -> str:
        """文本分类"""
        cats = ", ".join(categories)
        prompt = f"将文本分类到以下类别之一，只返回类别名：\n类别：{cats}\n文本：{text}"
        result = await self.generate(prompt, max_tokens=20, temperature=0.1)
        result_stripped = result.strip()
        for cat in categories:
            if cat in result_stripped:
                return cat
        return categories[0] if categories else result_stripped

    async def detect_language(self, text: str) -> str:
        """语言检测"""
        prompt = f"检测语言，只返回语言代码（zh/en/ja/ko/other）：\n{text}"
        result = await self.generate(prompt, max_tokens=10, temperature=0.1)
        result_lower = result.lower().strip()
        for keyword in ("zh", "en", "ja", "ko"):
            if keyword in result_lower:
                return keyword
        return "other"

    async def close(self):
        """关闭客户端（清理单例）"""
        LiteModelClient._instance = None

    async def health_check(self) -> bool:
        """健康检查"""
        try:
            response = await self.generate("你好", max_tokens=5)
            return len(response) > 0
        except Exception as e:
            logger.warning(f"轻量模型健康检查失败: {e}")
            return False
