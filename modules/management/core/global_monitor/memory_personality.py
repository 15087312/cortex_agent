"""
7. 记忆与长期人格监控

监控向量库读写是否正常
记忆是否丢失、污染、错乱
人格是否稳定、不漂移
"""
from typing import Dict, Any, List, Optional
from .base_recorder import BaseRecorder


class MemoryPersonalityRecorder(BaseRecorder):
    """记忆与长期人格监控记录器"""

    def __init__(self):
        super().__init__("memory_personality", max_records=300)

    def record_operation(
        self,
        operation: str,
        key: str,
        success: bool,
        details: Dict[str, Any] = None
    ) -> None:
        """
        记录记忆操作
        
        Args:
            operation: 操作类型 (read, write, delete)
            key: 记忆键
            success: 是否成功
            details: 详细信息
        """
        super().add_record({
            "operation": operation,
            "key": key,
            "success": success,
            "details": details or {}
        })

    def check_failures(self) -> List[Dict[str, Any]]:
        """
        检查记忆操作失败
        
        Returns:
            失败告警列表
        """
        alerts = []
        latest = self.get_latest()
        
        if latest and not latest.get("success"):
            alerts.append({
                "level": "warning",
                "message": f"记忆操作失败: {latest['operation']} - {latest['key']}",
                "operation": latest["operation"],
                "key": latest["key"]
            })
        
        return alerts

    def get_status(self) -> Dict[str, Any]:
        """获取记忆状态"""
        return {
            "total_records": self.get_count(),
            "recent_records": self.get_records(limit=5),
            "alerts": self.check_failures()
        }
