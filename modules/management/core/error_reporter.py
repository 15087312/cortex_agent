"""统一错误报告模块（兼容层）。

实现已下沉到 utils/error_reporter.py，本文件仅保留旧路径 re-export，
避免破坏既有 import（modules.management.core.error_reporter.xxx）。
"""
from __future__ import annotations

from utils.error_reporter import (
    ErrorReporter,
    ErrorReport,
    _reporter,
    report_api_error,
    report_error,
    report_exception,
)

__all__ = [
    "ErrorReporter",
    "ErrorReport",
    "_reporter",
    "report_api_error",
    "report_error",
    "report_exception",
]
