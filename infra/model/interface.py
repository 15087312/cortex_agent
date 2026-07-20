"""
模型系统抽象层 - 模块间交互接口

modules 层应通过 BaseModelClient ABC 和工厂函数来使用模型，而不是直接导入具体实现。
"""
from typing import Protocol, Optional, Dict, Any, AsyncGenerator
from infra.model.base_model import BaseModelClient, ChatMessage, ChatResponse


__all__ = ["BaseModelClient", "ChatMessage", "ChatResponse"]
