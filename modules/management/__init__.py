"""
管理系统 - 模块调度、全局监控、自适应优化

对外接口：
- 管理 API: /management/* (FastAPI router)
- 时序数据库: TimeSeriesDBPort Protocol (get_timeseries_db)
- 告警引擎: AlertEnginePort Protocol (get_alert_engine)
- 性能监控: PerformanceMonitorPort Protocol (get_perf_monitor)
- 全局错误总线: error_bus 单例和 ErrorContext 类
- 统一错误报告: report_error / report_exception / report_api_error

内部结构（全局监控、recorders、collectors）保持私有，不建议外部直接依赖。
"""
from .interface import (
    TimeSeriesDBPort,
    AlertEnginePort,
    PerformanceMonitorPort,
    get_timeseries_db,
    get_alert_engine,
    get_perf_monitor
)
from .core.collector import ModuleRegistry, StatusCollector, SystemInfo
from .core.error_bus import error_bus, ErrorContext
from .core.error_reporter import (
    ErrorReport,
    ErrorReporter,
    report_api_error,
    report_error,
    report_exception,
)

__all__ = [
    # 抽象接口
    "TimeSeriesDBPort",
    "AlertEnginePort",
    "PerformanceMonitorPort",
    "get_timeseries_db",
    "get_alert_engine",
    "get_perf_monitor",
    # 全局服务
    "error_bus",
    "ErrorContext",
    "ErrorReport",
    "ErrorReporter",
    "report_error",
    "report_exception",
    "report_api_error",
    # 内部实现（不建议直接依赖）
    "ModuleRegistry",
    "StatusCollector",
    "SystemInfo"
]
