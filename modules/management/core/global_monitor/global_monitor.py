"""
全局监控器 - 聚合所有监控记录器

AI 的体检仪 + 黑匣子 + 安全护栏
聚合 9 大监控领域，提供统一的监控接口。
"""
import time
from typing import Dict, Any, List, Optional
from datetime import datetime
from utils.logger import setup_logger

from .module_status import ModuleStatusRecorder
from .resource_monitor import ResourceMonitor
from .thinking_process import ThinkingProcessRecorder
from .io_monitor import IOMonitorRecorder
from .task_scheduler import TaskSchedulerRecorder
from .memory_personality import MemoryPersonalityRecorder
from .system_operation import SystemOperationRecorder
from .alert_manager import AlertManager


class GlobalMonitor:
    """
    全局监控器 - 聚合所有监控记录器
    
    核心职责：
    - 聚合 9 大监控领域
    - 提供统一的监控接口
    - 自动收集各模块告警
    - 提供综合状态查询
    """

    def __init__(self):
        """初始化全局监控器"""
        self.logger = setup_logger("global_monitor")
        self._start_time = time.time()
        
        # 8 大监控记录器 (已弃用: 自进化监控由 SecurityMonitor + DifferenceDetector 负责)
        self.module_status = ModuleStatusRecorder()
        self.resource_monitor = ResourceMonitor()
        self.thinking_process = ThinkingProcessRecorder()
        self.io_monitor = IOMonitorRecorder()
        self.task_scheduler = TaskSchedulerRecorder()
        self.memory_personality = MemoryPersonalityRecorder()
        self.system_operation = SystemOperationRecorder()
        
        # 告警管理器
        self.alert_manager = AlertManager()
        
        self.logger.info("全局监控器初始化完成（多文件架构）")

    # ========== 1. 模块运行状态 ==========

    def record_module_status(
        self,
        module_name: str,
        status: str,
        details: Dict[str, Any] = None
    ) -> None:
        """记录模块状态"""
        self.module_status.record_status(module_name, status, details)
        
        # 异常状态自动告警
        if status in ["error", "crashed"]:
            self.alert_manager.add_alert(
                level="critical" if status == "crashed" else "warning",
                message=f"模块 {module_name} 状态异常: {status}",
                details={"module_name": module_name, "status": status}
            )

    def get_module_status(self) -> Dict[str, Any]:
        """获取模块状态"""
        return self.module_status.get_status()

    def check_module_health(self) -> Dict[str, Any]:
        """检查模块健康度"""
        return self.module_status.check_health()

    # ========== 2. 内存与硬件资源 ==========

    def capture_resource_snapshot(self) -> Dict[str, Any]:
        """捕获资源快照"""
        return self.resource_monitor.capture_snapshot()

    def get_resource_status(self) -> Dict[str, Any]:
        """获取资源状态"""
        status = self.resource_monitor.get_status()
        
        # 资源告警自动收集
        for alert in status.get("alerts", []):
            self.alert_manager.add_alert(
                level=alert["level"],
                message=alert["message"],
                details=alert
            )
        
        return status

    # ========== 3. 思考全过程 ==========

    def record_thinking_process(
        self,
        thinking_id: str,
        phase: str,
        details: Dict[str, Any] = None
    ) -> None:
        """记录思考过程"""
        self.thinking_process.record_phase(thinking_id, phase, details)
        
        # 检查思考轮次
        if phase == "expert_execute":
            round_check = self.thinking_process.check_rounds_limit(thinking_id)
            if round_check["exceeded"]:
                self.alert_manager.add_alert(
                    level="warning",
                    message=round_check["message"],
                    details=round_check
                )

    def get_thinking_history(
        self,
        thinking_id: str = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """获取思考历史"""
        return self.thinking_process.get_history(thinking_id, limit)

    # ========== 5. 输入输出全过程 ==========

    def record_io_operation(
        self,
        operation: str,
        input_data: str,
        output_data: str = None,
        latency_ms: float = None
    ) -> None:
        """记录输入输出操作"""
        self.io_monitor.record_operation(operation, input_data, output_data, latency_ms)
        
        # 延迟过高自动告警
        if latency_ms and latency_ms > self.io_monitor.max_latency_ms:
            self.alert_manager.add_alert(
                level="warning",
                message=f"响应延迟过高: {latency_ms:.0f}ms (目标 ≤{self.io_monitor.max_latency_ms}ms)",
                details={"latency_ms": latency_ms}
            )

    # ========== 6. 任务与调度 ==========

    def record_task_event(
        self,
        task_id: str,
        event: str,
        details: Dict[str, Any] = None
    ) -> None:
        """记录任务事件"""
        self.task_scheduler.record_event(task_id, event, details)
        
        # 任务失败自动告警
        if event in ["failed", "timeout"]:
            self.alert_manager.add_alert(
                level="warning",
                message=f"任务异常: {task_id} - {event}",
                details={"task_id": task_id, "event": event}
            )

    # ========== 7. 记忆与长期人格 ==========

    def record_memory_operation(
        self,
        operation: str,
        key: str,
        success: bool,
        details: Dict[str, Any] = None
    ) -> None:
        """记录记忆操作"""
        self.memory_personality.record_operation(operation, key, success, details)
        
        # 操作失败自动告警
        if not success:
            self.alert_manager.add_alert(
                level="warning",
                message=f"记忆操作失败: {operation} - {key}",
                details={"operation": operation, "key": key}
            )

    # ========== 8. 自进化与源码修改 ==========
    # 注：自进化由 SecurityMonitor (检测价值观偏离) + DifferenceDetector (差异评分)
    #    + MultiModelOrchestrator (思考判断+大模型处理) 负责

    def record_evolution_action(
        self,
        action: str,
        target: str,
        changes: Dict[str, Any],
        safety_check: bool
    ) -> None:
        """记录自进化行为（已弃用，由新系统负责）"""
        # 安全检查未通过严重告警
        if not safety_check:
            self.alert_manager.add_alert(
                level="critical",
                message=f"自进化行为未通过安全检查: {action} - {target}",
                details={"action": action, "target": target}
            )
            self.logger.critical("⚠️ 自进化行为未通过安全检查: %s - %s", action, target)

    # ========== 9. 系统级操作 ==========

    def record_system_operation(
        self,
        operation: str,
        target: str,
        success: bool,
        sandbox_test: bool = True
    ) -> None:
        """记录系统操作"""
        self.system_operation.record_operation(operation, target, success, sandbox_test)
        
        # 操作失败自动告警
        if not success:
            self.alert_manager.add_alert(
                level="warning",
                message=f"系统操作失败: {operation} - {target}",
                details={"operation": operation, "target": target}
            )

    # ========== 告警管理 ==========

    def get_alerts(self, level: str = None) -> List[Dict[str, Any]]:
        """获取告警列表"""
        return self.alert_manager.get_alerts(level)

    # ========== 综合状态 ==========

    def get_comprehensive_status(self) -> Dict[str, Any]:
        """
        获取综合状态
        
        Returns:
            完整的系统状态
        """
        # 捕获最新资源快照
        resource_status = self.get_resource_status()
        
        return {
            "timestamp": datetime.now().isoformat(),
            "uptime_seconds": time.time() - self._start_time,
            
            # 1. 模块状态
            "modules": self.get_module_status(),
            "module_health": self.check_module_health(),
            
            # 2. 资源状态
            "resources": {
                "memory_mb": resource_status["current"]["process"]["rss_mb"],
                "memory_percent": resource_status["current"]["system"]["memory_percent"],
                "cpu_percent": resource_status["current"]["system"]["cpu_percent"]
            },
            
            # 3. 思考过程
            "thinking": {
                "total_records": self.thinking_process.get_count(),
                "recent_records": self.get_thinking_history(limit=5)
            },
            
            # 4. 情绪状态
            "emotion": self.get_emotion_status(),
            
            # 5. 输入输出
            "io": self.io_monitor.get_status(),
            
            # 6. 任务调度
            "tasks": self.task_scheduler.get_status(),
            
            # 7. 记忆操作
            "memory": self.memory_personality.get_status(),

            # 8. 系统操作
            "system": self.system_operation.get_status(),
            
            # 告警
            "alerts": {
                **self.alert_manager.get_summary(),
                "recent": self.get_alerts()[-5:]
            }
        }

    def get_summary(self) -> str:
        """获取状态摘要"""
        resource = self.get_resource_status()
        health = self.check_module_health()
        alerts = self.alert_manager.get_summary()
        
        summary = (
            f"全局监控状态 | "
            f"运行时间: {int(time.time() - self._start_time)}s | "
            f"内存: {resource['current']['process']['rss_mb']:.0f}MB ({resource['current']['system']['memory_percent']:.1%}) | "
            f"CPU: {resource['current']['system']['cpu_percent']:.1f}% | "
            f"模块: {health['total_modules']} 个 ({'健康' if health['healthy'] else '异常'}) | "
            f"告警: {alerts['total']} 条"
        )
        
        return summary

    def reset(self) -> None:
        """重置所有监控数据"""
        self.module_status.clear()
        self.resource_monitor.recorder.clear()
        self.thinking_process.clear()
        self.io_monitor.clear()
        self.task_scheduler.clear()
        self.memory_personality.clear()
        self.system_operation.clear()
        self.alert_manager.clear()
        self._start_time = time.time()
        self.logger.info("全局监控数据已重置")
