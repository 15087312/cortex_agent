"""
Provider 基类 — 定义模型 API 适配器接口
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional


@dataclass
class ProviderSpec:
    """供应商目录条目（opencode 风格）

    描述一个供应商如何接入：默认端点、协议格式、默认模型、认证方式等。
    用户只需按 name 配置，即可自动获得 base_url / 格式 / 默认模型。
    """

    name: str
    base_url: str = ""
    api_format: str = "openai"
    default_model: str = ""
    env_key: str = ""
    auth_header: str = "Authorization"  # 认证头名（Provider 内可覆盖）
    auth_prefix: str = "Bearer "  # 认证值前缀
    openai_compatible: bool = True  # 是否支持 OpenAI /chat/completions 格式
    supports_tools: bool = True
    supports_streaming: bool = True
    supports_reasoning: bool = False  # 是否支持推理内容（reasoning_content）
    doc: str = ""
    aliases: List[str] = field(default_factory=list)


class ProviderBase(ABC):
    """模型 API 适配器基类

    每个子类封装一种 API 格式的差异：
    - 请求头（auth 方式）
    - 请求体组装
    - 响应解析
    """

    #: 适配器对应的协议格式名（如 openai / anthropic / gemini / azure / bedrock / cohere / ollama / dashscope）
    format_name: str = "openai"

    def __init__(self, api_key: str, base_url: str, model_name: str):
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name

    @abstractmethod
    def build_headers(self) -> Dict[str, str]:
        """构建请求头"""

    @abstractmethod
    def build_request(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int,
        temperature: float,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[Any] = None,
        stream: bool = False,
        top_p: Optional[float] = None,
        reasoning_effort: str = "",  # 推理强度（DeepSeek 专用，其他 Provider 忽略）
    ) -> Dict[str, Any]:
        """构建请求体"""

    @abstractmethod
    def parse_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """解析响应，返回标准化格式 {content, tool_calls, finish_reason, usage}"""

    @abstractmethod
    def chat_url(self) -> str:
        """返回 chat completions 端点 URL"""

    @abstractmethod
    def parse_stream_line(self, line: str) -> Optional[Dict[str, Any]]:
        """解析 SSE 流的一行，返回增量 delta 或 None"""
