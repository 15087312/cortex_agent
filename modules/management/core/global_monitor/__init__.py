"""
全局监控模块 - 多文件架构

AI 的体检仪 + 黑匣子 + 安全护栏
覆盖 8 大核心监控领域，轻量级实现。

架构：
- base_recorder.py: 基础记录器
- module_status.py: 1. 模块运行状态
- resource_monitor.py: 2. 内存与硬件资源
- thinking_process.py: 3. 思考全过程
- io_monitor.py: 5. 输入输出全过程
- task_scheduler.py: 6. 任务与调度
- memory_personality.py: 7. 记忆与长期人格
- system_operation.py: 8. 系统级操作
- alert_manager.py: 告警管理器
- global_monitor.py: 全局监控器（聚合所有）

已弃用：
- evolution_monitor.py: 自进化由 SecurityMonitor + DifferenceDetector 负责
- emotion_monitor.py: 情绪由 EmotionExpert prompt 注入处理，不再后处理监控
"""

from modules.management.core.global_monitor.global_monitor import GlobalMonitor
from modules.management.core.global_monitor.module_status import ModuleStatusRecorder
from modules.management.core.global_monitor.resource_monitor import ResourceMonitor
from modules.management.core.global_monitor.thinking_process import ThinkingProcessRecorder
from modules.management.core.global_monitor.io_monitor import IOMonitorRecorder
from modules.management.core.global_monitor.task_scheduler import TaskSchedulerRecorder
from modules.management.core.global_monitor.memory_personality import MemoryPersonalityRecorder
from modules.management.core.global_monitor.system_operation import SystemOperationRecorder
from modules.management.core.global_monitor.alert_manager import AlertManager

__all__ = [
    "GlobalMonitor",
    "ModuleStatusRecorder",
    "ResourceMonitor",
    "ThinkingProcessRecorder",
    "IOMonitorRecorder",
    "TaskSchedulerRecorder",
    "MemoryPersonalityRecorder",
    "SystemOperationRecorder",
    "AlertManager"
]
