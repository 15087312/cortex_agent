"""
全局错误总线 - 捕获所有未处理的错误并统一输出到终端

放置在管理模块中，作为系统级错误收集服务。
其他模块通过导入此模块来报告不可恢复的错误。

使用方式：
    在项目入口处（如main.py或api/main.py）导入并初始化：
        from modules.management.core.error_bus import error_bus, ErrorContext
        import asyncio
        
        # 设置asyncio异常处理器（在有事件循环时调用）
        loop = asyncio.get_running_loop()
        error_bus.setup_asyncio_handler(loop)
        
        # 然后启动你的应用
        # ...

    在业务代码中使用：
        from modules.management.core.error_bus import error_bus, ErrorContext
        
        def my_function():
            try:
                # 业务逻辑
                pass
            except Exception as e:
                # 上报无法处理的错误
                error_bus.report_error(
                    e,
                    ErrorContext(
                        module="my_module",
                        function="my_function",
                        extra={"param1": value1}
                    )
                )
                # 重新抛出或根据情况处理
                raise
"""

import sys
import traceback
import threading
import asyncio
from typing import Dict, Any, Optional
from dataclasses import dataclass
import logging

# 全局错误总线 logger
_logger = None


def _setup_global_logger() -> logging.Logger:
    """设置错误总线日志器（使用统一日志格式）"""
    global _logger
    if _logger is not None:
        return _logger
    # 使用统一的 setup_logger，不直接操作根 logger
    from utils.logger import setup_logger
    _logger = setup_logger("error_bus")
    return _logger


@dataclass
class ErrorContext:
    """错误上下文信息"""
    module: str
    function: str
    extra: Optional[Dict[str, Any]] = None


class GlobalErrorBus:
    """全局错误总线"""

    def __init__(self):
        self.logger = _setup_global_logger()
        self._init_hooks()
    
    def _init_hooks(self):
        """初始化所有全局错误钩子"""
        # 1. 捕获主线程未处理的同步异常
        sys.excepthook = self._handle_uncaught_exception
        
        # 2. 捕获所有子线程未处理的异常
        threading.excepthook = self._handle_thread_exception
        
        # 3. asyncio处理器需要在有事件循环时设置
        self._original_loop_exception_handler = None
    
    def setup_asyncio_handler(self, loop: asyncio.AbstractEventLoop):
        """设置asyncio事件循环的异常处理器"""
        self._original_loop_exception_handler = loop.get_exception_handler()
        loop.set_exception_handler(self._handle_asyncio_exception)
    
    def restore_asyncio_handler(self, loop: asyncio.AbstractEventLoop):
        """恢复原始的asyncio异常处理器"""
        if self._original_loop_exception_handler is not None:
            loop.set_exception_handler(self._original_loop_exception_handler)
    
    def report_error(self, error: Exception, context: ErrorContext = None):
        """
        业务层主动上报错误的统一入口
        所有模块捕获到不能处理的错误时，都调用这个方法
        """
        error_info = self._format_error(error, context)
        self.logger.error(error_info)

        try:
            from .error_reporter import report_exception
            if context and context.extra and context.extra.get("source") == "error_bus":
                return
            report_exception(
                error,
                module=context.module if context else "unknown",
                function=context.function if context else "unknown",
                context=context.extra if context and context.extra else {},
                source="error_bus",
            )
        except Exception as e:
            self.logger.debug(f"错误总线自身上报失败 (非致命): {e}")

        # 注意：这里不做额外的提醒或退出，让调用者决定如何处理
        # 如果需要，可以在这里添加错误上报、告警逻辑
        # 例如：send_alert_to_slack(error_info)
    def _format_error(self, error: Exception, context: ErrorContext = None) -> str:
        """统一格式化错误信息"""
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
        """处理主线程未捕获的异常"""
        if issubclass(exc_type, KeyboardInterrupt):
            # 让Ctrl+C正常退出
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        
        error = exc_value.with_traceback(exc_traceback)
        self.report_error(error, ErrorContext(module="main", function="main_thread"))
    
    def _handle_thread_exception(self, args: threading.ExceptHookArgs):
        """处理子线程未捕获的异常"""
        self.report_error(
            args.exc_value,
            ErrorContext(
                module="thread",
                function=args.thread.name if args.thread else "unknown_thread",
                extra={"thread_id": args.thread.ident if args.thread else None}
            )
        )
    
    def _handle_asyncio_exception(self, loop: asyncio.AbstractEventLoop, context: Dict):
        """处理asyncio未捕获的异常"""
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
    """获取全局错误总线单例"""
    global _error_bus
    if _error_bus is None:
        with _error_bus_lock:
            if _error_bus is None:
                _error_bus = GlobalErrorBus()
    return _error_bus


# 向后兼容
error_bus = get_error_bus()

# 导出公共接口
__all__ = ["error_bus", "get_error_bus", "ErrorContext"]