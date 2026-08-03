"""
全局错误总线 — 系统级异常捕获 + 统一输出

职责:
  1. sys.excepthook / threading.excepthook / asyncio handler
  2. 主动上报入口: report_error(error, context)
  3. 写日志 + 推送到 WebSocket（可选）

设计原则:
  - 单向依赖: ErrorBus → logger / WS, 不反向依赖其他模块
  - 不重复: 每条错误只在 ErrorBus 写一次日志
  - ErrorReporter 已合并至此模块
"""

import sys
import traceback
import threading
import asyncio
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass
from datetime import datetime
import logging

from utils.logger import setup_logger

_logger = None


def _setup_global_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    _logger = setup_logger("error_bus")
    return _logger


@dataclass
class ErrorContext:
    """错误上下文信息"""
    module: str
    function: str
    extra: Optional[Dict[str, Any]] = None


@dataclass
class ErrorReport:
    """结构化错误报告"""
    timestamp: str
    source: str
    module: str
    function: str
    error_type: str
    message: str
    context: Dict[str, Any]
    stack: str
    severity: str = "ERROR"


class GlobalErrorBus:
    """全局错误总线"""

    def __init__(self):
        self.logger = _setup_global_logger()
        self._ws_callback: Optional[Callable[[str, str, dict], None]] = None
        self._init_hooks()

    def set_ws_callback(self, callback: Callable[[str, str, dict], None]) -> None:
        self._ws_callback = callback

    def _push_to_ws(self, error_type: str, error_msg: str, ctx: dict) -> None:
        if self._ws_callback:
            try:
                self._ws_callback(error_type, error_msg, ctx)
            except Exception as e:
                self.logger.error("推送错误到 TUI 失败: %s", e)

    def _init_hooks(self):
        sys.excepthook = self._handle_uncaught_exception
        threading.excepthook = self._handle_thread_exception
        self._original_loop_exception_handler = None

    def setup_asyncio_handler(self, loop: asyncio.AbstractEventLoop):
        self._original_loop_exception_handler = loop.get_exception_handler()
        loop.set_exception_handler(self._handle_asyncio_exception)

    def restore_asyncio_handler(self, loop: asyncio.AbstractEventLoop):
        if self._original_loop_exception_handler is not None:
            loop.set_exception_handler(self._original_loop_exception_handler)

    def report_error(self, error: Exception, context: ErrorContext = None):
        """统一错误上报入口 — 写日志 + 推 WS，不再转发到其它模块"""
        report = self._build_report(error, context)
        self.logger.error("[ERROR] %s %s: %s | %s",
                          report.module, report.function, report.error_type, report.message)
        if report.stack:
            self.logger.debug("Stack trace:\n%s", report.stack)

        ctx_dict = context.extra if context and context.extra else {}
        ctx_dict["module"] = context.module if context else ""
        ctx_dict["function"] = context.function if context else ""
        self._push_to_ws(type(error).__name__, str(error), ctx_dict)

    def _build_report(self, error: Exception, context: ErrorContext = None) -> ErrorReport:
        return ErrorReport(
            timestamp=datetime.now().isoformat(),
            source="error_bus",
            module=context.module if context else "unknown",
            function=context.function if context else "unknown",
            error_type=type(error).__name__,
            message=str(error),
            context=context.extra if context and context.extra else {},
            stack=traceback.format_exc(),
        )

    def _format_error(self, error: Exception, context: ErrorContext = None) -> str:
        error_type = type(error).__name__
        error_msg = str(error)
        stack_trace = traceback.format_exc()
        context_str = ""
        if context:
            context_str = f"\n模块: {context.module}\n函数: {context.function}"
            if context.extra:
                context_str += f"\n上下文: {context.extra}"
        return f"""
========================================
错误类型: {error_type}
错误信息: {error_msg}{context_str}
堆栈跟踪:
{stack_trace}
========================================
"""

    def _handle_uncaught_exception(self, exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        error = exc_value.with_traceback(exc_traceback)
        self.report_error(error, ErrorContext(module="main", function="main_thread"))

    def _handle_thread_exception(self, args: threading.ExceptHookArgs):
        self.report_error(
            args.exc_value,
            ErrorContext(
                module="thread",
                function=args.thread.name if args.thread else "unknown_thread",
                extra={"thread_id": args.thread.ident if args.thread else None}
            )
        )

    def _handle_asyncio_exception(self, loop: asyncio.AbstractEventLoop, context: Dict):
        exception = context.get("exception")
        if exception:
            self.report_error(
                exception,
                ErrorContext(
                    module="asyncio",
                    function=context.get("task", "unknown_task").get_name() if context.get("task") else "unknown",
                    extra={"message": context.get("message")}
                )
            )
        else:
            self.logger.error(f"Asyncio error without exception: {context}")


import threading as _threading

_error_bus = None
_error_bus_lock = _threading.Lock()


def get_error_bus() -> GlobalErrorBus:
    global _error_bus
    if _error_bus is None:
        with _error_bus_lock:
            if _error_bus is None:
                _error_bus = GlobalErrorBus()
    return _error_bus


error_bus = get_error_bus()

__all__ = ["error_bus", "get_error_bus", "ErrorContext", "ErrorReport"]