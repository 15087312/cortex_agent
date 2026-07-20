"""
统一错误报告模块。

提供：
- ErrorReport: 结构化错误报告数据
- report_error: 统一错误上报入口
- report_exception: 异常对象快捷上报
- report_api_error: HTTP/API 错误快捷上报

默认只做本地结构化日志和 error_bus 转发，不引入外部依赖。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional
import traceback

from utils.logger import setup_logger

logger = setup_logger("error_reporter")


@dataclass
class ErrorReport:
    timestamp: str
    source: str
    module: str
    function: str
    error_type: str
    message: str
    context: Dict[str, Any] = field(default_factory=dict)
    stack: str = ""
    severity: str = "ERROR"
    code: str = ""


class ErrorReporter:
    def __init__(self):
        self.logger = logger

    def report(self, report: ErrorReport) -> None:
        payload = asdict(report)
        try:
            self.logger.error("[ERROR_REPORT] %s", payload)
        except Exception as e:
            logger.warning(f"错误报告结构化序列化失败: {e}")
            try:
                self.logger.error("[ERROR_REPORT] %s", str(payload))
            except Exception as e:
                logger.debug("错误报告字符串序列化也失败: %s", e)

        try:
            from modules.management.core.error_bus import error_bus, ErrorContext
            if getattr(report, "source", "") != "error_bus":
                error_bus.report_error(
                    Exception(f"{report.error_type}: {report.message}"),
                    ErrorContext(
                        module=report.module,
                        function=report.function,
                        extra={
                            **(report.context or {}),
                            "source": report.source,
                            "severity": report.severity,
                            "code": report.code,
                        },
                    ),
                )
        except Exception as e:
            logger.debug("错误总线转发失败 (非致命): %s", e)


_reporter = ErrorReporter()


def _build_report(
    error: Exception,
    *,
    source: str,
    module: str,
    function: str,
    context: Optional[Dict[str, Any]] = None,
    severity: str = "ERROR",
    code: str = "",
) -> ErrorReport:
    return ErrorReport(
        timestamp=datetime.now().isoformat(),
        source=source,
        module=module,
        function=function,
        error_type=type(error).__name__,
        message=str(error),
        context=context or {},
        stack=traceback.format_exc(),
        severity=severity,
        code=code,
    )


def report_error(
    error: Exception,
    *,
    source: str,
    module: str,
    function: str,
    context: Optional[Dict[str, Any]] = None,
    severity: str = "ERROR",
    code: str = "",
) -> None:
    _reporter.report(
        _build_report(
            error,
            source=source,
            module=module,
            function=function,
            context=context,
            severity=severity,
            code=code,
        )
    )


def report_exception(
    error: Exception,
    module: str,
    function: str,
    context: Optional[Dict[str, Any]] = None,
    source: str = "exception",
    severity: str = "ERROR",
    code: str = "",
) -> None:
    report_error(
        error,
        source=source,
        module=module,
        function=function,
        context=context,
        severity=severity,
        code=code,
    )


def report_api_error(
    error: Exception,
    module: str,
    function: str,
    status_code: Optional[int] = None,
    request: Optional[Dict[str, Any]] = None,
    response: Optional[Dict[str, Any]] = None,
    source: str = "api",
) -> None:
    context: Dict[str, Any] = {}
    if status_code is not None:
        context["status_code"] = status_code
    if request is not None:
        context["request"] = request
    if response is not None:
        context["response"] = response
    report_error(
        error,
        source=source,
        module=module,
        function=function,
        context=context,
        severity="ERROR",
        code=str(status_code) if status_code is not None else "",
    )
