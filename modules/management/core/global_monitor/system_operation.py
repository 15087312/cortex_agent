"""
9. 系统级操作监控

监控文件读写、程序启停是否正常
是否越权、误删、误执行
沙盘推演是否安全不影响真机
"""
from typing import Dict, Any, List, Optional
from .base_recorder import BaseRecorder


class SystemOperationRecorder(BaseRecorder):
    """系统级操作监控记录器"""

    def __init__(self):
        super().__init__("system_operation", max_records=200)

    def record_operation(
        self,
        operation: str,
        target: str,
        success: bool,
        sandbox_test: bool = True
    ) -> None:
        """
        记录系统操作
        
        Args:
            operation: 操作类型 (file_read, file_write, program_start, program_stop)
            target: 目标路径/程序
            success: 是否成功
            sandbox_test: 是否在沙盘中测试
        """
        super().add_record({
            "operation": operation,
            "target": target,
            "success": success,
            "sandbox_tested": sandbox_test
        })

    def check_failures(self) -> List[Dict[str, Any]]:
        """
        检查系统操作失败
        
        Returns:
            失败告警列表
        """
        alerts = []
        latest = self.get_latest()
        
        if latest and not latest.get("success"):
            alerts.append({
                "level": "warning",
                "message": f"系统操作失败: {latest['operation']} - {latest['target']}",
                "operation": latest["operation"],
                "target": latest["target"]
            })
        
        return alerts

    def get_status(self) -> Dict[str, Any]:
        """获取系统操作状态"""
        return {
            "total_records": self.get_count(),
            "recent_records": self.get_records(limit=5),
            "alerts": self.check_failures()
        }
