"""
管理系统抽象层 - 模块间交互接口
"""
from typing import Protocol


class ErrorReporterPort(Protocol):
    """错误报告接口 - 统一上报结构化错误"""

    def report(self, error: Exception, **kwargs) -> None:
        ...


def get_error_reporter() -> ErrorReporterPort:
    """工厂函数 - 获取统一错误报告器"""
    from .core.error_reporter import _reporter
    return _reporter
