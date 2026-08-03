"""
异常层基类 + 安全调用工具

分层:
  BackendError         — 项目所有异常的基类
    ├── ModelError     — LLM 调用相关
    ├── ToolError      — 工具执行相关
    ├── SecurityError  — 安全拦截/审查
    ├── MemoryError    — 记忆/存储
    ├── CommunicationError  — WebSocket/HTTP 通信
    └── ConfigError    — 配置错误

用法:
    raise ModelError("API 返回空", model="gpt-4", cause=err)
    result = safe_call(fn, fallback=[], logger=log)
"""

import logging
from typing import Any, Awaitable, Callable, Optional, TypeVar

T = TypeVar("T")


class BackendError(Exception):
    """项目异常基类。"""
    def __init__(self, message: str = "", *, cause: Optional[Exception] = None, **context):
        super().__init__(message)
        self.message = message
        self.cause = cause
        self.context = context


class ModelError(BackendError):
    """模型调用异常（超时、空响应、连接失败等）。"""


class ToolError(BackendError):
    """工具执行异常。"""


class SecurityError(BackendError):
    """安全审查/拦截异常。"""


class MemoryError(BackendError):
    """记忆/存储系统异常。"""


class CommunicationError(BackendError):
    """网络通信异常（WebSocket 断连、HTTP 失败等）。"""


class ConfigError(BackendError):
    """配置错误。"""


# ── 安全调用工具 ──

def safe_call(
    fn: Callable[[], T],
    fallback: Any = None,
    logger: Optional[logging.Logger] = None,
    level: int = logging.WARNING,
    msg: str = "",
) -> T:
    try:
        return fn()
    except Exception as e:
        (logger or logging.getLogger("safe")).log(level, "%s: %s", msg or getattr(fn, "__name__", "?"), e)
        return fallback


async def safe_acall(
    awaitable: Awaitable[T],
    fallback: Any = None,
    logger: Optional[logging.Logger] = None,
    level: int = logging.WARNING,
    msg: str = "",
) -> T:
    try:
        return await awaitable
    except Exception as e:
        (logger or logging.getLogger("safe")).log(level, "%s: %s", msg, e)
        return fallback
