"""
模型配置 - 大/小模型的 API 地址、参数、限流策略
"""
from pydantic import BaseModel
from typing import Optional, Dict, Any, List


class ModelConfig(BaseModel):
    """模型配置基类"""
    api_key: str
    api_url: str
    timeout: int = 120
    max_retries: int = 3
    rate_limit: int = 100  # 每分钟请求数限制


class LargeModelConfig(ModelConfig):
    """大模型配置（DeepSeek-V4-Flash）"""
    model_name: str = "deepseek-v4-flash"
    max_tokens: int = 4096
    temperature: float = 0.7
    top_p: float = 0.9
    api_format: str = ""  # "dashscope" / "openai" / 留空自动检测

    # 思维链配置
    enable_chain_of_thought: bool = True
    max_thought_steps: int = 10


class SmallModelConfig(ModelConfig):
    """小模型配置（DeepSeek-V4-Flash）"""
    model_name: str = "deepseek-v4-flash"
    max_tokens: int = 512
    temperature: float = 0.3
    top_p: float = 0.9


class ModelPoolConfig(BaseModel):
    """模型池配置"""
    min_instances: int = 1
    max_instances: int = 5
    health_check_interval: int = 30  # 秒
    load_balance_strategy: str = "round_robin"  # round_robin, least_connections, weighted


def get_large_model_config() -> LargeModelConfig:
    """获取大模型配置（API 调用 DeepSeek-V4-Flash）"""
    from config.settings import settings
    return LargeModelConfig(
        api_key=settings.LARGE_MODEL_API_KEY,
        api_url=settings.LARGE_MODEL_API_URL,
        model_name=settings.LARGE_MODEL_NAME,
        temperature=0.7,
        timeout=settings.MODEL_TIMEOUT,
        api_format=settings.LARGE_MODEL_API_FORMAT,
    )


def get_small_model_config() -> SmallModelConfig:
    """获取小模型配置（DeepSeek API 调用）"""
    from config.settings import settings
    return SmallModelConfig(
        api_key=settings.SMALL_MODEL_API_KEY or settings.LARGE_MODEL_API_KEY,
        api_url=settings.SMALL_MODEL_API_URL or settings.LARGE_MODEL_API_URL,
        model_name=settings.SMALL_MODEL_NAME,
        max_tokens=512,
        temperature=0.3,
        timeout=settings.MODEL_TIMEOUT,
    )


class MediumModelConfig(ModelConfig):
    """中模型配置（DeepSeek 32B 级）"""
    model_name: str = "deepseek-v4-flash"
    max_tokens: int = 1024
    temperature: float = 0.1


def get_medium_model_config() -> MediumModelConfig:
    """获取中模型配置（DeepSeek API 调用）"""
    from config.settings import settings
    return MediumModelConfig(
        api_key=settings.MEDIUM_MODEL_API_KEY or settings.LARGE_MODEL_API_KEY,
        api_url=settings.MEDIUM_MODEL_API_URL,
        model_name=settings.MEDIUM_MODEL_NAME,
        max_tokens=1024,
        temperature=0.1,
        timeout=settings.MODEL_TIMEOUT,
    )



