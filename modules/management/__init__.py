"""
管理系统 - 模块调度、全局监控、自适应优化
"""
from .core.collector import ModuleRegistry, StatusCollector
from .core.error_bus import error_bus, ErrorContext
from .core.error_reporter import (
    ErrorReport,
    ErrorReporter,
    report_api_error,
    report_error,
    report_exception,
)

__all__ = [
    "error_bus",
    "ErrorContext",
    "ErrorReport",
    "ErrorReporter",
    "report_error",
    "report_exception",
    "report_api_error",
    "ModuleRegistry",
    "StatusCollector",
]
