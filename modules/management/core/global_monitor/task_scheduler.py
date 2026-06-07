"""
6. 任务与调度监控

监控任务队列、优先级、执行顺序
任务是否堵塞、丢失、重复
模块间数据传递是否正常
"""
from typing import Dict, Any, List, Optional
from .base_recorder import BaseRecorder


class TaskSchedulerRecorder(BaseRecorder):
    """任务与调度监控记录器"""

    def __init__(self):
        super().__init__("task_scheduler", max_records=500)

    def record_event(
        self,
        task_id: str,
        event: str,
        details: Dict[str, Any] = None
    ) -> None:
        """
        记录任务事件
        
        Args:
            task_id: 任务 ID
            event: 事件类型 (created, started, completed, failed, timeout)
            details: 详细信息
        """
        super().add_record({
            "task_id": task_id,
            "event": event,
            "details": details or {}
        })

    def get_task_history(self, task_id: str) -> List[Dict[str, Any]]:
        """
        获取任务历史
        
        Args:
            task_id: 任务 ID
            
        Returns:
            任务事件列表
        """
        return [r for r in self.records if r.get("task_id") == task_id]

    def check_task_failures(self) -> List[Dict[str, Any]]:
        """
        检查任务失败
        
        Returns:
            失败告警列表
        """
        alerts = []
        latest = self.get_latest()
        
        if latest and latest.get("event") in ["failed", "timeout"]:
            alerts.append({
                "level": "warning",
                "message": f"任务异常: {latest['task_id']} - {latest['event']}",
                "task_id": latest["task_id"],
                "event": latest["event"]
            })
        
        return alerts

    def get_status(self) -> Dict[str, Any]:
        """获取任务调度状态"""
        return {
            "total_records": self.get_count(),
            "recent_records": self.get_records(limit=5),
            "alerts": self.check_task_failures()
        }
