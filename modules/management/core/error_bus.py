"""错误总线兼容壳 — 真身已迁移至 infra/error_bus.py。

保留此文件仅为维持 `modules.management` 包既有公开 API 的引用路径；
新代码请直接 `from infra.error_bus import ...`。
"""
from infra.error_bus import (  # noqa: F401  re-export
    error_bus,
    get_error_bus,
    ErrorContext,
    ErrorReport,
    GlobalErrorBus,
    _setup_global_logger,
)

__all__ = ["error_bus", "get_error_bus", "ErrorContext", "ErrorReport", "GlobalErrorBus", "_setup_global_logger"]
